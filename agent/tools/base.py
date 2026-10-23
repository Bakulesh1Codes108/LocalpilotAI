"""Tool registry: each tool declares a JSON schema, a risk function and an async runner."""
from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..safety import Risk

IS_WINDOWS = platform.system() == "Windows"


@dataclass
class ToolContext:
    cfg: dict
    scheduler: Any = None  # set by the server once the scheduler exists

    @property
    def workspace(self) -> Path:
        p = Path(self.cfg["agent"]["workspace"])
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def allowed_dirs(self) -> list[str]:
        return self.cfg["agent"]["allowed_dirs"]

    def resolve(self, p: str | None) -> Path:
        """Expand ~ and env vars; relative paths are relative to the workspace."""
        if not p:
            return self.workspace
        q = Path(os.path.expandvars(os.path.expanduser(str(p).strip().strip('"').strip("'"))))
        if not q.is_absolute():
            q = self.workspace / q
        return q.resolve()


RiskFn = Callable[[dict, ToolContext], "tuple[Risk, str]"]
RunFn = Callable[[dict, ToolContext], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: RunFn
    risk: RiskFn
    preview: Callable[[dict], str] | None = None
    timeout: float = 300

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    def show(self, args: dict) -> str:
        if self.preview:
            try:
                return self.preview(args)
            except Exception:
                pass
        return json.dumps(args, indent=1, ensure_ascii=False)[:1500] if args else ""

    def missing_args(self, args: dict) -> list[str]:
        return [r for r in self.parameters.get("required", []) if r not in args or args[r] in (None, "")]


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def names(self) -> list[str]:
        return list(self.tools)


def params(required: list[str] | None = None, **props) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def s(desc: str) -> dict:
    return {"type": "string", "description": desc}


def i(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def b(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def always(risk: Risk, why: str) -> RiskFn:
    return lambda args, ctx: (risk, why)
