"""`claudeteam forget <agent> [--kind K] [--yes]` — wipe agent memory.

Symmetric to `claudeteam recall` + `remember`. Used when an agent's
memory has gotten poisoned (wrong learning recorded, stale task
assignments piling up) and starting fresh is cheaper than triaging
which entries are still useful.

Two flavours:
- `forget <agent> --yes`              — wipe ALL entries
- `forget <agent> --kind K --yes`     — wipe only entries with kind=K
                                          (kind-level scalpel)

Refuses without `--yes` so an operator typo doesn't take out hours of
accumulated context. The reset command (`claudeteam reset`) already
does the whole-state nuke; this is the per-agent scalpel.
"""
from __future__ import annotations

from claudeteam.store import memory, team_memory
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, usage_error,
)


USAGE = (
    "usage: claudeteam forget <agent> [--kind K] [--yes]\n"
    "       claudeteam forget --team --id <E-n>   (retire one shared entry)\n"
    "       claudeteam forget --team --yes        (wipe all shared experience)\n"
    f"       known kinds: {memory.kinds_summary()}\n"
    "       (--kind drops only that slice; default = all entries)"
)


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    yes = pop_bool_flag(rest, "--yes")
    team = pop_bool_flag(rest, "--team")
    entry_id = pop_flag(rest, "--id") or ""
    kind = pop_flag(rest, "--kind") or ""

    if team:
        # Shared experience: --id retires a single entry (a targeted drop
        # needs no --yes); wiping the whole pool still does.
        if entry_id:
            ok = team_memory.remove(entry_id)
            print(f"🗑  team experience: retired {entry_id}" if ok
                  else f"🤝 team: no entry {entry_id} to retire")
            return 0
        if not yes:
            return error_exit(
                "❌ refusing to wipe ALL team experience without --yes; run "
                "`claudeteam recall --team` first, then re-run with --yes")
        n = team_memory.clear()
        print(f"🗑  team experience: wiped {n} entr{'ies' if n != 1 else 'y'}"
              if n else "🤝 team: nothing to wipe")
        return 0

    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]
    if not yes:
        target = f"{agent}'s {kind} memory" if kind else f"{agent}'s memory"
        recall_hint = (f"claudeteam recall {agent} --kind {kind}" if kind
                       else f"claudeteam recall {agent}")
        return error_exit(
            f"❌ refusing to wipe {target} without --yes; "
            f"run `{recall_hint}` first to verify what you're about to "
            f"drop, then re-run with --yes")

    memory.warn_unknown_kind(kind)

    if kind:
        n = memory.clear_kind(agent, kind)
        if n == 0:
            print(f"🧠 {agent}: nothing to forget (no entries with kind={kind})")
        else:
            print(f"🗑  {agent}: forgot {n} {kind} memory entr"
                  f"{'ies' if n != 1 else 'y'}")
    else:
        n = memory.clear(agent)
        if n == 0:
            print(f"🧠 {agent}: nothing to forget (memory was already empty)")
        else:
            print(f"🗑  {agent}: forgot {n} memory entr"
                  f"{'ies' if n != 1 else 'y'}")
    return 0
