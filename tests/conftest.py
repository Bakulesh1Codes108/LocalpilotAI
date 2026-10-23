import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import DEFAULTS, deep_merge  # noqa: E402
from agent.llm import BaseLLM  # noqa: E402


class FakeLLM(BaseLLM):
    """Scripted model: each chat() returns the next item; an item is a string (final answer)
    or a list of (tool_name, args) tool calls."""
    provider = "fake"

    def __init__(self, script):
        super().__init__("http://fake", "fake-model")
        self.script = list(script)
        self.calls = 0

    def assistant_message(self, content, calls):
        return {"role": "assistant", "content": content, "tool_calls": calls}

    def tool_result_message(self, call, result):
        return {"role": "tool", "content": result, "tool_name": call["name"]}

    async def chat(self, messages, tools):
        self.calls += 1
        item = self.script.pop(0) if self.script else "done"
        if isinstance(item, str):
            return self._normalize(item, [], tools)
        raw = [{"function": {"name": n, "arguments": a}} for n, a in item]
        return self._normalize("", raw, tools)

    async def list_models(self):
        return ["fake-model"]


@pytest.fixture
def cfg(tmp_path):
    c = deep_merge(DEFAULTS, {})
    c["agent"]["workspace"] = str(tmp_path / "ws")
    c["agent"]["allowed_dirs"] = [str(tmp_path)]
    c["agent"]["autofix_typos"] = False  # scripted tests enable it explicitly
    (tmp_path / "ws").mkdir()
    return c


@pytest.fixture
def fake_llm():
    return FakeLLM
