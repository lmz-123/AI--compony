"""`claudeteam team [--json]`

Walk the current roster and show each agent's latest status. The roster is the
only thing enumerated — status.json keeps a row for every agent that ever
reported, but we look up by name rather than listing it, so a de-rostered /
renamed agent can't surface as a phantom. Default:
human-readable single-line per agent:
  `name  status  task  [⛔ blocker]  (Nm ago)  ♥ Nm ago`.

With `--json`, dump a list of status records (each with name, status,
task, blocker, updated_at_ms, heartbeat_ms) so CI / peer agents can
parse machine-readable state.
"""
from __future__ import annotations

from claudeteam.runtime import config
from claudeteam.store import local_facts
from claudeteam.util import ago_ms, maybe_print_help, pop_bool_flag, print_json


USAGE = "usage: claudeteam team [--json]"


def _emit_text(rows: list[dict], heartbeats: dict[str, int]) -> None:
    if not rows:
        print("👥 no agents have reported status yet")
        return
    name_w = max(len(r["agent"]) for r in rows)
    for r in rows:
        line = (
            f"{r['agent'].ljust(name_w)}  "
            f"{r['status']}  {r['task']}"
        )
        if r.get("blocker"):
            line += f"  ⛔ {r['blocker']}"
        line += f"  ({ago_ms(r.get('updated_at', 0))})"
        hb = heartbeats.get(r["agent"])
        if hb:
            line += f"  ♥ {ago_ms(hb)}"
        print(line)


def _emit_json(rows: list[dict], heartbeats: dict[str, int]) -> None:
    """Machine-readable shape: a flat list of status records, one per
    rostered agent that has reported. Heartbeat is folded in alongside so
    consumers don't have to cross-reference two structures."""
    out = [
        {
            "agent": r["agent"],
            "status": r["status"],
            "task": r.get("task", ""),
            "blocker": r.get("blocker", ""),
            "updated_at_ms": r.get("updated_at", 0),
            "heartbeat_ms": heartbeats.get(r["agent"], 0),
        }
        for r in rows
    ]
    print_json(out)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    # The roster is the only enumeration source: walk the declared team and
    # look each agent's status up by name. status.json keeps a row for every
    # agent that ever reported, but we never list it — a de-rostered / renamed
    # agent (or a leftover from an earlier config on this state dir) simply
    # isn't in the roster, so it can't surface as a phantom. Mirrors how
    # feishu/slash.py's /team already reads.
    roster = sorted(config.load_team().get("agents", {}))
    rows = [s for a in roster if (s := local_facts.get_status(a))]
    heartbeats = local_facts.all_heartbeats()
    if as_json:
        _emit_json(rows, heartbeats)
    else:
        _emit_text(rows, heartbeats)
    return 0
