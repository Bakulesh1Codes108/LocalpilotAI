"""Local LLM clients: Ollama (native) and any OpenAI-compatible server (LM Studio, llama.cpp, vLLM).

Both return the same normalized shape:
    {"content": str, "tool_calls": [{"id", "name", "arguments"}], "history_message": dict}
If a model has no native tool calling, we fall back to a text protocol: <tool_call>{json}</tool_call>.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import AsyncIterator

import httpx

THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
TOOLCALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)

FALLBACK_INSTRUCTIONS = """
## How to use tools (text protocol)
To call a tool, reply with ONLY this, nothing else:
<tool_call>{{"name": "TOOL_NAME", "arguments": {{...}}}}</tool_call>
You will then receive the tool result. When you are done, reply normally without any <tool_call>.
Available tools (JSON schema):
{tools}
"""


class LLMError(Exception):
    pass


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


def parse_args(a) -> dict:
    if isinstance(a, dict):
        return a
    if isinstance(a, str):
        try:
            v = json.loads(a) if a.strip() else {}
            return v if isinstance(v, dict) else {"value": v}
        except json.JSONDecodeError:
            return {"_unparsed": a}
    return {}


def extract_text_tool_calls(content: str, tool_names: set[str]) -> list[dict]:
    """Recover tool calls a model wrote as text instead of using native tool calling."""
    calls = []
    for m in TOOLCALL_RE.finditer(content or ""):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        name = obj.get("name") or obj.get("tool")
        if name in tool_names:
            calls.append({"id": uuid.uuid4().hex[:8], "name": name,
                          "arguments": parse_args(obj.get("arguments", obj.get("args", {})))})
    if calls:
        return calls
    stripped = FENCE_RE.sub("", (content or "").strip())
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            name = obj.get("name") or obj.get("tool")
            if name in tool_names:
                return [{"id": uuid.uuid4().hex[:8], "name": name,
                         "arguments": parse_args(obj.get("arguments", obj.get("args", obj.get("parameters", {}))))}]
        except json.JSONDecodeError:
            pass
    return []


class BaseLLM:
    provider = "base"

    def __init__(self, base_url: str, model: str, temperature: float = 0.2, num_ctx: int = 8192,
                 timeout: float = 300, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.api_key = api_key
        self.native_tools = True

    def _with_fallback_prompt(self, messages: list[dict], tools: list[dict]) -> list[dict]:
        msgs = [dict(m) for m in messages]
        spec = json.dumps([t["function"] for t in tools], indent=1)
        extra = FALLBACK_INSTRUCTIONS.format(tools=spec)
        if msgs and msgs[0]["role"] == "system":
            msgs[0]["content"] += "\n" + extra
        else:
            msgs.insert(0, {"role": "system", "content": extra})
        return msgs

    def _normalize(self, content: str, raw_calls: list, tools: list[dict]) -> dict:
        content = strip_think(content)
        names = {t["function"]["name"] for t in tools}
        calls = []
        for c in raw_calls or []:
            fn = c.get("function", c)
            calls.append({"id": c.get("id") or uuid.uuid4().hex[:8], "name": fn.get("name"),
                          "arguments": parse_args(fn.get("arguments", {}))})
        if not calls:
            calls = extract_text_tool_calls(content, names)
            if calls:
                content = TOOLCALL_RE.sub("", content).strip()
                if content.startswith("{"):
                    content = ""
        return {"content": content, "tool_calls": calls, "history_message": self.assistant_message(content, calls)}

    def assistant_message(self, content: str, calls: list[dict]) -> dict:
        raise NotImplementedError

    def tool_result_message(self, call: dict, result: str) -> dict:
        raise NotImplementedError

    async def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        raise NotImplementedError

    async def list_models(self) -> list[str]:
        return []

    async def health(self) -> bool:
        try:
            await self.list_models()
            return True
        except Exception:
            return False


class OllamaLLM(BaseLLM):
    provider = "ollama"

    def assistant_message(self, content, calls):
        msg = {"role": "assistant", "content": content}
        if calls and self.native_tools:
            msg["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
        elif calls:
            msg["content"] = (content + "\n" if content else "") + "\n".join(
                f'<tool_call>{json.dumps({"name": c["name"], "arguments": c["arguments"]})}</tool_call>' for c in calls)
        return msg

    def tool_result_message(self, call, result):
        if self.native_tools:
            return {"role": "tool", "content": result, "tool_name": call["name"]}
        return {"role": "user", "content": f"[result of tool {call['name']}]\n{result}"}

    async def chat(self, messages, tools):
        payload = {
            "model": self.model, "stream": False,
            "messages": messages if self.native_tools else self._with_fallback_prompt(messages, tools),
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        if tools and self.native_tools:
            payload["tools"] = tools
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot reach Ollama at {self.base_url}. Is it running? (start it with `ollama serve`)") from e
        except httpx.TimeoutException as e:
            raise LLMError("The model took too long to answer. Try a smaller model or raise llm.timeout.") from e
        if r.status_code == 400 and "does not support tools" in r.text and self.native_tools:
            self.native_tools = False
            return await self.chat(messages, tools)
        if r.status_code == 404 and "not found" in r.text:
            raise LLMError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}")
        if r.status_code >= 400:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
        msg = r.json().get("message", {})
        return self._normalize(msg.get("content", ""), msg.get("tool_calls") or [], tools)

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{self.base_url}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]

    async def pull(self, model: str) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", f"{self.base_url}/api/pull", json={"model": model, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line.strip():
                        yield json.loads(line)


class OpenAICompatLLM(BaseLLM):
    provider = "openai"

    def assistant_message(self, content, calls):
        msg = {"role": "assistant", "content": content or ""}
        if calls and self.native_tools:
            msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in calls]
        elif calls:
            msg["content"] = (content + "\n" if content else "") + "\n".join(
                f'<tool_call>{json.dumps({"name": c["name"], "arguments": c["arguments"]})}</tool_call>' for c in calls)
        return msg

    def tool_result_message(self, call, result):
        if self.native_tools:
            return {"role": "tool", "tool_call_id": call["id"], "content": result}
        return {"role": "user", "content": f"[result of tool {call['name']}]\n{result}"}

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def chat(self, messages, tools):
        url = f"{self.base_url}/v1/chat/completions" if not self.base_url.endswith("/v1") else f"{self.base_url}/chat/completions"
        payload = {"model": self.model, "temperature": self.temperature,
                   "messages": messages if self.native_tools else self._with_fallback_prompt(messages, tools)}
        if tools and self.native_tools:
            payload["tools"] = tools
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(url, json=payload, headers=self._headers())
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot reach the model server at {self.base_url}.") from e
        if r.status_code == 400 and "tool" in r.text.lower() and self.native_tools:
            self.native_tools = False
            return await self.chat(messages, tools)
        if r.status_code >= 400:
            raise LLMError(f"Model server error {r.status_code}: {r.text[:300]}")
        msg = r.json()["choices"][0]["message"]
        return self._normalize(msg.get("content") or "", msg.get("tool_calls") or [], tools)

    async def list_models(self) -> list[str]:
        url = f"{self.base_url}/v1/models" if not self.base_url.endswith("/v1") else f"{self.base_url}/models"
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(url, headers=self._headers())
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]


def make_llm(cfg: dict) -> BaseLLM:
    c = cfg["llm"]
    cls = OpenAICompatLLM if c["provider"] == "openai" else OllamaLLM
    return cls(c["base_url"], c["model"], c["temperature"], c["num_ctx"], c["timeout"], c.get("api_key", ""))
