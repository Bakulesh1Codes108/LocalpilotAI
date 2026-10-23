"""Safety layer: classifies every action into a risk tier and decides auto / ask / deny.

Risk tiers
  READ     - looks only (list files, read, search, git status)       -> runs automatically
  WRITE    - changes something reversible (write file, git commit)    -> asks (careful) / auto (trusted)
  HIGH     - risky or hard to undo (delete, install, push, sudo)      -> ALWAYS asks, every time
  BLOCKED  - catastrophic (wipe disk, disable antivirus, exfil keys)  -> never runs
"""
from __future__ import annotations

import re
from enum import IntEnum
from pathlib import Path


class Risk(IntEnum):
    READ = 0
    WRITE = 1
    HIGH = 2
    BLOCKED = 3


MODES = ("readonly", "careful", "trusted")

BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"\bmkfs(\.\w+)?\b", "formats a disk"),
    (r"\bdd\b.*\bof=/dev/(sd|nvme|hd|disk|mmcblk)", "overwrites a raw disk"),
    (r"\bformat(-volume)?\s+[a-z]:", "formats a drive"),
    (r"\bformat-volume\b", "formats a drive"),
    (r"\bdiskpart\b", "edits disk partitions"),
    (r"\bclear-disk\b", "wipes a disk"),
    (r":\(\)\s*\{.*\|.*&\s*\}\s*;", "fork bomb"),
    (r"\bvssadmin\b.*\bdelete\b", "deletes system restore points"),
    (r"\bwbadmin\b.*\bdelete\b", "deletes system backups"),
    (r"\bbcdedit\b", "edits the boot configuration"),
    (r"\bcipher\s+/w", "wipes free disk space"),
    (r"set-mppreference\b.*-disable", "disables antivirus protection"),
    (r"\bshred\b.*/dev/", "shreds a disk"),
    (r">\s*/dev/(sd|nvme|hd)", "overwrites a raw disk"),
    (r"\bchmod\s+(-r|--recursive)\s+[0-7]{3,4}\s+/(\s|$)", "changes permissions of the whole system"),
    (r"\bchown\s+(-r|--recursive)\s+\S+\s+/(\s|$)", "changes ownership of the whole system"),
    (r"(curl|wget|invoke-webrequest|iwr|scp|nc)\b.*(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|login data|\.gnupg)",
     "sends private keys or passwords off the machine"),
    (r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|login data|\.gnupg|\.git-credentials).*\|.*\b(curl|wget|iwr|invoke-webrequest|invoke-restmethod|irm|nc|ncat|scp)\b",
     "sends private keys or passwords off the machine"),
    (r"\bgh\s+repo\s+delete\b", "permanently deletes a GitHub repository"),
    (r"\b(rd|rmdir)\s+/s\s+/q\s+[a-z]:\\?\s*$", "deletes an entire drive"),
    (r"\b(del|erase)\b.*\s/s\b.*\s[a-z]:\\?\*?\s*$", "deletes an entire drive"),
    (r"\bremove-item\b.*-recurse.*\s['\"]?[a-z]:\\?['\"]?\s*(-force)?\s*$", "deletes an entire drive"),
]

HIGH_PATTERNS: list[tuple[str, str]] = [
    (r"\bsudo\b|\brunas\b|\bdoas\b", "runs with administrator rights"),
    (r"\brm\b|\brmdir\b|\bunlink\b|\s-delete\b", "deletes files"),
    (r"(\.ssh/|id_rsa|id_ed25519|\.aws/|\.gnupg|\.git-credentials|login data|\.env\b)", "touches passwords or keys"),
    (r"\bgh\s+repo\s+create\b.*--public|\bgh\s+(release\s+create|pr\s+merge)\b|\bgh\s+repo\s+edit\b.*--visibility",
     "publishes to GitHub"),
    (r"\b(del|erase)\b|\brd\s+/s|\bremove-item\b|\bri\s", "deletes files"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b|restart-computer|stop-computer|\bhalt\b", "shuts down or restarts the PC"),
    (r"\breg(\.exe)?\s+(add|delete|import)\b|set-itemproperty\s+.*hk(lm|cu)", "edits the Windows registry"),
    (r"\bchmod\b|\bchown\b|\bicacls\b|\btakeown\b", "changes file permissions"),
    (r"\bgit\s+push\b", "publishes code to a remote"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|checkout\s+--\s+\.|branch\s+-d\b|rebase\b|filter-branch|stash\s+(drop|clear))",
     "can discard work in git"),
    (r"(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b.*\|\s*(sh|bash|zsh|iex|python|pwsh|powershell)\b",
     "downloads and runs a script"),
    (r"\biex\b|invoke-expression", "runs dynamically built code"),
    (r"set-executionpolicy", "changes the script security policy"),
    (r"\bkill\b|\bpkill\b|\bkillall\b|\btaskkill\b|stop-process", "stops running programs"),
    (r"\bnetsh\b|\biptables\b|\bufw\b|set-netfirewall|new-netfirewall", "changes network/firewall settings"),
    (r"\bschtasks\b|\bcrontab\b|register-scheduledtask", "changes system scheduled tasks"),
    (r"\bsystemctl\b|\bservice\s+\S+\s+(start|stop|restart|disable)|\bsc(\.exe)?\s+(config|delete|stop)\b|set-service|stop-service",
     "changes system services"),
    (r"\b(pip|pip3|npm|pnpm|yarn|winget|choco|scoop|brew|apt|apt-get|dnf|yum|pacman|snap|conda)\b.*\b(install|uninstall|remove|purge|add|upgrade|update)\b",
     "installs or removes software"),
    (r"\bnpm\s+publish\b|\btwine\s+upload\b|\bdocker\s+push\b", "publishes a package"),
    (r"\bdocker\s+(rm|rmi|system\s+prune|volume\s+rm)\b", "deletes containers or data"),
    (r"\bsetx\b|\[environment\]::setenvironmentvariable", "changes permanent environment variables"),
    (r"\bnet\s+(user|localgroup)\b", "changes user accounts"),
    (r"\bssh-keygen\b.*-f|\bgpg\b.*--delete", "touches security keys"),
    (r"\bmv\b.*\s/(etc|usr|bin|boot|system)\b|\bmove\b.*c:\\windows", "moves system files"),
]

# Commands that only look at things. A command is READ only if *every* pipe segment is in here.
READ_COMMANDS = {
    "ls", "dir", "pwd", "cat", "type", "head", "tail", "less", "more", "echo", "whoami", "hostname", "date",
    "uname", "where", "where.exe", "which", "grep", "findstr", "rg", "wc", "tree", "du", "df", "free", "uptime",
    "ps", "tasklist", "ipconfig", "ifconfig", "ping", "nslookup", "systeminfo", "nvidia-smi", "lscpu", "lsblk",
    "get-childitem", "gci", "ls", "get-content", "gc", "get-process", "gps", "get-location", "gl", "get-command",
    "select-string", "sls", "test-path", "get-item", "gi", "measure-object", "sort", "sort-object",
    "select-object", "format-table", "ft", "format-list", "fl", "get-computerinfo", "get-volume", "get-psdrive",
    "get-netipaddress", "env", "printenv", "file", "stat", "sw_vers", "ver", "cd", "set-location", "sl",
    "get-date", "out-string", "uniq", "cut", "awk", "jq", "column", "nproc", "id", "groups", "lsb_release",
}
READ_SUBCOMMANDS = {
    "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "describe", "tag", "blame",
            "shortlog", "ls-files", "config", "--version", "version"},
    "pip": {"list", "show", "freeze", "--version", "check"},
    "pip3": {"list", "show", "freeze", "--version", "check"},
    "npm": {"ls", "list", "view", "-v", "--version", "outdated", "config"},
    "ollama": {"list", "ps", "show", "--version", "-v"},
    "winget": {"list", "search", "show", "--version"},
    "choco": {"list", "search", "info", "--version"},
    "brew": {"list", "search", "info", "--version", "doctor"},
    "conda": {"list", "info", "--version", "env"},
    "docker": {"ps", "images", "version", "info", "--version", "logs", "inspect"},
    "gh": {"repo", "auth", "--version", "pr", "issue"},  # 'gh repo create' caught below
    "systemctl": {"status", "list-units", "is-active"},
}
WRITE_SUBCOMMAND_HINTS = {"create", "delete", "edit", "merge", "close", "add", "set", "remove", "rm"}
VERSION_FLAGS = {"--version", "-v", "-V", "version", "-version"}
CHAINING = (">", "&&", "||", ";", "`", "$(", "<(", "\n")


def _norm(cmd: str) -> str:
    return " ".join(cmd.strip().split()).lower()


def _base(token: str) -> str:
    t = token.strip("'\"").replace("\\", "/").rsplit("/", 1)[-1]
    return t[:-4] if t.endswith(".exe") else t


def _segment_is_read(seg: str) -> bool:
    toks = seg.split()
    if not toks:
        return False
    first = _base(toks[0])
    rest = toks[1:]
    if rest and all(t in VERSION_FLAGS for t in rest):
        return True
    if first == "find":
        return not any(t in ("-delete", "-exec", "-execdir", "-ok", "-fprint") for t in rest)
    if first in READ_SUBCOMMANDS:
        if not rest:
            return first in ("git",)  # bare `git` prints help
        if rest[0] not in READ_SUBCOMMANDS[first]:
            return False
        if first in ("gh", "git") and any(t in WRITE_SUBCOMMAND_HINTS for t in rest[1:3]):
            return False
        if first == "git" and rest[0] == "config" and not any(t in ("--get", "--list", "-l") for t in rest):
            return False
        if first == "git" and rest[0] in ("branch", "tag", "remote") and len(rest) > 1 and not rest[1].startswith("-v") \
                and rest[1] not in ("-a", "-r", "--list", "-l", "show"):
            return False
        return True
    if first in ("python", "python3", "py", "node", "java", "go", "cargo", "rustc", "dotnet", "gcc", "make"):
        return bool(rest) and all(t in VERSION_FLAGS for t in rest)
    return first in READ_COMMANDS


def is_read_only_command(cmd: str) -> bool:
    low = _norm(cmd)
    if any(c in low for c in CHAINING) or re.search(r"(^|\s)&(\s|$)", low):
        return False
    return all(_segment_is_read(seg.strip()) for seg in low.split("|"))


def _rm_wipes_everything(low: str) -> bool:
    m = re.search(r"\brm\s+(.*)", low)
    if not m:
        return False
    toks = m.group(1).split()
    flags = "".join(t for t in toks if t.startswith("-") and not t.startswith("--"))
    recursive = "r" in flags or "--recursive" in toks
    targets = {t.strip("'\"") for t in toks if not t.startswith("-")}
    danger = {"/", "/*", "~", "~/", "~/*", "$home", "$home/", "*", "/home", "/users", "c:", "c:\\", "c:/", ".", "./*", "/root"}
    return recursive and bool(targets & danger)


def classify_shell(cmd: str, extra_blocked=(), extra_high=()) -> tuple[Risk, str]:
    low = _norm(cmd)
    if not low:
        return Risk.READ, "empty command"
    for pat, why in [*BLOCKED_PATTERNS, *[(p, "matches a blocked pattern in config") for p in extra_blocked]]:
        if re.search(pat, low):
            return Risk.BLOCKED, why
    if _rm_wipes_everything(low):
        return Risk.BLOCKED, "recursively deletes your whole drive or home folder"
    for pat, why in [*HIGH_PATTERNS, *[(p, "matches a high-risk pattern in config") for p in extra_high]]:
        if re.search(pat, low):
            return Risk.HIGH, why
    if is_read_only_command(cmd):
        return Risk.READ, "read-only command"
    return Risk.WRITE, "may change files or settings"


SENSITIVE_PARTS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", "id_rsa", "id_ed25519", ".env", "credentials",
                   "login data", "cookies", "keychain", "wallet", ".git-credentials", ".npmrc", ".pypirc", "token")
SYSTEM_DIRS_POSIX = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/lib", "/lib64", "/opt", "/var/lib", "/system",
                     "/library", "/private/etc", "/applications")
SYSTEM_DIRS_WIN = ("c:/windows", "c:/program files", "c:/program files (x86)", "c:/programdata")


def _inside(path: Path, roots: list[str]) -> bool:
    for r in roots:
        try:
            path.relative_to(Path(r))
            return True
        except ValueError:
            continue
    return False


def classify_path(path: Path, write: bool, allowed_dirs: list[str]) -> tuple[Risk, str]:
    p = path.resolve()
    s = str(p).replace("\\", "/").lower()
    name_and_parents = s
    if any(part in name_and_parents for part in SENSITIVE_PARTS):
        return Risk.HIGH, "touches a file that may contain passwords or keys"
    if s.startswith(SYSTEM_DIRS_WIN) or any(s == d or s.startswith(d + "/") for d in SYSTEM_DIRS_POSIX):
        return (Risk.HIGH, "changes a system folder") if write else (Risk.READ, "reads a system folder")
    if not _inside(p, allowed_dirs):
        return (Risk.HIGH, "outside your allowed folders") if write else (Risk.WRITE, "reads outside your allowed folders")
    return (Risk.WRITE, "changes a file in your folders") if write else (Risk.READ, "reads your files")


class Policy:
    """Turns (risk, mode, per-session 'always allow') into auto / ask / deny."""

    def __init__(self, mode: str = "careful"):
        self.set_mode(mode)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode

    def decide(self, risk: Risk, tool: str, session_allowed: set[str] | None = None) -> str:
        if risk == Risk.BLOCKED:
            return "deny"
        if risk == Risk.READ:
            return "auto"
        if self.mode == "readonly":
            return "deny"
        if risk == Risk.HIGH:
            return "ask"  # risky actions always need a fresh yes
        if self.mode == "trusted" or tool in (session_allowed or set()):
            return "auto"
        return "ask"
