"""Configuration loading: defaults <- config.yaml <- environment variables."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LOCALPILOT_DATA", ROOT / "data"))

DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "ollama",            # ollama | openai  (openai = any OpenAI-compatible server: LM Studio, llama.cpp, vLLM)
        "base_url": "http://localhost:11434",
        "model": "auto",                 # "auto" = pick the best model for this PC
        "api_key": "",                   # only for openai-compatible servers that need one
        "temperature": 0.2,
        "num_ctx": 8192,
        "timeout": 300,
    },
    "agent": {
        "name": "LocalPilot",
        "mode": "careful",               # readonly | careful | trusted
        "max_steps": 15,
        "workspace": "~/LocalPilot-Workspace",
        "allowed_dirs": ["~"],
        "shell_timeout": 180,
        "autofix_typos": True,           # clean up typos in your messages before the agent acts
        "tool_output_limit": 6000,
    },
    "search": {
        "provider": "duckduckgo",        # duckduckgo | searxng
        "searxng_url": "http://localhost:8080",
        "max_results": 6,
    },
    "browser": {"headless": True},
    "laya": {                            # optional second safety layer (pip install laya)
        "enabled": "auto",               # auto = use it if installed | true | false
        "injection_threshold": 0.8,      # flag web/file content as prompt injection above this probability
        "destructive_threshold": 0.85,   # escalate an action to "always ask" above this probability
        "default": "multilingual",       # LAYA checkpoint for unknown language: multilingual | english
    },
    "scheduler": {"approval_timeout": 900},
    "server": {"host": "127.0.0.1", "port": 8484, "open_browser": True},
    "safety": {
        "extra_blocked_patterns": [],
        "extra_high_risk_patterns": [],
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def expand(p: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve()


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path or os.environ.get("LOCALPILOT_CONFIG", ROOT / "config.yaml"))
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            cfg = deep_merge(cfg, yaml.safe_load(f) or {})

    env = os.environ
    if env.get("LOCALPILOT_MODEL"):
        cfg["llm"]["model"] = env["LOCALPILOT_MODEL"]
    if env.get("OLLAMA_HOST"):
        host = env["OLLAMA_HOST"]
        cfg["llm"]["base_url"] = host if host.startswith("http") else f"http://{host}"
    if env.get("LOCALPILOT_MODE"):
        cfg["agent"]["mode"] = env["LOCALPILOT_MODE"]

    cfg["agent"]["workspace"] = str(expand(cfg["agent"]["workspace"]))
    cfg["agent"]["allowed_dirs"] = [str(expand(d)) for d in cfg["agent"]["allowed_dirs"]]
    if cfg["agent"]["workspace"] not in cfg["agent"]["allowed_dirs"]:
        cfg["agent"]["allowed_dirs"].append(cfg["agent"]["workspace"])
    return cfg
