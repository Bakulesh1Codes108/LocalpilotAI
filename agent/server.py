"""FastAPI server: chat over WebSocket, approvals, scheduler, model management. Local-only by design."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import hardware
from .audit import AuditLog
from .config import DATA_DIR, ROOT, load_config
from .core import Agent, Session
from .llm import BaseLLM, make_llm
from .safety import MODES, Policy
from .scheduler import TaskScheduler
from .tools import ToolContext, build_registry
from .typofix import fix_typos
from .laya_guard import LayaGuard

log = logging.getLogger("localpilot")
WEB = ROOT / "web"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class State:
    def __init__(self, cfg: dict, llm: BaseLLM | None = None, laya: LayaGuard | None = None):
        self.cfg = cfg
        self.llm = llm or make_llm(cfg)
        self.policy = Policy(cfg["agent"]["mode"])
        self.ctx = ToolContext(cfg=cfg)
        self.registry = build_registry()
        self.audit = AuditLog(DATA_DIR / "audit.log")
        self.laya = laya or LayaGuard(cfg)
        self.agent = Agent(self.llm, self.registry, self.policy, self.ctx, cfg, self.audit, self.laya)
        self.sessions: dict[str, Session] = {}
        self.sockets: dict[WebSocket, str] = {}
        self.approvals: dict[str, tuple[asyncio.Future, str, str]] = {}  # id -> (future, session, default)
        self.scheduler = TaskScheduler(DATA_DIR / "schedules.json", self.run_scheduled)
        self.ctx.scheduler = self.scheduler
        self.hw: dict = {}
        self.rec: dict = {}
        self.model_note = ""

    # ---------- model selection ----------
    async def resolve_model(self):
        self.hw = await asyncio.to_thread(hardware.detect)
        self.rec = hardware.recommend(self.hw)
        if self.cfg["llm"]["model"] != "auto":
            self.llm.model = self.cfg["llm"]["model"]
            return
        try:
            installed = await self.llm.list_models()
        except Exception:
            installed = []
        if self.rec["model"] in installed:
            self.llm.model = self.rec["model"]
        elif installed:
            self.llm.model = hardware.pick_installed(installed)
            self.model_note = f"Using installed {self.llm.model}; recommended for your PC: {self.rec['model']}"
        else:
            self.llm.model = self.rec["model"]
            self.model_note = f"Model not downloaded yet: run `ollama pull {self.rec['model']}` or use the System tab"

    async def status(self) -> dict:
        try:
            models, ok = await self.llm.list_models(), True
        except Exception:
            models, ok = [], False
        return {"name": self.cfg["agent"]["name"], "model": self.llm.model, "provider": self.llm.provider,
                "llm_online": ok, "installed_models": models, "hardware": self.hw, "recommended": self.rec,
                "note": self.model_note, "mode": self.policy.mode, "modes": MODES,
                "workspace": self.cfg["agent"]["workspace"], "native_tools": self.llm.native_tools,
                "autofix_typos": bool(self.cfg["agent"].get("autofix_typos", True)),
                "laya": self.laya.status(),
                "tools": self.registry.names()}

    # ---------- sessions & events ----------
    def session(self, sid: str) -> Session:
        if sid not in self.sessions:
            self.sessions[sid] = Session(id=sid)
        return self.sessions[sid]

    async def emit(self, sid: str, event: dict, broadcast: bool = False):
        event = {"ts": time.strftime("%H:%M"), "session": sid, **event}
        s = self.session(sid)
        if event["type"] not in ("status",):
            # replace earlier state of the same tool card instead of duplicating it
            if event["type"] == "tool":
                s.transcript = [e for e in s.transcript if not (e["type"] == "tool" and e.get("id") == event["id"])]
            s.transcript.append(event)
            s.transcript = s.transcript[-400:]
        for ws, wsid in list(self.sockets.items()):
            if broadcast or wsid == sid:
                try:
                    await ws.send_json(event)
                except Exception:
                    self.sockets.pop(ws, None)

    async def broadcast(self, event: dict):
        for ws in list(self.sockets):
            try:
                await ws.send_json(event)
            except Exception:
                self.sockets.pop(ws, None)

    async def wait_for_user(self, sid: str, req: dict, timeout: float | None, broadcast: bool = False,
                            default: str = "deny") -> str:
        """Show a card (approval or question) and wait for the user's reply; `default` on timeout/stop."""
        fut = asyncio.get_running_loop().create_future()
        self.approvals[req["id"]] = (fut, sid, default)
        await self.emit(sid, req, broadcast=broadcast)
        try:
            return await asyncio.wait_for(fut, timeout) if timeout else await fut
        except asyncio.TimeoutError:
            return default
        finally:
            self.approvals.pop(req["id"], None)

    async def ask(self, sid: str, req: dict, timeout: float | None, broadcast: bool = False) -> str:
        return await self.wait_for_user(sid, req, timeout, broadcast, default="deny")

    async def question(self, sid: str, req: dict, timeout: float | None, broadcast: bool = False) -> str:
        return await self.wait_for_user(sid, req, timeout, broadcast, default="")

    def resolve(self, approval_id: str, decision: str):
        item = self.approvals.get(approval_id)
        if item and not item[0].done():
            item[0].set_result(decision if decision in ("allow", "always", "deny") else "deny")

    def answer(self, question_id: str, text: str):
        item = self.approvals.get(question_id)
        if item and not item[0].done():
            item[0].set_result(str(text or "")[:2000])

    async def clarify_typos(self, sid: str, text: str) -> str:
        """Fix typos; if the meaning is unclear, ask 'Did you mean...?' and wait for the user's pick."""
        s = self.session(sid)
        await self.emit(sid, {"type": "status", "text": "fixing typos"})
        res = await fix_typos(self.llm, text)
        if res.unsure:
            cid = uuid.uuid4().hex[:10]
            req = {"type": "clarify", "id": cid, "question": "I'm not sure what you meant. Did you mean…",
                   "options": res.options, "original": text, "source": "typo"}
            s.pending[cid] = req
            try:
                picked = (await self.question(sid, req, None)).strip()
            finally:
                s.pending.pop(cid, None)
            await self.emit(sid, {"type": "clarify_resolved", "id": cid, "answer": picked or text})
            return picked or text
        if res.text != text:
            await self.emit(sid, {"type": "fixed", "text": res.text, "original": text})
        return res.text

    # ---------- running ----------
    async def run_chat(self, sid: str, text: str):
        s = self.session(sid)
        s.busy = True
        await self.emit(sid, {"type": "user", "text": text})
        try:
            if self.cfg["agent"].get("autofix_typos", True):
                text = await self.clarify_typos(sid, text)
            await self.agent.run(s, text, lambda e: self.emit(sid, e), lambda r: self.ask(sid, r, None),
                                 lambda r: self.question(sid, r, None))
        except asyncio.CancelledError:
            await self.emit(sid, {"type": "note", "text": "Stopped."})
        except Exception as e:
            log.exception("agent crashed")
            await self.emit(sid, {"type": "error", "text": f"Internal error: {type(e).__name__}: {e}"})
        finally:
            s.busy = False
            s.task = None
            await self.emit(sid, {"type": "done"})

    def stop(self, sid: str):
        s = self.session(sid)
        for aid, (fut, asid, default) in list(self.approvals.items()):
            if asid == sid and not fut.done():
                fut.set_result(default)
        if s.task and not s.task.done():
            s.task.cancel()

    async def run_scheduled(self, job: dict) -> str:
        sid = f"task-{job['id']}"
        s = self.session(sid)
        s.label = job["name"]
        if s.busy:
            return "Skipped: the previous run is still going."
        s.messages.clear()  # each scheduled run starts fresh
        s.busy = True
        timeout = self.cfg["scheduler"]["approval_timeout"]
        emit = lambda e: self.emit(sid, {**e, "label": job["name"]}, broadcast=True)  # noqa: E731
        await emit({"type": "user", "text": f"⏰ Scheduled task: {job['prompt']}"})
        try:
            return await self.agent.run(s, job["prompt"], emit,
                                        lambda r: self.ask(sid, {**r, "label": job["name"]}, timeout, broadcast=True),
                                        lambda r: self.question(sid, {**r, "label": job["name"]}, timeout, broadcast=True))
        finally:
            s.busy = False
            await emit({"type": "done"})
            await self.broadcast({"type": "schedules", "items": self.scheduler.list()})


# ---------------------------------------------------------------- app factory
class ScheduleIn(BaseModel):
    name: str
    prompt: str
    trigger_type: str
    trigger_value: str


class ModelIn(BaseModel):
    model: str


class ModeIn(BaseModel):
    mode: str


class TextIn(BaseModel):
    text: str


class SettingsIn(BaseModel):
    autofix_typos: bool


def _host_ok(host: str | None, port: int) -> bool:
    if not host:
        return True
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name in LOCAL_HOSTS


def _origin_ok(origin: str | None) -> bool:
    if not origin or origin == "null":
        return origin is None
    return (urlparse(origin).hostname or "") in LOCAL_HOSTS


def create_app(cfg: dict | None = None, llm: BaseLLM | None = None, start_scheduler: bool = True,
               laya: LayaGuard | None = None) -> FastAPI:
    cfg = cfg or load_config()
    state = State(cfg, llm, laya)
    port = cfg["server"]["port"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await state.resolve_model()
        if start_scheduler:
            state.scheduler.start()
        warm = asyncio.create_task(state.laya.warmup())  # loads LAYA in the background if installed
        log.info("LocalPilot ready: model=%s mode=%s", state.llm.model, state.policy.mode)
        yield
        warm.cancel()
        state.scheduler.shutdown()

    app = FastAPI(title="LocalPilot", lifespan=lifespan)
    app.state.lp = state

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Blocks DNS-rebinding and cross-site requests from web pages you visit.
        if not _host_ok(request.headers.get("host"), port):
            return JSONResponse({"error": "bad host"}, status_code=403)
        if request.method != "GET" and request.headers.get("origin") and not _origin_ok(request.headers.get("origin")):
            return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/status")
    async def status():
        return await state.status()

    @app.post("/api/mode")
    async def set_mode(body: ModeIn):
        try:
            state.policy.set_mode(body.mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
        await state.broadcast({"type": "mode", "mode": body.mode})
        return {"mode": body.mode}

    @app.post("/api/settings")
    async def settings(body: SettingsIn):
        state.cfg["agent"]["autofix_typos"] = body.autofix_typos
        return {"autofix_typos": body.autofix_typos}

    @app.post("/api/fix_text")
    async def fix_text(body: TextIn):
        res = await fix_typos(state.llm, body.text)
        return {"text": res.text, "changed": res.text != body.text, "options": res.options}

    @app.post("/api/model")
    async def set_model(body: ModelIn):
        state.llm.model = body.model.strip()
        state.llm.native_tools = True
        state.model_note = ""
        return await state.status()

    @app.post("/api/models/pull")
    async def pull(body: ModelIn):
        if not hasattr(state.llm, "pull"):
            raise HTTPException(400, "Pulling models is only supported with Ollama")

        async def go():
            last = 0.0
            try:
                async for p in state.llm.pull(body.model):
                    if time.time() - last > 0.5 or p.get("status") == "success":
                        last = time.time()
                        await state.broadcast({"type": "pull", "model": body.model, **p})
            except Exception as e:
                await state.broadcast({"type": "pull", "model": body.model, "status": f"error: {e}"})
        asyncio.create_task(go())
        return {"started": body.model}

    @app.get("/api/schedules")
    async def schedules():
        return state.scheduler.list()

    @app.post("/api/schedules")
    async def add_schedule(body: ScheduleIn):
        try:
            job = state.scheduler.add(body.name, body.prompt, body.trigger_type, body.trigger_value)
        except ValueError as e:
            raise HTTPException(400, str(e))
        await state.broadcast({"type": "schedules", "items": state.scheduler.list()})
        return job

    @app.delete("/api/schedules/{job_id}")
    async def del_schedule(job_id: str):
        if not state.scheduler.remove(job_id):
            raise HTTPException(404, "not found")
        await state.broadcast({"type": "schedules", "items": state.scheduler.list()})
        return {"ok": True}

    @app.post("/api/schedules/{job_id}/toggle")
    async def toggle_schedule(job_id: str):
        job = state.scheduler.toggle(job_id)
        if not job:
            raise HTTPException(404, "not found")
        await state.broadcast({"type": "schedules", "items": state.scheduler.list()})
        return job

    @app.post("/api/schedules/{job_id}/run")
    async def run_schedule(job_id: str):
        if job_id not in state.scheduler.jobs:
            raise HTTPException(404, "not found")
        asyncio.create_task(state.scheduler.run_now(job_id))
        return {"started": job_id}

    @app.get("/api/audit")
    async def audit(limit: int = 100):
        return state.audit.tail(min(limit, 500))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        if not _origin_ok(ws.headers.get("origin")) or not _host_ok(ws.headers.get("host"), port):
            await ws.close(code=1008)
            return
        await ws.accept()
        sid = None
        try:
            while True:
                msg = await ws.receive_json()
                t = msg.get("type")
                if t == "hello":
                    sid = (msg.get("session") or uuid.uuid4().hex)[:64]
                    state.sockets[ws] = sid
                    s = state.session(sid)
                    pending = [r for other in state.sessions.values() for r in other.pending.values()
                               if other is s or other.id.startswith("task-")]
                    await ws.send_json({"type": "hello", "session": sid, "busy": s.busy,
                                        "transcript": s.transcript, "pending": pending,
                                        "status": await state.status(), "schedules": state.scheduler.list()})
                elif sid is None:
                    await ws.send_json({"type": "error", "text": "say hello first"})
                elif t == "chat":
                    text = (msg.get("text") or "").strip()
                    s = state.session(sid)
                    if not text:
                        continue
                    if s.busy:
                        await ws.send_json({"type": "error", "text": "I'm still working. Press Stop or wait."})
                        continue
                    s.task = asyncio.create_task(state.run_chat(sid, text))
                elif t == "approval":
                    state.resolve(msg.get("id", ""), msg.get("decision", "deny"))
                elif t == "answer":
                    state.answer(msg.get("id", ""), msg.get("text", ""))
                elif t == "stop":
                    state.stop(sid)
                elif t == "reset":
                    state.stop(sid)
                    state.session(sid).reset()
                    await ws.send_json({"type": "reset"})
        except WebSocketDisconnect:
            pass
        finally:
            state.sockets.pop(ws, None)

    return app
