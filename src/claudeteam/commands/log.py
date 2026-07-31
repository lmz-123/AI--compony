"""`claudeteam log <agent> <kind> <content> [ref]`

Append a workspace log entry.  Append-only JSONL; agents leave a trail
that can be tailed for audit / replay.
"""
from __future__ import annotations

from claudeteam.store import local_facts
from claudeteam.util import maybe_print_help, reject_flag_as_agent, usage_error


USAGE = "usage: claudeteam log <agent> <kind> <content> [ref]"


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if len(argv) < 3:
        return usage_error(USAGE)
    agent, kind, content = argv[0], argv[1], argv[2]
    ref = argv[3] if len(argv) > 3 else ""
    if (rc := reject_flag_as_agent(agent, USAGE)) is not None:
        return rc
    local_facts.touch_heartbeat(agent)
    local_id = local_facts.append_log(agent, kind, content, ref=ref)
    print(f"📝 logged: {agent}/{kind}  [{local_id}]")
    return 0
