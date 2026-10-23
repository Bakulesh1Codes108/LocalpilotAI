from pathlib import Path

import pytest

from agent.core import Agent, Session
from agent.llm import extract_text_tool_calls, strip_think
from agent.loopguard import LoopGuard
from agent.safety import Policy
from agent.tools import ToolContext, build_registry


def make_agent(cfg, llm, mode="careful"):
    return Agent(llm, build_registry(), Policy(mode), ToolContext(cfg=cfg), cfg)


class Recorder:
    def __init__(self, answers=()):
        self.events, self.requests, self.answers = [], [], list(answers)

    async def emit(self, e):
        self.events.append(e)

    async def approve(self, req):
        self.requests.append(req)
        return self.answers.pop(0) if self.answers else "deny"

    def of(self, t):
        return [e for e in self.events if e["type"] == t]


@pytest.mark.asyncio
async def test_read_runs_without_asking(cfg, fake_llm):
    (Path(cfg["agent"]["workspace"]) / "hello.txt").write_text("hi")
    llm = fake_llm([[("list_dir", {"path": "."})], "There is one file: hello.txt"])
    rec = Recorder()
    out = await make_agent(cfg, llm).run(Session("t"), "what's here?", rec.emit, rec.approve)
    assert out == "There is one file: hello.txt"
    assert rec.requests == []
    done = [e for e in rec.of("tool") if e["state"] == "done"]
    assert "hello.txt" in done[0]["output"]


@pytest.mark.asyncio
async def test_write_asks_and_respects_allow(cfg, fake_llm):
    llm = fake_llm([[("write_file", {"path": "note.md", "content": "# hi"})], "Saved."])
    rec = Recorder(["allow"])
    await make_agent(cfg, llm).run(Session("t"), "save a note", rec.emit, rec.approve)
    assert rec.requests[0]["risk"] == "WRITE"
    assert (Path(cfg["agent"]["workspace"]) / "note.md").read_text() == "# hi"


@pytest.mark.asyncio
async def test_deny_prevents_action(cfg, fake_llm):
    llm = fake_llm([[("write_file", {"path": "x.txt", "content": "x"})], "Okay, I won't."])
    rec = Recorder(["deny"])
    s = Session("t")
    await make_agent(cfg, llm).run(s, "write x", rec.emit, rec.approve)
    assert not (Path(cfg["agent"]["workspace"]) / "x.txt").exists()
    assert any("DENIED" in m["content"] for m in s.messages if m["role"] == "tool")


@pytest.mark.asyncio
async def test_always_allow_skips_second_prompt(cfg, fake_llm):
    llm = fake_llm([[("write_file", {"path": "a.txt", "content": "1"})],
                    [("write_file", {"path": "b.txt", "content": "2"})], "done"])
    rec = Recorder(["always"])
    await make_agent(cfg, llm).run(Session("t"), "two files", rec.emit, rec.approve)
    assert len(rec.requests) == 1
    assert (Path(cfg["agent"]["workspace"]) / "b.txt").exists()


@pytest.mark.asyncio
async def test_blocked_never_asks(cfg, fake_llm):
    llm = fake_llm([[("run_shell", {"command": "rm -rf /"})], "I can't do that."])
    rec = Recorder(["allow"])
    await make_agent(cfg, llm, mode="trusted").run(Session("t"), "wipe", rec.emit, rec.approve)
    assert rec.requests == []
    assert rec.of("tool")[0]["state"] == "blocked"


@pytest.mark.asyncio
async def test_high_risk_asks_even_in_trusted(cfg, fake_llm):
    llm = fake_llm([[("install_package", {"manager": "pip", "package": "requests"})], "ok"])
    rec = Recorder(["deny"])
    await make_agent(cfg, llm, mode="trusted").run(Session("t"), "install", rec.emit, rec.approve)
    assert rec.requests[0]["risk"] == "HIGH"


@pytest.mark.asyncio
async def test_duplicate_calls_are_stopped(cfg, fake_llm):
    same = [("list_dir", {"path": "."})]
    llm = fake_llm([same, same, same, same, "never reached"])
    rec = Recorder()
    out = await make_agent(cfg, llm).run(Session("t"), "loop", rec.emit, rec.approve)
    assert "repeating" in out
    assert llm.calls == 3


@pytest.mark.asyncio
async def test_step_limit(cfg, fake_llm):
    cfg["agent"]["max_steps"] = 3
    llm = fake_llm([[("list_dir", {"path": str(i)})] for i in range(10)])
    rec = Recorder()
    out = await make_agent(cfg, llm).run(Session("t"), "go", rec.emit, rec.approve)
    assert "stopped after 3 steps" in out


@pytest.mark.asyncio
async def test_unknown_tool_and_missing_args(cfg, fake_llm):
    llm = fake_llm([[("hack_the_planet", {})], [("read_file", {})], "sorry"])
    s = Session("t")
    rec = Recorder()
    await make_agent(cfg, llm).run(s, "x", rec.emit, rec.approve)
    tool_msgs = [m["content"] for m in s.messages if m["role"] == "tool"]
    assert "Unknown tool" in tool_msgs[0] and "Missing required" in tool_msgs[1]


def test_text_tool_call_fallback():
    calls = extract_text_tool_calls('<tool_call>{"name": "web_search", "arguments": {"query": "x"}}</tool_call>',
                                    {"web_search"})
    assert calls[0]["name"] == "web_search" and calls[0]["arguments"] == {"query": "x"}
    calls = extract_text_tool_calls('```json\n{"tool": "git", "args": {"args": "status"}}\n```', {"git"})
    assert calls[0]["arguments"] == {"args": "status"}
    assert extract_text_tool_calls("just text", {"git"}) == []
    assert strip_think("<think>hmm</think>Answer") == "Answer"


def test_loopguard_resets_after_change():
    g = LoopGuard()
    assert g.check_call("git", {"args": "status"}) == "ok"
    g.record("write_file", {"path": "a"}, True, "ok", changed_state=True)
    assert g.check_call("git", {"args": "status"}) == "ok"


@pytest.mark.asyncio
async def test_agent_asks_when_unsure(cfg, fake_llm):
    llm = fake_llm([[("ask_user", {"question": "Which folder?", "options": ["Downloads", "Documents"]})],
                    "OK, using Documents."])
    rec = Recorder()
    asked = []

    async def ask(req):
        asked.append(req)
        return "Documents"
    s = Session("t")
    out = await make_agent(cfg, llm).run(s, "clean my docs", rec.emit, rec.approve, ask)
    assert asked[0]["options"] == ["Downloads", "Documents"] and asked[0]["type"] == "clarify"
    assert any(m["content"] == "The user answered: Documents" for m in s.messages if m["role"] == "tool")
    assert rec.of("clarify_resolved")[0]["answer"] == "Documents"
    assert out == "OK, using Documents." and not s.pending


@pytest.mark.asyncio
async def test_ask_user_without_a_person(cfg, fake_llm):
    llm = fake_llm([[("ask_user", {"question": "Which?"})], "I'll wait for you."])
    s = Session("t")
    rec = Recorder()
    await make_agent(cfg, llm).run(s, "x", rec.emit, rec.approve)
    assert any("not available" in m["content"] for m in s.messages if m["role"] == "tool")
