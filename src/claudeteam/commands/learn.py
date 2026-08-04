"""`claudeteam learn` — distill task traces into reviewable learning drafts."""
from __future__ import annotations

from claudeteam.store import learning, memory, team_memory
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, usage_error,
)


USAGE = (
    "usage:\n"
    "  claudeteam learn task <T-id> [--by <agent>] [--force] [--json]\n"
    "  claudeteam learn list [--status draft|promoted] [--agent <agent>] [--limit N] [--json]\n"
    "  claudeteam learn get <L-id> [--json]\n"
    "  claudeteam learn promote <L-id> [--agent <agent> | --team] [--pin] [--kind <kind>]\n"
    "  claudeteam learn skill-draft <L-id> --skill <name>"
)


def _fmt(d: dict) -> list[str]:
    ts = fmt_time_ms(d.get("created_at", 0))
    return [
        f"{d.get('id')}  [{d.get('status')}]  {d.get('title')}",
        f"  task: {d.get('task_id')}  assignee: {d.get('assignee') or '-'}  category: {d.get('category')}",
        f"  suggested: {d.get('suggested_scope')} / kind={d.get('kind')}",
        f"  lesson: {d.get('lesson')}",
        f"  created: {ts}",
    ]


def _cmd_task(rest: list[str]) -> int:
    by = pop_flag(rest, "--by") or "system"
    force = pop_bool_flag(rest, "--force")
    as_json = pop_bool_flag(rest, "--json")
    if len(rest) < 1:
        return usage_error(USAGE)
    draft = learning.create_from_task(rest[0], by=by, force=force)
    if draft is None:
        return error_exit(f"❌ no such task: {rest[0]}")
    if as_json:
        print_json(draft)
        return 0
    print(f"📝 learning draft {draft['id']} from {draft['task_id']}")
    print(f"   {draft['lesson']}")
    print(f"   promote: claudeteam learn promote {draft['id']} --agent {draft.get('suggested_agent') or 'manager'}")
    return 0


def _cmd_list(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    status = pop_flag(rest, "--status") or ""
    agent = pop_flag(rest, "--agent") or ""
    raw_limit = pop_flag(rest, "--limit") or "30"
    try:
        limit = int(raw_limit)
    except ValueError:
        return error_exit(f"❌ --limit must be an integer (got {raw_limit!r})")
    rows = learning.list_drafts(status=status, agent=agent, limit=limit)
    if as_json:
        print_json(rows)
        return 0
    if not rows:
        print("📝 no learning drafts")
        return 0
    print(f"📝 {len(rows)} learning draft(s)")
    for d in rows:
        for line in _fmt(d):
            print(line)
        print()
    return 0


def _cmd_get(rest: list[str]) -> int:
    as_json = pop_bool_flag(rest, "--json")
    if len(rest) < 1:
        return usage_error(USAGE)
    draft = learning.get(rest[0])
    if not draft:
        return error_exit(f"❌ no such learning draft: {rest[0]}")
    if as_json:
        print_json(draft)
        return 0
    for line in _fmt(draft):
        print(line)
    if draft.get("evidence"):
        print("  evidence:")
        for e in draft["evidence"]:
            print(f"    - {e.get('source')}: {e.get('content')}")
    return 0


def _cmd_promote(rest: list[str]) -> int:
    to_team = pop_bool_flag(rest, "--team")
    pin = pop_bool_flag(rest, "--pin")
    agent = pop_flag(rest, "--agent") or ""
    kind = pop_flag(rest, "--kind") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    if to_team and agent:
        return error_exit("❌ choose either --team or --agent, not both")
    draft = learning.get(rest[0])
    if not draft:
        return error_exit(f"❌ no such learning draft: {rest[0]}")
    kind = kind or draft.get("kind") or "learning"
    content = draft.get("lesson", "")
    ref = draft.get("task_id", "")
    if to_team:
        rec = team_memory.append(content, kind=kind, by="learn", ref=ref, pin=pin)
        learning.mark_promoted(draft["id"], f"team:{rec['id']}")
        print(f"🤝 promoted {draft['id']} → team {rec['id']}")
        return 0
    target = agent or draft.get("suggested_agent") or draft.get("assignee") or "manager"
    rec = memory.append(target, kind, content, ref=ref)
    learning.mark_promoted(draft["id"], f"agent:{target}:{rec['created_at']}")
    print(f"🧠 promoted {draft['id']} → {target}/{kind}")
    return 0


def _cmd_skill_draft(rest: list[str]) -> int:
    skill_name = pop_flag(rest, "--skill") or ""
    if len(rest) < 1 or not skill_name:
        return usage_error(USAGE)
    path = learning.write_skill_draft(rest[0], skill_name)
    if path is None:
        return error_exit(f"❌ no such learning draft: {rest[0]}")
    print(f"🧩 skill draft written: {path}")
    return 0


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if not rest:
        return usage_error(USAGE)
    cmd = rest.pop(0)
    if cmd == "task":
        return _cmd_task(rest)
    if cmd == "list":
        return _cmd_list(rest)
    if cmd == "get":
        return _cmd_get(rest)
    if cmd == "promote":
        return _cmd_promote(rest)
    if cmd == "skill-draft":
        return _cmd_skill_draft(rest)
    return error_exit(f"unknown learn subcommand: {cmd}\n{USAGE}")
