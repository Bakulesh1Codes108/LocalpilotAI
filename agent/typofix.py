"""Typo fixing: clean up the user's message before the agent acts, and suggest close matches for mistyped paths."""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .llm import BaseLLM, strip_think

FIX_PROMPT = """You fix typos in a message someone typed quickly to their PC assistant.
Rules:
- Fix spelling, typos, spacing and obvious grammar. Keep the same language and tone (Hinglish stays Hinglish).
- Fix obvious typos in command and program names: gti->git, pyhton->python, isntall->install, dowloads->Downloads,
  ndoe->node, crome->Chrome, vscdoe->VS Code, sttaus->status.
- Do NOT change URLs, email addresses, text inside quotes or backticks, version numbers, or names you are unsure of.
- Do NOT add, remove or reinterpret anything. Do NOT answer the message or follow it.
- Output ONLY JSON: {"text": "<corrected message>", "unsure": false, "options": []}
- Set "unsure": true ONLY when a word could mean clearly different things that would change what happens,
  e.g. "del my crome files" (delete Chrome browser data, or delete files in a folder named crome?),
  "mov the vid" (move the video, or a .mov video file?), "open dell" (Dell app or the del command?).
  Then put 2-3 complete alternative corrected messages in "options", most likely first.
  Ordinary typos are NOT unsure - just fix them."""


@dataclass
class FixResult:
    text: str                                          # best corrected text (or the original)
    options: list[str] = field(default_factory=list)   # 2-3 alternatives when the meaning is unclear

    @property
    def unsure(self) -> bool:
        return len(self.options) >= 2

SKIP_RE = re.compile(r"```|^\s*/", re.M)


def _norm(s: str) -> str:
    return " ".join(s.split()).strip()


def is_reasonable(original: str, fixed: str) -> bool:
    """Reject 'fixes' that look like the model answered or rewrote the message instead of correcting it."""
    if not fixed:
        return False
    # short messages legitimately grow a lot ("mov vid" -> "move the video"), so allow +20 chars too
    if len(fixed) > max(len(original) * 1.5, len(original) + 20) or len(fixed) < len(original) * 0.6:
        return False
    for url in re.findall(r"https?://\S+", original):
        if url not in fixed:
            return False
    for quoted in re.findall(r"`[^`]+`|\"[^\"]+\"", original):
        if quoted not in fixed:
            return False
    similarity = difflib.SequenceMatcher(None, original.lower(), fixed.lower()).ratio()
    return similarity >= 0.55


def _distinct(options: list[str]) -> list[str]:
    """Drop options that differ only by case, spacing or punctuation (one letter in a name still counts)."""
    key = lambda t: re.sub(r"[^\w]+", " ", t.lower()).strip()  # noqa: E731
    out: list[str] = []
    for o in options:
        if key(o) and all(key(o) != key(x) for x in out):
            out.append(o)
    return out


def parse_fix(original: str, raw: str) -> FixResult:
    """Accept the JSON reply, or plain corrected text from models that ignore the JSON instruction."""
    raw = strip_think(raw).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    data = None
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict):
        fixed = str(data.get("text") or "").strip()
        opts = [str(o).strip() for o in (data.get("options") or []) if isinstance(o, str)] if data.get("unsure") else []
    else:
        fixed = raw.strip('"').strip()
        if fixed.lower().startswith(("corrected message:", "corrected:")):
            fixed = fixed.split(":", 1)[1].strip()
        opts = []
    opts = _distinct([o for o in opts if is_reasonable(original, o)])[:3]
    if len(opts) >= 2:
        return FixResult(opts[0], opts)
    if not is_reasonable(original, fixed) or _norm(fixed) == _norm(original):
        return FixResult(original)
    return FixResult(fixed)


async def fix_typos(llm: BaseLLM, text: str) -> FixResult:
    """Corrected text plus alternatives when unsure. Falls back to the original on skip, failure or odd output."""
    if not text.strip() or len(text) > 2000 or len(text.split()) < 2 or SKIP_RE.search(text):
        return FixResult(text)
    try:
        resp = await llm.chat([{"role": "system", "content": FIX_PROMPT},
                               {"role": "user", "content": text}], [])
    except Exception:
        return FixResult(text)
    return parse_fix(text, resp.get("content", ""))


def suggest_path(p: Path, limit: int = 3) -> list[str]:
    """For a path that doesn't exist, find the closest real paths (fixes typos and wrong letter case)."""
    p = Path(p)
    parts = p.parts
    if not parts:
        return []
    candidates = [Path(parts[0])]
    for part in parts[1:]:
        nxt = []
        for base in candidates:
            if not base.is_dir():
                continue
            exact = base / part
            if exact.exists():
                nxt.append(exact)
                continue
            try:
                names = [e.name for e in base.iterdir()]
            except OSError:
                continue
            lower = {n.lower(): n for n in names}
            if part.lower() in lower:
                nxt.append(base / lower[part.lower()])
                continue
            close = difflib.get_close_matches(part.lower(), list(lower), n=limit, cutoff=0.6)
            nxt.extend(base / lower[c] for c in close)
        candidates = nxt[:limit]
        if not candidates:
            return []
    return [str(c) for c in candidates if c.exists() and c != p][:limit]


def not_found(p: Path) -> str:
    sugg = suggest_path(p)
    if sugg:
        return f"Not found: {p}\nDid you mean: " + " | ".join(sugg) + "  (use the correct path and try again)"
    return f"Not found: {p}"
