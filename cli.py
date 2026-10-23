"""Terminal chat with the same agent and the same safety rules:  python cli.py"""
import asyncio
import json
import sys

from agent import hardware
from agent.audit import AuditLog
from agent.config import DATA_DIR, load_config
from agent.core import Agent, Session
from agent.llm import make_llm
from agent.safety import Policy
from agent.tools import ToolContext, build_registry
from agent.typofix import fix_typos
from agent.laya_guard import LayaGuard

RISK_COLOR = {"READ": "\033[32m", "WRITE": "\033[33m", "HIGH": "\033[31m", "BLOCKED": "\033[41m"}
R = "\033[0m"


async def main():
    cfg = load_config()
    llm = make_llm(cfg)
    if cfg["llm"]["model"] == "auto":
        rec = hardware.recommend()
        try:
            installed = await llm.list_models()
        except Exception:
            print("Cannot reach Ollama. Start it with `ollama serve`.")
            return
        llm.model = rec["model"] if rec["model"] in installed else (hardware.pick_installed(installed) or rec["model"])
    agent = Agent(llm, build_registry(), Policy(cfg["agent"]["mode"]), ToolContext(cfg=cfg), cfg,
                  AuditLog(DATA_DIR / "audit.log"), LayaGuard(cfg))
    session = Session(id="cli")
    print(f"LocalPilot CLI  | model: {llm.model} | mode: {agent.policy.mode} | type 'exit' to quit, '/mode trusted' to switch, '/fix off' to stop typo fixing\n")

    async def emit(e):
        t = e["type"]
        if t == "assistant":
            print(f"\n\033[1mLocalPilot:\033[0m {e['text']}\n")
        elif t == "note":
            print(f"  · {e['text']}")
        elif t == "tool" and e["state"] in ("done", "failed", "blocked", "skipped"):
            c = RISK_COLOR.get(e["risk"], "")
            print(f"  {c}[{e['risk']}]{R} {e['name']} -> {e['state']}: {e['output'][:300].strip()}")
        elif t == "error":
            print(f"\033[31mError: {e['text']}{R}")

    async def approve(req):
        c = RISK_COLOR.get(req["risk"], "")
        print(f"\n{c}APPROVAL NEEDED [{req['risk']}]{R} {req['name']}: {req['reason']}\n{req['preview']}")
        ans = (await asyncio.to_thread(input, "Allow? [y]es / [a]lways / [N]o: ")).strip().lower()
        return {"y": "allow", "yes": "allow", "a": "always"}.get(ans, "deny")

    async def choose(question, options, fallback=""):
        print(f"\n\033[36m❓ {question}\033[0m")
        for k, o in enumerate(options, 1):
            print(f"   {k}. {o}")
        ans = (await asyncio.to_thread(input, "Pick a number or type your answer: ")).strip()
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1]
        return ans or fallback

    async def ask(req):
        return await choose(req["question"], req.get("options") or [])

    while True:
        try:
            text = (await asyncio.to_thread(input, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in ("exit", "quit"):
            break
        if text.startswith("/mode "):
            agent.policy.set_mode(text.split()[1])
            print(f"mode = {agent.policy.mode}")
            continue
        if text in ("/fix on", "/fix off"):
            cfg["agent"]["autofix_typos"] = text.endswith("on")
            print(f"typo fixing = {'on' if cfg['agent']['autofix_typos'] else 'off'}")
            continue
        if text:
            if cfg["agent"].get("autofix_typos", True):
                res = await fix_typos(llm, text)
                if res.unsure:
                    text = await choose("I'm not sure what you meant. Did you mean…", res.options, text)
                elif res.text != text:
                    print(f"  \033[36m✏️  understood as:\033[0m {res.text}")
                    text = res.text
            await agent.run(session, text, emit, approve, ask)


if __name__ == "__main__":
    if sys.platform == "win32":
        import os
        os.system("")  # enable ANSI colors in Windows terminals
    asyncio.run(main())
