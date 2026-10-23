"""Tools that let the agent create and manage scheduled tasks (like Grok Tasks)."""
from __future__ import annotations

from ..safety import Risk
from .base import Tool, ToolContext, always, params, s


async def schedule_task(args: dict, ctx: ToolContext) -> str:
    if not ctx.scheduler:
        return "Scheduler is not running."
    job = ctx.scheduler.add(args["name"], args["prompt"], args["trigger_type"], args["trigger_value"])
    return f"Scheduled '{job['name']}' (id {job['id']}). Next run: {job.get('next_run') or 'n/a'}"


async def list_schedules(args: dict, ctx: ToolContext) -> str:
    if not ctx.scheduler:
        return "Scheduler is not running."
    jobs = ctx.scheduler.list()
    if not jobs:
        return "No scheduled tasks."
    return "\n".join(f"- [{j['id']}] {j['name']} | {j['trigger_type']}={j['trigger_value']} | "
                     f"{'on' if j['enabled'] else 'paused'} | next: {j.get('next_run') or '-'} | prompt: {j['prompt'][:80]}"
                     for j in jobs)


async def cancel_schedule(args: dict, ctx: ToolContext) -> str:
    if not ctx.scheduler:
        return "Scheduler is not running."
    return "Removed." if ctx.scheduler.remove(args["id"]) else f"No task with id {args['id']}."


def tools() -> list[Tool]:
    return [
        Tool("schedule_task",
             "Schedule a prompt to run automatically later. trigger_type: 'cron' (5-field crontab, e.g. '0 9 * * 1-5' = "
             "weekdays 9:00), 'interval' (minutes, e.g. '60'), or 'once' (ISO time, e.g. '2026-11-01T18:30').",
             params(["name", "prompt", "trigger_type", "trigger_value"], name=s("short name"),
                    prompt=s("what the agent should do each run"), trigger_type=s("cron | interval | once"),
                    trigger_value=s("cron expression, minutes, or ISO datetime")),
             schedule_task, always(Risk.WRITE, "creates a scheduled task"),
             preview=lambda a: f"{a.get('name')}  [{a.get('trigger_type')}: {a.get('trigger_value')}]\n{a.get('prompt')}"),
        Tool("list_schedules", "List scheduled tasks.", params([]), list_schedules, always(Risk.READ, "lists tasks")),
        Tool("cancel_schedule", "Delete a scheduled task by id.", params(["id"], id=s("task id")),
             cancel_schedule, always(Risk.WRITE, "removes a scheduled task")),
    ]
