"""Anti-loop protection: step limits, duplicate-call detection and repeated-failure cut-off."""
from __future__ import annotations

import json


class LoopGuard:
    def __init__(self, max_steps: int = 15, max_identical: int = 2, max_consecutive_errors: int = 3):
        self.max_steps = max_steps
        self.max_identical = max_identical
        self.max_consecutive_errors = max_consecutive_errors
        self.steps = 0
        self.seen: dict[str, int] = {}
        self.last_result: dict[str, str] = {}
        self.consecutive_errors = 0
        self.empty_replies = 0

    @staticmethod
    def key(name: str, args: dict) -> str:
        return name + ":" + json.dumps(args, sort_keys=True, default=str)

    def next_step(self) -> bool:
        """Call once per model turn. False = step budget used up."""
        self.steps += 1
        return self.steps <= self.max_steps

    def check_call(self, name: str, args: dict) -> str:
        """'ok' to run, 'repeat' to skip and nudge the model, 'stop' to end the run."""
        k = self.key(name, args)
        n = self.seen.get(k, 0) + 1
        self.seen[k] = n
        if n == 1:
            return "ok"
        if n <= self.max_identical:
            return "repeat"
        return "stop"

    def record(self, name: str, args: dict, ok: bool, output: str, changed_state: bool) -> str:
        """Record a result. Returns 'ok' or 'stop' (too many failures in a row)."""
        self.last_result[self.key(name, args)] = output
        if changed_state and ok:
            # The world changed, so re-checking the same thing later is legitimate.
            self.seen = {self.key(name, args): 1}
        self.consecutive_errors = 0 if ok else self.consecutive_errors + 1
        return "stop" if self.consecutive_errors >= self.max_consecutive_errors else "ok"

    def previous(self, name: str, args: dict) -> str:
        return self.last_result.get(self.key(name, args), "(no result recorded)")
