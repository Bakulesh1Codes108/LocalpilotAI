"""ask_user: lets the agent stop and ask a clarifying question with tap-to-answer options."""
from __future__ import annotations

from ..safety import Risk
from .base import Tool, ToolContext, always, params, s


async def _handled_by_agent(args: dict, ctx: ToolContext) -> str:  # the agent loop intercepts this tool
    return "ask_user is handled by the agent loop."


def tools() -> list[Tool]:
    return [
        Tool("ask_user",
             "Ask the user a short clarifying question when their request is unclear or could mean different things "
             "(e.g. which folder, which of two programs, delete vs move). Give 2-4 likely answers as options. "
             "Use this instead of guessing, especially before risky actions.",
             params(["question"], question=s("the question, short and clear"),
                    options={"type": "array", "items": {"type": "string"}, "description": "2-4 likely answers"}),
             _handled_by_agent, always(Risk.READ, "asks you a question")),
    ]
