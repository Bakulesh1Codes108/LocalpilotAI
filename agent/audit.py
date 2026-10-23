"""Append-only audit log of every action the agent proposes or takes (JSON lines)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, **entry) -> None:
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        if "output" in entry and isinstance(entry["output"], str):
            entry["output"] = entry["output"][:500]
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out
