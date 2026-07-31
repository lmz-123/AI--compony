"""Per-agent durable memory.

Each agent gets a `memory.jsonl` under their per-agent business dir
(`agents/<agent>/memory.jsonl`, alongside identity.md) — append-only
structured notes that survive across tmux pane restarts and `/clear`
cycles. (Pre-consolidation this lived at `facts/<agent>/memory.jsonl`;
`_migrate_legacy` moves it on first access.)

Why not reuse `local_facts.append_log`? Logs are an audit trail
(every action). Memory is a curated subset — what an agent should
*re-read* on wake to keep continuity. Keeping them separate lets us:
  - inject memory (NOT logs) into the identity init prompt without
    flooding the worker with audit minutiae.
  - rate-limit memory growth (default 200; tunable
    `memory.max_per_agent`) without forcing log truncation.

Each entry is a tiny dict:
  {kind, content, ref?, created_at}

`kind` is a short tag (see `KNOWN_KINDS`):
  `task_assigned` / `task_completed` / `learning` / `blocker` /
  `decision` / `note`
Convention, not enforced — `append` accepts any string but soft-warns
to stderr when `kind` falls outside KNOWN_KINDS so the schema doesn't
fragment into free-form labels (`fyi`, `important!!!`, `mood-of-day`)
that hurt `recall` scannability.

API surface:
  `append(agent, kind, content, *, ref="")`  → write 1 entry
  `list_recent(agent, *, limit=20)`          → list, oldest-first
  `clear(agent)`                              → drop all entries
  `clear_kind(agent, kind)`                   → drop one slice
  `render_for_prompt(agent, *, limit=20)`     → markdown for init prompt
  `all_agents_with_memory()`                  → iterator for /health audit
  `kinds_summary()` / `kinds_sorted()`        → KNOWN_KINDS pretty-prints
"""
from __future__ import annotations

import json
from typing import Iterable

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_jsonl


_MAX_PER_AGENT = 200  # default cap; override via tunable memory.max_per_agent


def _max_per_agent() -> int:
    """Retention cap (entries per agent). Tunable `memory.max_per_agent`
    so a deployment with chatty agents can raise it without a code
    change; floor of 1 keeps the truncate-from-front slice sane if
    someone configures 0 or junk."""
    try:
        from claudeteam.runtime import tunables
        return max(1, int(tunables.tunable("memory.max_per_agent",
                                           _MAX_PER_AGENT)))
    except Exception:
        return _MAX_PER_AGENT

# Convention vocabulary for memory entry `kind`. Not enforced — `append`
# accepts any string so future kinds can land without a code change —
# but unknown kinds get a soft stderr warning so boss `recall` reading
# doesn't get a long-tail of free-form labels (`fyi`, `important!!!`,
# `mood-of-day`) that fragment the schema.
KNOWN_KINDS: tuple[str, ...] = (
    "task_assigned",
    "task_completed",
    "learning",
    "blocker",
    "decision",
    "note",
)


def kinds_summary() -> str:
    """`' / '`-joined list of KNOWN_KINDS — used by `claudeteam remember /
    recall / forget` USAGE strings. Extracted from the three CLI commands
    so the separator (and any future kinds) flows from one place."""
    return " / ".join(KNOWN_KINDS)


def kinds_sorted() -> list[str]:
    """Sorted list of KNOWN_KINDS — used by slash card handlers
    (`/recall`, `/forget`) for in-card display. Several sites in
    `feishu/slash.py` previously called `sorted(memory.KNOWN_KINDS)`
    inline; centralising here keeps the alphabetical-display contract
    in one place (Feishu boss reading two cards expects the same
    order)."""
    return sorted(KNOWN_KINDS)


def warn_unknown_kind(kind: str) -> None:
    """If `kind` is non-empty and not in KNOWN_KINDS, emit a one-line
    stderr nudge with the convention list. No-op for empty / known
    kinds.

    Extracted from `commands/recall` + `commands/forget`, which inlined
    the same `warn(f"⚠️ --kind {kind!r} not in known kinds
    ({sorted(KNOWN_KINDS)}); proceeding anyway")` block. The
    slash-card siblings (`feishu/slash._handle_recall/_handle_forget`)
    don't use this — they embed the warning into a card body string
    with different formatting per command, so they keep their
    inline branches.
    """
    if kind and kind not in KNOWN_KINDS:
        from claudeteam.util import warn
        warn(f"⚠️  --kind {kind!r} not in known kinds "
             f"({sorted(KNOWN_KINDS)}); proceeding anyway")


def _agent_dir(agent: str):
    return paths.agent_dir(agent)


def _memory_file(agent: str):
    return _agent_dir(agent) / "memory.jsonl"


def _locked(agent: str):
    return flock(_agent_dir(agent) / ".memory.lock")


def _legacy_memory_file(agent: str):
    """Pre-consolidation home: `facts/<agent>/memory.jsonl`. Kept only so
    a one-time lazy migration can move existing memory into the new
    `agents/<agent>/` business dir on first access (R-consolidate)."""
    return paths.facts_dir() / agent / "memory.jsonl"


def _migrate_legacy(agent: str) -> None:
    """Move memory.jsonl from the old `facts/<agent>/` location into the
    consolidated `agents/<agent>/` dir. No-op once migrated, or if there's
    nothing to move. Best-effort: a failure here must not block the
    caller — at worst the agent starts a fresh file in the new location
    and the legacy copy is ignored."""
    new = _memory_file(agent)
    if new.exists():
        return
    old = _legacy_memory_file(agent)
    if not old.exists():
        return
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.replace(new)
    except OSError:
        # Cross-device rename / perms — fall back to copy, leave old in place.
        try:
            new.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass


def append(agent: str, kind: str, content: str, *, ref: str = "") -> dict:
    """Append a memory entry. Returns the persisted record (for caller logging).

    The append is fcntl-locked so concurrent writers from different
    panes don't interleave bytes mid-line. Caller passes `ref` (a
    message_id, task_id, etc.) when the memory is tied to an external
    artefact — it is rendered verbatim into the recall view, not parsed.

    When `kind` isn't in KNOWN_KINDS, print a one-line stderr warning
    suggesting a known kind. Doesn't reject the write (free-form is
    sometimes the right call), just nudges so recall output stays
    scannable.
    """
    if kind and kind not in KNOWN_KINDS:
        import sys
        print(f"  ⚠️ memory.append: unknown kind {kind!r} for {agent} — "
              f"convention is {sorted(KNOWN_KINDS)}; entry written "
              f"anyway",
              file=sys.stderr)
    entry = {
        "kind": str(kind),
        "content": str(content or ""),
        "ref": str(ref or ""),
        "created_at": now_ms(),
    }
    _migrate_legacy(agent)
    _agent_dir(agent).mkdir(parents=True, exist_ok=True)
    path = _memory_file(agent)
    with _locked(agent):
        # Read existing (corrupt-tolerant via util.read_jsonl), append,
        # truncate from front if over cap, write back atomically.
        # Append-only file mode would be faster but we need the
        # truncate-from-front step to keep memory size bounded.
        rows = read_jsonl(path)
        rows.append(entry)
        cap = _max_per_agent()
        if len(rows) > cap:
            dropped = rows[:-cap]
            rows = rows[-cap:]
            # The drop is by-design bounded retention, but it must not be
            # invisible: an agent whose early decisions/blockers silently
            # age out can't know to re-record them. One line per overflow
            # write, naming the oldest casualty so it's recoverable from
            # logs while the window is still fresh.
            import sys
            oldest = dropped[0]
            print(f"  ⚠️ memory.append: {agent} over cap ({cap}) — dropped "
                  f"{len(dropped)} oldest entr{'y' if len(dropped) == 1 else 'ies'}, "
                  f"first casualty: [{oldest.get('kind', '?')}] "
                  f"{str(oldest.get('content', ''))[:60]!r} "
                  f"(raise tunable memory.max_per_agent to keep more)",
                  file=sys.stderr)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
    return entry


def list_recent(agent: str, *, limit: int = 20) -> list[dict]:
    """Return up to `limit` most recent memory entries, oldest-first
    (so the agent reads them in chronological order)."""
    _migrate_legacy(agent)
    return read_jsonl(_memory_file(agent))[-limit:]


def list_recent_filtered(agent: str, *,
                         kind: str = "", limit: int = 20) -> list[dict]:
    """Return up to `limit` most recent entries, oldest-first.

    When `kind` is set, scans the FULL memory window for matches and
    slices to `limit` afterwards — so a hot agent with many unrelated
    notes still surfaces rare kinds (e.g. one resolved blocker among
    100 task_completed rows). When `kind` is empty,
    behaves identically to `list_recent` so callers can use this as
    the single entry point regardless of whether they filter.

    Extracted from `commands/recall.py` + `feishu/slash._handle_recall`,
    which inlined the same fetch-then-filter dance and both reached
    into the module-private `_MAX_PER_AGENT` to size the pre-filter
    window.
    """
    if kind:
        all_rows = list_recent(agent, limit=_max_per_agent())
        return [r for r in all_rows if r.get("kind") == kind][-limit:]
    return list_recent(agent, limit=limit)


def clear(agent: str) -> int:
    """Wipe an agent's memory file. Returns the number of dropped entries.

    Used by `claudeteam reset` (the whole-state nuke) and operationally
    when an agent's history is poisoned and starting fresh is cheaper
    than triaging which memories are stale."""
    _migrate_legacy(agent)
    path = _memory_file(agent)
    if not path.exists():
        return 0
    n = sum(1 for _ in path.read_text(encoding="utf-8").splitlines() if _.strip())
    path.unlink()
    return n


def clear_kind(agent: str, kind: str) -> int:
    """Drop only entries with `kind == <kind>` from `agent`'s memory.

    Returns the number of dropped entries (0 if the file is missing or
    no entries match). Scalpel inside the scalpel — `forget --kind
    blocker` lets boss / manager wipe one slice (e.g. resolved
    blockers) while keeping decisions / learnings intact.

    Reads + filters + atomic-rewrites under the same flock the append
    path uses, so concurrent writers can't see a partial state.
    """
    _migrate_legacy(agent)
    path = _memory_file(agent)
    if not path.exists():
        return 0
    with _locked(agent):
        rows = read_jsonl(path)
        kept = [r for r in rows if r.get("kind") != kind]
        dropped = len(rows) - len(kept)
        if dropped == 0:
            return 0
        if not kept:
            # All entries matched the filter — remove the file entirely
            # so list_recent treats this agent as fresh (matches `clear`'s
            # "empty memory == no file" invariant).
            path.unlink()
        else:
            path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False)
                          for r in kept) + "\n",
                encoding="utf-8",
            )
        return dropped


def render_for_prompt(agent: str, *, limit: int = 20) -> str:
    """Format `agent`'s recent memory as a markdown block suitable for
    injecting into the identity init prompt.

    Empty memory → empty string (callers should branch on `if memory:`).
    Each entry renders as one bullet line: `- [<kind>] <content> (ref=<ref>)`
    with the ref suffix omitted when empty.
    """
    rows = list_recent(agent, limit=limit)
    if not rows:
        return ""
    lines = ["## 既往记忆（按时间）"]
    for r in rows:
        suffix = f" (ref={r['ref']})" if r.get("ref") else ""
        lines.append(f"- [{r.get('kind', '?')}] {r.get('content', '')}{suffix}")
    return "\n".join(lines)


def all_agents_with_memory() -> Iterable[str]:
    """Yield agent names that have a memory file, sorted + de-duped.

    Scans both the consolidated location (`agents/<name>/memory.jsonl`)
    and the legacy one (`facts/<name>/memory.jsonl`) so an agent whose
    memory hasn't been touched (hence not yet lazily migrated) since the
    consolidation still shows up in health / audit during the transition.
    """
    seen: set[str] = set()
    for base in (paths.state_dir() / "agents", paths.facts_dir()):
        if not base.exists():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "memory.jsonl").exists():
                seen.add(child.name)
    yield from sorted(seen)
