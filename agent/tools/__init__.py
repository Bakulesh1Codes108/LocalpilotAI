from .base import Tool, ToolContext, ToolRegistry
from . import files, interact, schedule, system, web


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for mod in (interact, web, files, system, schedule):
        for t in mod.tools():
            reg.add(t)
    return reg


__all__ = ["Tool", "ToolContext", "ToolRegistry", "build_registry"]
