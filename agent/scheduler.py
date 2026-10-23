"""Built-in scheduler (like Grok Tasks): run an agent prompt on a cron, an interval, or once."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

Runner = Callable[[dict], Awaitable[str]]


def local_tz():
    """The PC's timezone. Falls back to a fixed UTC offset if tz names can't be resolved (e.g. missing tzdata)."""
    try:
        from tzlocal import get_localzone
        return get_localzone()
    except Exception:
        return datetime.now().astimezone().tzinfo


def make_trigger(trigger_type: str, value: str):
    t = (trigger_type or "").lower().strip()
    v = str(value).strip()
    if t == "cron":
        return CronTrigger.from_crontab(v, timezone=local_tz())
    if t == "interval":
        minutes = float(v)
        if minutes < 1:
            raise ValueError("interval must be at least 1 minute")
        return IntervalTrigger(minutes=minutes, timezone=local_tz())
    if t == "once":
        when = datetime.fromisoformat(v)
        if when.tzinfo is None:
            when = when.replace(tzinfo=local_tz())
        if when <= datetime.now(when.tzinfo):
            raise ValueError(f"time {v} is in the past")
        return DateTrigger(run_date=when, timezone=local_tz())
    raise ValueError("trigger_type must be cron, interval or once")


class TaskScheduler:
    def __init__(self, path: Path, runner: Runner):
        self.path = Path(path)
        self.runner = runner
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.sched: AsyncIOScheduler | None = None
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.jobs = {j["id"]: j for j in json.loads(self.path.read_text("utf-8"))}
            except (json.JSONDecodeError, KeyError):
                self.jobs = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(self.jobs.values()), indent=2, default=str), "utf-8")
        tmp.replace(self.path)

    def start(self):
        self.sched = AsyncIOScheduler(timezone=local_tz())
        self.sched.start()
        for job in list(self.jobs.values()):
            if job["enabled"]:
                try:
                    self._register(job)
                except ValueError:
                    job["enabled"] = False  # e.g. a one-time task whose time passed while the PC was off
        self._save()

    def shutdown(self):
        if self.sched and self.sched.running:
            self.sched.shutdown(wait=False)

    def _register(self, job: dict):
        if not self.sched:
            return
        self.sched.add_job(self._fire, make_trigger(job["trigger_type"], job["trigger_value"]), id=job["id"],
                           args=[job["id"]], replace_existing=True, misfire_grace_time=3600, coalesce=True,
                           max_instances=1)

    def _next_run(self, job_id: str) -> str | None:
        j = self.sched.get_job(job_id) if self.sched else None
        return j.next_run_time.strftime("%Y-%m-%d %H:%M") if j and j.next_run_time else None

    async def _fire(self, job_id: str):
        job = self.jobs.get(job_id)
        if not job:
            return
        job["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            result = await self.runner(job)
            job["last_result"] = (result or "")[:2000]
            job["last_status"] = "ok"
        except Exception as e:
            job["last_result"] = f"{type(e).__name__}: {e}"
            job["last_status"] = "error"
        if job["trigger_type"] == "once":
            job["enabled"] = False
        self._save()

    def add(self, name: str, prompt: str, trigger_type: str, trigger_value: str) -> dict:
        make_trigger(trigger_type, trigger_value)  # validate first; raises ValueError with a clear message
        job = {"id": uuid.uuid4().hex[:8], "name": name.strip()[:80], "prompt": prompt.strip(),
               "trigger_type": trigger_type.lower().strip(), "trigger_value": str(trigger_value).strip(),
               "enabled": True, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
               "last_run": None, "last_result": None, "last_status": None}
        with self._lock:
            self.jobs[job["id"]] = job
            self._register(job)
            self._save()
        return self.get(job["id"])

    def get(self, job_id: str) -> dict | None:
        j = self.jobs.get(job_id)
        return {**j, "next_run": self._next_run(job_id) if j["enabled"] else None} if j else None

    def list(self) -> list[dict]:
        return [self.get(i) for i in self.jobs]

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self.jobs:
                return False
            self.jobs.pop(job_id)
            if self.sched and self.sched.get_job(job_id):
                self.sched.remove_job(job_id)
            self._save()
        return True

    def toggle(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        job["enabled"] = not job["enabled"]
        if job["enabled"]:
            self._register(job)
        elif self.sched and self.sched.get_job(job_id):
            self.sched.remove_job(job_id)
        self._save()
        return self.get(job_id)

    async def run_now(self, job_id: str):
        await self._fire(job_id)
