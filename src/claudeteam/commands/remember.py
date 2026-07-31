"""`claudeteam remember <agent> <kind> <content> [--ref <ref>]`

Append an entry to `<agent>`'s durable memory (`store/memory.py`).
Memory survives tmux pane restart / `/clear`, gets injected into the
agent's identity init prompt on wake so context carries over.

Example:
    claudeteam remember manager task_assigned "implement remember cmd" --ref om_xx
    claudeteam remember worker_cc learning "auth uses bcrypt; salt rounds=12"
    claudeteam remember worker_codex blocker "blocked on missing GH PAT" --ref T-42

Convention for `kind` (not enforced):
    task_assigned / task_completed / learning / blocker / decision / note
"""
from __future__ import annotations

from claudeteam.store import memory, team_memory
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, usage_error,
)


USAGE = (
    "usage: claudeteam remember <agent> <kind> <content> [--ref <ref>] [--team] [--pin]\n"
    "       claudeteam remember <agent> <kind> <content> --team --update <E-n> [--pin|--unpin]\n"
    f"       known kinds: {memory.kinds_summary()}\n"
    "       --team writes shared team experience; --pin keeps it resident in every "
    "agent's prompt\n"
    "       --update <id> edits one in place"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    to_team = pop_bool_flag(rest, "--team")
    update_id = pop_flag(rest, "--update") or ""
    pin = pop_bool_flag(rest, "--pin")
    unpin = pop_bool_flag(rest, "--unpin")
    ref = pop_flag(rest, "--ref") or ""
    if pin and unpin:
        return error_exit("❌ --pin and --unpin are mutually exclusive")
    if (pin or unpin) and not to_team:
        return error_exit("❌ --pin / --unpin only apply to team experience "
                          "(--team)")
    if len(rest) < 3:
        return usage_error(USAGE)
    agent = rest[0]
    kind = rest[1]
    # Join everything after kind into a single content string (so callers
    # can pass an unquoted message without surprising arg-count errors).
    content = " ".join(rest[2:])
    if update_id and not to_team:
        return error_exit("❌ --update only applies with --team "
                          "(per-agent memory is append-only)")
    if to_team:
        # Shared pool: the agent arg records *who* contributed (`by`); the
        # entry lands in state/share. Pinned entries ride every agent's
        # prompt; the rest are pull-only via `recall --team`.
        pin_state = True if pin else (False if unpin else None)
        if update_id:
            rec = team_memory.update(update_id, content=content, kind=kind,
                                     by=agent, pin=pin_state)
            if rec is None:
                return error_exit(
                    f"❌ no team experience entry {update_id!r} — run "
                    f"`claudeteam recall --team` to see ids")
            mark = " 📌" if rec.get("pin") else ""
            print(f"✏️  team experience updated: {update_id} [{kind}] by {agent}{mark}")
            return 0
        record = team_memory.append(content, kind=kind, by=agent, ref=ref,
                                    pin=pin)
        suffix = f" (ref={ref})" if ref else ""
        mark = " 📌置顶" if record.get("pin") else ""
        print(f"🤝 team experience: {record['id']} [{kind}] by {agent}{suffix}{mark}")
        return 0
    record = memory.append(agent, kind, content, ref=ref)
    suffix = f" (ref={ref})" if ref else ""
    print(f"🧠 remembered: {agent}/{kind}{suffix}  [{record['created_at']}]")
    return 0
