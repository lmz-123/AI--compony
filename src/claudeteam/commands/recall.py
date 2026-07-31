"""`claudeteam recall <agent> [--limit N] [--kind K] [--json]`

Print an agent's durable memory entries. Symmetric to `claudeteam remember`.

Use cases:
  - operator audit: `claudeteam recall manager` to see what manager has
    been remembering across /clear cycles.
  - manager 巡视 a worker: `claudeteam recall worker_cc` from manager's
    pane to check what the worker has stored without going into worker_cc's
    tmux window.
  - debugging "agent forgot the task" — verify whether the memory entry
    was actually written.
  - kind filter: `claudeteam recall manager --kind decision` to scan
    one slice (kind-level filter). Cross-checks against `memory.KNOWN_KINDS`
    so a typo (`--kind decsion`) doesn't silently return [] — prints
    a hint with the closest known kind.

Default output is human-readable bullets; `--json` dumps the underlying
records for piping to jq / CI scripts.
"""
from __future__ import annotations

from claudeteam.store import memory, team_memory
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    print_json, usage_error,
)


USAGE = (
    "usage: claudeteam recall <agent> [--limit N] [--kind K] [--grep TEXT] [--json]\n"
    "       claudeteam recall --team [--limit N] [--grep TEXT] [--json]\n"
    f"       known kinds: {memory.kinds_summary()}\n"
    "       --kind accepts any string; unknown kinds get a stderr nudge\n"
    "       --grep TEXT filters by substring (content/kind/by/ref, case-insensitive)\n"
    "       --team reads the shared team experience (no agent arg needed); 📌 = pinned"
)

_DEFAULT_LIMIT = 20
_WIDE = 10_000  # fetch the whole pool before --grep filtering, then slice to limit


def _grep_rows(rows: list[dict], grep: str) -> list[dict]:
    """Substring filter over content/kind/by/ref (case-insensitive) — a
    dependency-free stand-in for semantic search so agents can pull the
    relevant slice on demand instead of carrying the whole pool."""
    g = grep.lower()
    return [r for r in rows
            if g in " ".join(str(r.get(k, "")) for k in
                             ("content", "kind", "by", "ref")).lower()]


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    as_json = pop_bool_flag(rest, "--json")
    from_team = pop_bool_flag(rest, "--team")
    raw_limit = pop_flag(rest, "--limit")
    kind_filter = pop_flag(rest, "--kind") or ""
    grep = pop_flag(rest, "--grep") or ""
    try:
        limit = int(raw_limit) if raw_limit else _DEFAULT_LIMIT
    except ValueError:
        return error_exit(f"❌ --limit must be an integer (got {raw_limit!r})")
    if limit < 1:
        return error_exit("❌ --limit must be >= 1")

    if from_team:
        rows = team_memory.list_recent(limit=_WIDE)
        if grep:
            rows = _grep_rows(rows, grep)
        rows = rows[-limit:]
        if as_json:
            print_json(rows)
            return 0
        if not rows:
            print(f"🤝 team: no shared experience matching {grep!r}" if grep
                  else "🤝 team: no shared experience yet")
            return 0
        gnote = f", grep={grep}" if grep else ""
        print(f"🤝 team shared experience: {len(rows)} "
              f"entr{'ies' if len(rows) != 1 else 'y'} "
              f"(oldest first, capped at {limit}{gnote})")
        for row in rows:
            ts = fmt_time_ms(row.get("created_at", 0))
            rid = row.get("id", "")
            kind = row.get("kind", "?")
            content = row.get("content", "")
            by = row.get("by", "")
            ref = row.get("ref", "")
            pin_mark = "📌 " if row.get("pin") else ""
            id_prefix = f"{rid}  " if rid else ""
            by_suffix = f"  (@{by})" if by else ""
            ref_suffix = f"  (ref={ref})" if ref else ""
            print(f"  [{ts}] {pin_mark}{id_prefix}[{kind}] "
                  f"{content}{by_suffix}{ref_suffix}")
        return 0

    if len(rest) < 1:
        return usage_error(USAGE)
    agent = rest[0]

    # Soft warn for unknown kinds — operator's filter could be
    # intentional (`fyi`-kind entry) so we proceed; the warning just
    # nudges them toward the convention if it was a typo.
    memory.warn_unknown_kind(kind_filter)

    if grep:
        rows = _grep_rows(
            memory.list_recent_filtered(agent, kind=kind_filter, limit=_WIDE),
            grep)[-limit:]
    else:
        rows = memory.list_recent_filtered(agent, kind=kind_filter, limit=limit)

    if as_json:
        print_json(rows)
        return 0

    if not rows:
        suffix = f" (kind={kind_filter})" if kind_filter else ""
        print(f"🧠 {agent}: no memory entries{suffix}")
        return 0
    filter_note = f", filter kind={kind_filter}" if kind_filter else ""
    print(f"🧠 {agent}: {len(rows)} entr{'ies' if len(rows) != 1 else 'y'} "
          f"(oldest first, capped at {limit}{filter_note})")
    for row in rows:
        ts = fmt_time_ms(row.get("created_at", 0))
        kind = row.get("kind", "?")
        content = row.get("content", "")
        ref = row.get("ref", "")
        suffix = f"  (ref={ref})" if ref else ""
        print(f"  [{ts}] [{kind}] {content}{suffix}")
    return 0
