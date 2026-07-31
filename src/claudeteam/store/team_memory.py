"""Team-shared experience — durable lessons the whole team reads and writes.

Distinct from per-agent `memory.py` (each agent's own notes) and from
`local_facts` logs (the raw audit trail): this is the *one* pool of
distilled team experience — conventions, gotchas, cross-agent decisions —
injected into every agent's wake prompt so hard-won lessons aren't
relearned per agent.

One append-only file: `<state_dir>/share/experience.jsonl`. Each entry:
  {kind, content, by, ref?, created_at}
`kind` reuses `memory.KNOWN_KINDS`; `by` records the contributing agent.

Injection follows the just-in-time model: only the **pinned** entries
(the curated core) ride every agent's always-loaded prompt; the long tail
is pulled on demand via `recall`. This keeps the native memory file small
(Claude Code starts dropping rules once CLAUDE.md grows past ~200 lines).

API mirrors the per-agent store but takes no `agent` key (the pool is
shared), plus update/remove/pin so it stays a *living* KB rather than an
append-only pile of contradictions:
  `append(content, *, kind, by, ref, pin)`  → write 1 entry (gets `E-<n>` id)
  `update(id, *, content, kind, by, pin)`   → modify an entry in place
  `remove(id)`                          → retire one entry
  `list_recent(*, limit=30)`            → list all, oldest-first
  `clear()`                             → drop all entries
  `render_for_prompt(*, limit=12)`      → pinned core (+ pull pointer)
"""
from __future__ import annotations

import json

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_jsonl


_MAX = 300  # default cap; override via tunable share.max_experience


def _max() -> int:
    """Retention cap for the shared pool. Larger than the per-agent cap
    since it serves the whole team; tunable `share.max_experience`."""
    try:
        from claudeteam.runtime import tunables
        return max(1, int(tunables.tunable("share.max_experience", _MAX)))
    except Exception:
        return _MAX


def _file():
    return paths.share_dir() / "experience.jsonl"


def _next_id(rows: list[dict]) -> str:
    """`E-<n>` with n = (highest existing numeric suffix) + 1."""
    n = 0
    for r in rows:
        rid = str(r.get("id", ""))
        if rid.startswith("E-") and rid[2:].isdigit():
            n = max(n, int(rid[2:]))
    return f"E-{n + 1}"


def _enforce_cap(rows: list[dict], cap: int) -> list[dict]:
    """Bound the pool to `cap`, evicting the oldest UNPINNED entries first;
    pinned entries (the curated core) are never auto-dropped. Order
    preserved. If pinned alone exceed the cap, they all stay — an explicit
    pin is a deliberate keep."""
    if len(rows) <= cap:
        return rows
    budget = max(0, cap - sum(1 for r in rows if r.get("pin")))
    kept, taken = [], 0
    for r in reversed(rows):                      # newest → oldest
        if r.get("pin"):
            kept.append(r)
        elif taken < budget:
            kept.append(r)
            taken += 1
    return list(reversed(kept))


def append(content: str, *, kind: str = "note", by: str = "",
           ref: str = "", pin: bool = False) -> dict:
    """Append one team-experience entry; returns the persisted record.

    fcntl-locked so concurrent writers from different panes don't
    interleave bytes. Each entry gets a stable `E-<n>` id (so it can be
    updated or retired) and a `pin` flag — pinned entries are the curated
    core injected into every agent's prompt; the rest are pull-only. Over
    the cap, the oldest UNPINNED entries drop first."""
    paths.share_dir().mkdir(parents=True, exist_ok=True)
    path = _file()
    with flock(paths.share_dir() / ".experience.lock"):
        rows = read_jsonl(path)
        entry = {
            "id": _next_id(rows),
            "kind": str(kind),
            "content": str(content or ""),
            "by": str(by or ""),
            "ref": str(ref or ""),
            "pin": bool(pin),
            "created_at": now_ms(),
        }
        rows.append(entry)
        rows = _enforce_cap(rows, _max())
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
    return entry


def update(entry_id: str, *, content: str | None = None,
           kind: str | None = None, by: str = "",
           pin: bool | None = None) -> dict | None:
    """Modify an existing entry in place — only the provided fields change.

    `pin=True/False` promotes/demotes the entry to/from the curated core.
    Stamps `updated_at` (and `updated_by` when `by` is given). Returns the
    updated record, or None if no entry carries that id. This is what makes
    the shared pool a *living* KB: refine a lesson instead of appending a
    contradicting one."""
    if not _file().exists():
        return None
    path = _file()
    with flock(paths.share_dir() / ".experience.lock"):
        rows = read_jsonl(path)
        hit = next((r for r in rows if str(r.get("id", "")) == entry_id), None)
        if hit is None:
            return None
        if content is not None:
            hit["content"] = str(content)
        if kind is not None:
            hit["kind"] = str(kind)
        if pin is not None:
            hit["pin"] = bool(pin)
        hit["updated_at"] = now_ms()
        if by:
            hit["updated_by"] = str(by)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
    return hit


def remove(entry_id: str) -> bool:
    """Retire one entry by id (a lesson that's now wrong / obsolete).
    Returns True if an entry was dropped, else False."""
    if not _file().exists():
        return False
    path = _file()
    with flock(paths.share_dir() / ".experience.lock"):
        rows = read_jsonl(path)
        kept = [r for r in rows if str(r.get("id", "")) != entry_id]
        if len(kept) == len(rows):
            return False
        if kept:
            path.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n",
                encoding="utf-8",
            )
        else:
            path.unlink()
    return True


def list_recent(*, limit: int = 30) -> list[dict]:
    """Return up to `limit` most recent entries, oldest-first."""
    return read_jsonl(_file())[-limit:]


def clear() -> int:
    """Wipe the shared experience file. Returns the number of dropped entries."""
    path = _file()
    if not path.exists():
        return 0
    n = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    path.unlink()
    return n


_PROMPT_PINNED_CAP = 12  # how many pinned entries to inject into the prompt


def render_for_prompt(*, limit: int = _PROMPT_PINNED_CAP) -> str:
    """Markdown for the always-loaded prompt — the curated CORE only.

    Injects just the **pinned** entries (capped), plus a one-line pointer
    to `recall` for everything else. Keeps the native memory file small
    (Claude Code drops rules once CLAUDE.md grows past ~200 lines) and
    pushes the long tail to just-in-time pull. Empty pool → ''."""
    rows = read_jsonl(_file())
    if not rows:
        return ""
    pinned = [r for r in rows if r.get("pin")][-limit:]
    lines: list[str] = []
    if pinned:
        lines.append("## 团队共享经验（置顶 · 全队常驻）")
        for r in pinned:
            rid = f"[{r['id']}] " if r.get("id") else ""
            by = f" (@{r['by']})" if r.get("by") else ""
            ref = f" (ref={r['ref']})" if r.get("ref") else ""
            lines.append(
                f"- {rid}[{r.get('kind', '?')}] {r.get('content', '')}{by}{ref}")
    others = len(rows) - len(pinned)
    if others > 0:
        lines.append(
            f"_另有 {others} 条团队经验未置顶；需要时 "
            f"`claudeteam recall --team [--grep 关键词]` 现拉。_")
    return "\n".join(lines)
