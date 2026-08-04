"""Passive current-task update store ("radio-lite").

This is a tiny, local AgentRadio-inspired layer: same-task supplements are
recorded beside an agent's state so the worker can pull only the updates for
its current T-n, instead of re-reading the whole inbox or carrying unrelated
history into the task context.

The authoritative audit row remains `facts/inbox.json`; radio only keeps a
short per-agent/per-task projection for passive awareness.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_json, write_json


MAX_SUMMARY_CHARS = 220
MAX_CONTENT_CHARS = 1200


def _file(agent: str) -> Path:
    return paths.agent_dir(agent) / "radio.json"


def _locked(agent: str):
    return flock(paths.agent_dir(agent) / "radio.lock")


def _empty() -> dict:
    return {"threads": {}}


def _summary(text: str, *, limit: int = MAX_SUMMARY_CHARS) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _content(text: str, *, limit: int = MAX_CONTENT_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n..."


def append_update(agent: str, task_id: str, local_id: str, frm: str, content: str) -> None:
    """Append an unacked passive update for `agent` / `task_id`.

    Idempotent by `local_id`, so a retrying sender cannot duplicate the
    same update in radio.
    """
    if not agent or not task_id or not local_id:
        return
    with _locked(agent):
        path = _file(agent)
        data = read_json(path, _empty())
        threads = data.setdefault("threads", {})
        thread = threads.setdefault(task_id, {
            "task_id": task_id,
            "updates": [],
            "archived": False,
            "archived_at": None,
        })
        thread["archived"] = False
        thread["archived_at"] = None
        updates = thread.setdefault("updates", [])
        if any(u.get("local_id") == local_id for u in updates):
            return
        updates.append({
            "local_id": local_id,
            "from": frm,
            "content": _content(content),
            "summary": _summary(content),
            "created_at": now_ms(),
            "acked": False,
            "acked_at": None,
        })
        write_json(path, data)


def list_updates(agent: str, *, task_id: str = "", unacked_only: bool = False,
                 include_archived: bool = False, limit: int = 20) -> list[dict]:
    """Return flattened radio updates, oldest-first."""
    data = read_json(_file(agent), _empty())
    rows: list[dict] = []
    for tid, thread in (data.get("threads") or {}).items():
        if task_id and tid != task_id:
            continue
        if thread.get("archived") and not include_archived:
            continue
        for update in thread.get("updates") or []:
            if unacked_only and update.get("acked"):
                continue
            row = dict(update)
            row["task_id"] = tid
            row["archived"] = bool(thread.get("archived"))
            rows.append(row)
    rows.sort(key=lambda r: r.get("created_at", 0))
    return rows[-max(1, limit):]


def agent_threads(agent: str, *, include_archived: bool = False) -> list[dict]:
    """Compact per-task radio summary for admin/detail views."""
    data = read_json(_file(agent), _empty())
    rows: list[dict] = []
    for tid, thread in (data.get("threads") or {}).items():
        archived = bool(thread.get("archived"))
        if archived and not include_archived:
            continue
        updates = list(thread.get("updates") or [])
        unacked = [u for u in updates if not u.get("acked")]
        rows.append({
            "task_id": tid,
            "archived": archived,
            "count": len(updates),
            "unacked_count": len(unacked),
            "last_summary": (updates[-1].get("summary", "") if updates else ""),
            "last_at": (updates[-1].get("created_at") if updates else None),
        })
    rows.sort(key=lambda r: r.get("last_at") or 0)
    return rows


def ack(agent: str, *, task_id: str = "", local_id: str = "") -> int:
    """Mark updates as acked and mark their backing inbox rows read."""
    changed_ids: list[str] = []
    with _locked(agent):
        path = _file(agent)
        data = read_json(path, _empty())
        now = now_ms()
        for tid, thread in (data.get("threads") or {}).items():
            if task_id and tid != task_id:
                continue
            for update in thread.get("updates") or []:
                if local_id and update.get("local_id") != local_id:
                    continue
                if not update.get("acked"):
                    update["acked"] = True
                    update["acked_at"] = now
                    changed_ids.append(str(update.get("local_id") or ""))
        if changed_ids:
            write_json(path, data)
    if changed_ids:
        # Keep inbox/read state consistent, but radio state is already
        # authoritative for this passive projection if a best-effort read fails.
        try:
            from claudeteam.store import local_facts
            for mid in changed_ids:
                if mid:
                    local_facts.mark_read(mid)
        except Exception:
            pass
    return len(changed_ids)


def seal_task(agent: str, task_id: str) -> bool:
    """Archive a task radio thread when the task reaches a terminal state."""
    if not agent or not task_id:
        return False
    with _locked(agent):
        path = _file(agent)
        data = read_json(path, _empty())
        thread = (data.get("threads") or {}).get(task_id)
        if not thread:
            return False
        now = now_ms()
        thread["archived"] = True
        thread["archived_at"] = now
        for update in thread.get("updates") or []:
            if not update.get("acked"):
                update["acked"] = True
                update["acked_at"] = now
        write_json(path, data)
        return True
