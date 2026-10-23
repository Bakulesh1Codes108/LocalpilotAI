"""File tools. Deletion never destroys data: files go to the Recycle Bin / Trash (or a .trash folder)."""
from __future__ import annotations

import asyncio
import fnmatch
import os
import shutil
import time
from pathlib import Path

from ..safety import Risk, classify_path
from ..typofix import not_found
from .base import Tool, ToolContext, b, i, params, s

TEXT_LIMIT = 20000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", "Library", ".cache", "$Recycle.Bin"}


def path_risk(key: str, write: bool):
    def fn(args: dict, ctx: ToolContext):
        return classify_path(ctx.resolve(args.get(key)), write, ctx.allowed_dirs)
    return fn


def two_path_risk(args: dict, ctx: ToolContext):
    r1 = classify_path(ctx.resolve(args.get("src")), True, ctx.allowed_dirs)
    r2 = classify_path(ctx.resolve(args.get("dst")), True, ctx.allowed_dirs)
    return max(r1, r2, key=lambda r: r[0])


def delete_risk(args: dict, ctx: ToolContext):
    p = ctx.resolve(args.get("path"))
    protected = {Path.home().resolve(), ctx.workspace.resolve(), *(Path(d).resolve() for d in ctx.allowed_dirs)}
    if p == Path(p.anchor) or p in protected:
        return Risk.BLOCKED, "refuses to delete a drive root, your home folder or an allowed root folder"
    risk, why = classify_path(p, True, ctx.allowed_dirs)
    return Risk.HIGH, f"deletes {p.name} ({why})"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


async def list_dir(args, ctx):
    p = ctx.resolve(args.get("path"))
    if not p.exists():
        return not_found(p)
    if p.is_file():
        return f"{p} is a file ({_human(p.stat().st_size)})"
    rows = []
    for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:300]:
        try:
            rows.append(f"[DIR]  {e.name}/" if e.is_dir() else f"       {e.name}  ({_human(e.stat().st_size)})")
        except OSError:
            rows.append(f"  ?    {e.name}")
    return f"{p}\n" + ("\n".join(rows) or "(empty)")


async def read_file(args, ctx):
    p = ctx.resolve(args["path"])
    if not p.exists():
        return not_found(p)
    if not p.is_file():
        return f"Not a file (it's a folder): {p}"
    data = p.read_bytes()[: TEXT_LIMIT * 2]
    if b"\x00" in data[:4096]:
        return f"{p} looks like a binary file ({_human(p.stat().st_size)}); not shown."
    text = data.decode("utf-8", errors="replace")
    start = int(args.get("start_line") or 1)
    lines = text.splitlines()[start - 1:]
    body = "\n".join(lines)[:TEXT_LIMIT]
    return f"{p} ({len(lines)} lines from line {start})\n{body}"


async def write_file(args, ctx):
    p = ctx.resolve(args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.get("append") else "w"
    backup = ""
    if p.exists() and mode == "w":
        bak = ctx.workspace / ".backups" / f"{time.strftime('%Y%m%d_%H%M%S')}_{p.name}"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, bak)
        backup = f" (previous version backed up to {bak})"
    with open(p, mode, encoding="utf-8", newline="") as f:
        f.write(args.get("content", ""))
    return f"{'Appended to' if mode == 'a' else 'Wrote'} {p} ({len(args.get('content', ''))} chars){backup}"


async def make_dir(args, ctx):
    p = ctx.resolve(args["path"])
    p.mkdir(parents=True, exist_ok=True)
    return f"Folder ready: {p}"


async def move_path(args, ctx):
    src, dst = ctx.resolve(args["src"]), ctx.resolve(args["dst"])
    if not src.exists():
        return not_found(src)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"Refusing to overwrite existing {dst}. Choose another name."
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved {src} -> {dst}"


async def copy_path(args, ctx):
    src, dst = ctx.resolve(args["src"]), ctx.resolve(args["dst"])
    if not src.exists():
        return not_found(src)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"Refusing to overwrite existing {dst}. Choose another name."
    dst.parent.mkdir(parents=True, exist_ok=True)
    (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    return f"Copied {src} -> {dst}"


async def delete_path(args, ctx):
    p = ctx.resolve(args["path"])
    if not p.exists():
        return not_found(p)
    try:
        from send2trash import send2trash
        await asyncio.to_thread(send2trash, str(p))
        return f"Moved to Recycle Bin/Trash: {p} (restore it from there if needed)"
    except Exception:
        dest = ctx.workspace / ".trash" / f"{time.strftime('%Y%m%d_%H%M%S')}_{p.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
        return f"Moved to {dest} (LocalPilot trash; nothing was permanently deleted)"


async def find_files(args, ctx):
    root = ctx.resolve(args.get("root"))
    pattern = args.get("pattern") or "*"
    limit = int(args.get("max_results") or 100)
    contains = (args.get("contains") or "").lower()

    def walk():
        hits = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if fnmatch.fnmatch(fn.lower(), pattern.lower()):
                    full = Path(dirpath) / fn
                    if contains:
                        try:
                            if full.stat().st_size > 2_000_000 or contains not in full.read_text("utf-8", "ignore").lower():
                                continue
                        except OSError:
                            continue
                    hits.append(str(full))
                    if len(hits) >= limit:
                        return hits
        return hits

    if not root.is_dir():
        return not_found(root)
    hits = await asyncio.to_thread(walk)
    return f"{len(hits)} match(es) for '{pattern}' under {root}:\n" + "\n".join(hits) if hits else f"No matches for '{pattern}' under {root}"


def tools() -> list[Tool]:
    W = lambda a: f"{a.get('path')}\n---\n{(a.get('content') or '')[:800]}"  # noqa: E731
    return [
        Tool("list_dir", "List files and folders. Relative paths are inside the workspace. Use ~ for home.",
             params([], path=s("folder path")), list_dir, path_risk("path", False)),
        Tool("read_file", "Read a text file.", params(["path"], path=s("file path"), start_line=i("first line to show")),
             read_file, path_risk("path", False)),
        Tool("write_file", "Create or overwrite a text file (old version is backed up automatically).",
             params(["path", "content"], path=s("file path"), content=s("full file content"), append=b("append instead")),
             write_file, path_risk("path", True), preview=W),
        Tool("make_dir", "Create a folder (and parents).", params(["path"], path=s("folder path")),
             make_dir, path_risk("path", True), preview=lambda a: f"mkdir {a.get('path')}"),
        Tool("move_path", "Move or rename a file/folder. Never overwrites.",
             params(["src", "dst"], src=s("source"), dst=s("destination")), move_path, two_path_risk,
             preview=lambda a: f"{a.get('src')}  ->  {a.get('dst')}"),
        Tool("copy_path", "Copy a file/folder. Never overwrites.",
             params(["src", "dst"], src=s("source"), dst=s("destination")), copy_path, two_path_risk,
             preview=lambda a: f"{a.get('src')}  ->  {a.get('dst')}"),
        Tool("delete_path", "Delete a file/folder by moving it to the Recycle Bin/Trash (recoverable).",
             params(["path"], path=s("path")), delete_path, delete_risk,
             preview=lambda a: f"Recycle: {a.get('path')}"),
        Tool("find_files", "Find files by name pattern (e.g. *.pdf) and optionally text they contain.",
             params([], root=s("folder to search, default workspace"), pattern=s("glob like *.py"),
                    contains=s("text the file must contain"), max_results=i("limit, default 100")),
             find_files, path_risk("root", False)),
    ]
