"""Shell, git, package installs and system info."""
from __future__ import annotations

import asyncio
import platform
import re
import shlex
import shutil
import subprocess
import sys

import psutil

from .. import hardware
from ..safety import Risk, classify_path, classify_shell
from .base import IS_WINDOWS, Tool, ToolContext, always, params, s


def _shell_argv(command: str) -> list[str]:
    if IS_WINDOWS:
        exe = shutil.which("pwsh") or "powershell"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
    return [shutil.which("bash") or "/bin/sh", "-c", command]


def _run(argv: list[str], cwd: str, timeout: float) -> str:
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"exit code: TIMEOUT after {timeout:.0f}s (command was stopped)"
    except FileNotFoundError as e:
        return f"exit code: 127\nstderr:\n{e}"
    out = f"exit code: {p.returncode}\n"
    if p.stdout.strip():
        out += f"stdout:\n{p.stdout.strip()}\n"
    if p.stderr.strip():
        out += f"stderr:\n{p.stderr.strip()}\n"
    if not p.stdout.strip() and not p.stderr.strip():
        out += "(no output)"
    return out


def shell_risk(args: dict, ctx: ToolContext):
    sc = ctx.cfg.get("safety", {})
    risk, why = classify_shell(args.get("command", ""), sc.get("extra_blocked_patterns", []),
                               sc.get("extra_high_risk_patterns", []))
    if risk < Risk.HIGH and args.get("cwd"):
        prisk, pwhy = classify_path(ctx.resolve(args["cwd"]), risk >= Risk.WRITE, ctx.allowed_dirs)
        if prisk > risk:
            return prisk, pwhy
    return risk, why


async def run_shell(args: dict, ctx: ToolContext) -> str:
    cwd = str(ctx.resolve(args.get("cwd")))
    timeout = float(args.get("timeout") or ctx.cfg["agent"]["shell_timeout"])
    return await asyncio.to_thread(_run, _shell_argv(args["command"]), cwd, timeout)


GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "shortlog",
            "describe", "tag", "config", "fetch", "--version", "version"}


def git_risk(args: dict, ctx: ToolContext):
    a = (args.get("args") or "").strip()
    risk, why = classify_shell("git " + a)
    if risk >= Risk.HIGH:
        return risk, why
    first = a.split()[0] if a else "status"
    if first in GIT_READ and risk == Risk.READ:
        return Risk.READ, "read-only git command"
    if first == "fetch":
        return Risk.READ, "downloads remote refs (no local changes)"
    return Risk.WRITE, f"git {first} changes the repository"


async def git(args: dict, ctx: ToolContext) -> str:
    if not shutil.which("git"):
        return "git is not installed. Ask me to install it (winget install Git.Git / brew install git / apt install git)."
    argv = ["git", *shlex.split(args.get("args") or "status", posix=not IS_WINDOWS)]
    cwd = str(ctx.resolve(args.get("repo")))
    return await asyncio.to_thread(_run, argv, cwd, ctx.cfg["agent"]["shell_timeout"])


PKG_RE = re.compile(r"^[A-Za-z0-9._@/+=<>~:\-\[\],]+$")
MANAGERS = {
    "pip": lambda p: [sys.executable, "-m", "pip", "install", p],
    "npm": lambda p: ["npm", "install", "-g", p],
    "winget": lambda p: ["winget", "install", "--id", p, "-e", "--accept-source-agreements", "--accept-package-agreements"],
    "choco": lambda p: ["choco", "install", p, "-y"],
    "scoop": lambda p: ["scoop", "install", p],
    "brew": lambda p: ["brew", "install", p],
    "apt": lambda p: ["sudo", "-n", "apt-get", "install", "-y", p],
    "ollama": lambda p: ["ollama", "pull", p],
}


async def install_package(args: dict, ctx: ToolContext) -> str:
    mgr, pkg = args["manager"].lower(), args["package"].strip()
    if mgr not in MANAGERS:
        return f"Unknown manager '{mgr}'. Use one of: {', '.join(MANAGERS)}"
    if not PKG_RE.match(pkg):
        return f"Refusing suspicious package name: {pkg!r}"
    argv = MANAGERS[mgr](pkg)
    if not shutil.which(argv[0]) and argv[0] != sys.executable:
        return f"'{argv[0]}' is not installed on this PC."
    return await asyncio.to_thread(_run, argv, str(ctx.workspace), 1800)


def _tool_version(cmd: str) -> str:
    if not shutil.which(cmd):
        return "not installed"
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0][:80]
    except Exception:
        return "installed"


async def system_info(args: dict, ctx: ToolContext) -> str:
    def collect():
        hw = hardware.detect()
        rec = hardware.recommend(hw)
        disk_root = "C:\\" if IS_WINDOWS else "/"
        du = psutil.disk_usage(disk_root)
        lines = [
            f"OS: {hw['os']} ({hw['machine']}), Python {platform.python_version()}",
            f"CPU: {hw['cpu_cores']} cores / {hw['cpu_threads']} threads, load {psutil.cpu_percent(interval=0.5)}%",
            f"RAM: {hw['ram_gb']} GB total, {hw['ram_free_gb']} GB free",
            f"Disk {disk_root}: {du.free / 1024**3:.0f} GB free of {du.total / 1024**3:.0f} GB",
            *[f"GPU: {g['name']} ({g['vram_gb']} GB VRAM)" for g in hw["gpus"]],
            f"Recommended local model: {rec['model']} ({rec['why']})",
            "Dev tools: " + ", ".join(f"{t}={_tool_version(t)}" for t in ("git", "node", "python", "docker", "ollama", "gh", "code")),
        ]
        return "\n".join(lines)
    return await asyncio.to_thread(collect)


def tools() -> list[Tool]:
    return [
        Tool("run_shell",
             f"Run a {'PowerShell' if IS_WINDOWS else 'bash'} command on the user's PC and return its output. "
             "Read-only commands run immediately; anything that changes the system asks the user first.",
             params(["command"], command=s("the command"), cwd=s("working folder (default workspace)"),
                    timeout={"type": "integer", "description": "seconds"}),
             run_shell, shell_risk, preview=lambda a: f"{a.get('command')}" + (f"\n(in {a['cwd']})" if a.get("cwd") else "")),
        Tool("git", "Run a git command in a repository, e.g. args='status', 'clone https://github.com/x/y', "
                    "'add -A', 'commit -m \"msg\"', 'push'.",
             params(["args"], args=s("git arguments"), repo=s("repository folder (default workspace)")),
             git, git_risk, preview=lambda a: f"git {a.get('args')}" + (f"\n(in {a['repo']})" if a.get("repo") else "")),
        Tool("install_package", "Install software or a library. Managers: " + ", ".join(MANAGERS) +
             " (winget ids like 'Git.Git', 'OpenJS.NodeJS.LTS', 'Microsoft.VisualStudioCode').",
             params(["manager", "package"], manager=s("package manager"), package=s("package name/id")),
             install_package, always(Risk.HIGH, "installs software on your PC"),
             preview=lambda a: " ".join(MANAGERS.get(a.get("manager", "").lower(), lambda p: [a.get("manager"), "install", p])(a.get("package", "")))),
        Tool("system_info", "Show this PC's OS, CPU, RAM, disk, GPU, installed dev tools and the best local model for it.",
             params([]), system_info, always(Risk.READ, "reads system info")),
    ]
