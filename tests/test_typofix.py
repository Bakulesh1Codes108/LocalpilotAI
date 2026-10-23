import pytest

import json

from agent.typofix import fix_typos, is_reasonable, parse_fix, suggest_path


@pytest.mark.asyncio
async def test_fixes_typos(fake_llm):
    llm = fake_llm(["check git status in my Downloads folder"])
    assert (await fix_typos(llm, "chek gti sttaus in my dowloads foldr")).text == "check git status in my Downloads folder"


@pytest.mark.asyncio
async def test_keeps_original_when_model_answers_instead(fake_llm):
    llm = fake_llm(["Sure! Here is a long explanation of how git works and why you should use it every day."])
    assert (await fix_typos(llm, "gti sttaus")).text == "gti sttaus"


@pytest.mark.asyncio
async def test_skips_code_short_and_slash(fake_llm):
    llm = fake_llm(["SHOULD NOT BE USED"] * 3)
    for text in ["hi", "/mode trusted", "run this ```rm x```"]:
        assert (await fix_typos(llm, text)).text == text
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_model_offline_returns_original():
    class Down:
        async def chat(self, *a):
            raise RuntimeError("offline")
    assert (await fix_typos(Down(), "instal pyhton plz")).text == "instal pyhton plz"


def test_urls_and_quotes_must_survive():
    assert not is_reasonable('open https://github.com/abc/repo', 'open https://github.com/abc/rep')
    assert not is_reasonable('commit with "fixd bug"', 'commit with "fixed bug"')
    assert is_reasonable("isntall nodejs", "install nodejs")


def test_suggest_path(tmp_path):
    (tmp_path / "Downloads" / "Invoices").mkdir(parents=True)
    assert suggest_path(tmp_path / "downloads") == [str(tmp_path / "Downloads")]
    assert suggest_path(tmp_path / "Dowloads" / "invoces") == [str(tmp_path / "Downloads" / "Invoices")]
    assert suggest_path(tmp_path / "zzzz") == []


@pytest.mark.asyncio
async def test_file_tool_says_did_you_mean(cfg):
    from pathlib import Path
    from agent.tools import ToolContext, build_registry
    ws = Path(cfg["agent"]["workspace"])
    (ws / "Projects").mkdir()
    out = await build_registry().get("list_dir").run({"path": "projcts"}, ToolContext(cfg=cfg))
    assert "Did you mean" in out and "Projects" in out


def test_json_reply_and_unsure_options():
    orig = "del my crome fiels"
    sure = parse_fix(orig, json.dumps({"text": "del my Chrome files", "unsure": False, "options": []}))
    assert sure.text == "del my Chrome files" and not sure.unsure
    unsure = parse_fix(orig, json.dumps({"text": "x", "unsure": True,
                                         "options": ["delete my Chrome files", "delete my crome files"]}))
    assert unsure.unsure and unsure.options == ["delete my Chrome files", "delete my crome files"]
    # options that are near-identical or wild rewrites are dropped -> no question asked
    weak = parse_fix(orig, json.dumps({"text": "del my Chrome files", "unsure": True,
                                       "options": ["del my Chrome files", "del my Chrome files.",
                                                   "Here is how to clear browser data in Chrome step by step"]}))
    assert not weak.unsure and weak.text == "del my Chrome files"
