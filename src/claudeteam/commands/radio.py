"""`claudeteam radio` — passive current-task updates.

Use this instead of sweeping `inbox` when a worker is already busy on a T-n
and receives same-task supplements.
"""
from __future__ import annotations

from claudeteam.store import radio
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, reject_flag_as_agent, usage_error,
)


USAGE = (
    "usage:\n"
    "  claudeteam radio updates <agent> [--task T-n] [--json] [--all]\n"
    "  claudeteam radio ack <agent> [--task T-n | --local-id msg_x]\n"
    "  claudeteam radio seal <agent> --task T-n"
)


def _cmd_updates(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    include_all = pop_bool_flag(rest, "--all")
    task_id = pop_flag(rest, "--task") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]
    if (rc := reject_flag_as_agent(agent, USAGE)) is not None:
        return rc
    rows = radio.list_updates(agent, task_id=task_id, unacked_only=not include_all,
                              include_archived=include_all, limit=20)
    if as_json:
        print_json({"agent": agent, "task_id": task_id, "updates": rows})
        return 0
    label = f"{agent}/{task_id}" if task_id else agent
    if not rows:
        print(f"📻 {label}: no radio updates")
        return 0
    print(f"📻 {label}: {len(rows)} update(s)")
    for row in rows[-5:]:
        ts = fmt_time_ms(row.get("created_at", 0))
        status = "✓" if row.get("acked") else "未读"
        print(f"── [{ts}] {row.get('from', '?')} → {agent}  {row.get('task_id', '')}  {status}  {row.get('local_id', '')}")
        print(f"   {row.get('content', '')}")
    return 0


def _cmd_ack(rest: list[str]) -> int:
    task_id = pop_flag(rest, "--task") or ""
    local_id = pop_flag(rest, "--local-id") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    if not task_id and not local_id:
        return error_exit("❌ radio ack requires --task or --local-id")
    agent = rest[0]
    if (rc := reject_flag_as_agent(agent, USAGE)) is not None:
        return rc
    changed = radio.ack(agent, task_id=task_id, local_id=local_id)
    print(f"✅ radio acked {changed} update(s)")
    return 0


def _cmd_seal(rest: list[str]) -> int:
    task_id = pop_flag(rest, "--task") or ""
    if len(rest) < 1 or not task_id:
        return usage_error(USAGE)
    agent = rest[0]
    if (rc := reject_flag_as_agent(agent, USAGE)) is not None:
        return rc
    ok = radio.seal_task(agent, task_id)
    print(f"{'✅ sealed' if ok else 'ℹ️ no radio thread'} {agent}/{task_id}")
    return 0


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if not rest:
        return usage_error(USAGE)
    cmd = rest.pop(0)
    if cmd == "updates":
        return _cmd_updates(rest)
    if cmd == "ack":
        return _cmd_ack(rest)
    if cmd == "seal":
        return _cmd_seal(rest)
    return error_exit(f"unknown radio subcommand: {cmd}\n{USAGE}")
