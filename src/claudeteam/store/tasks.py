"""Local task store — coordination cards across agents.

One JSON file (`$CLAUDETEAM_STATE_DIR/tasks.json`) with shape:
    {"tasks": [...], "intents": [...], "_meta": {"last_id": N, "last_intent_id": M}}

Each task:
    {id, title, description, assignee, creator, intent_id,
     status, awaiting, approval_note, paused_by, paused_at,
     created_at, updated_at, completed_at}

Each intent (immutable verbatim record of the boss's original words):
    {id, raw_text, source_msg, key_points, creator, created_at}

Pure file-locked CRUD; lifecycle orchestration (who gets what, when) is left
to the agents — the store only owns state + the approval-suspend invariants.

Status vocabulary: 待处理 / 进行中 / 需审批 / 已完成 / 已取消
The `需审批` (needs-approval) state is a hard suspend: a task there can only
leave via approve()/reject(), never via the generic update() path.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.util import flock, now_ms, read_json, write_json


VALID_STATUSES = {"待处理", "进行中", "需审批", "已完成", "已取消"}
DEFAULT_STATUS = "待处理"
SUSPEND_STATUS = "需审批"
TERMINAL_STATUSES = {"已完成", "已取消"}

# Legal moves for the generic update() path. Terminals are frozen: a
# 已完成/已取消 task can never be resurrected by a stray `task update`
# — reopening is an explicit new task, not a silent status flip (a
# revived task would also re-anchor a stale intent into the assignee's
# CLAUDE.md). 待处理 → 已完成 stays legal so the everyday
# `task done <T-n>` shortcut works without a ceremonial 进行中 hop.
# 需审批 is absent on purpose: both directions are gated by
# pause()/approve()/reject() before this map is consulted.
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "待处理": {"进行中", "已完成", "已取消"},
    "进行中": {"待处理", "已完成", "已取消"},
    "已完成": set(),
    "已取消": set(),
}


def _file() -> Path:
    return paths.state_dir() / "tasks.json"


def _locked():
    return flock(_file().with_suffix(".lock"))


def _load() -> dict:
    return read_json(_file(), {"tasks": [], "intents": [],
                               "_meta": {"last_id": 0, "last_intent_id": 0}})


def _save(data: dict) -> None:
    write_json(_file(), data)


def _find(data: dict, task_id: str) -> dict | None:
    for task in data.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


def _set_status(task: dict, status: str) -> None:
    """Apply a status change + bookkeeping. Single source of truth for
    completed_at / updated_at so every transition path stays consistent."""
    task["status"] = status
    task["completed_at"] = now_ms() if status in TERMINAL_STATUSES else None
    task["updated_at"] = now_ms()


# ── public API ────────────────────────────────────────────────────


def create(assignee: str, title: str, *,
           description: str = "", creator: str = "", intent_id: str = "") -> str:
    """Create a new task; return its task_id (T-<n>).

    `intent_id` (optional) back-links the task to an immutable intent record
    (see create_intent) so the boss's verbatim ask can be re-read later.
    """
    if not title.strip():
        raise ValueError("title cannot be empty")
    with _locked():
        data = _load()
        meta = data.setdefault("_meta", {})
        meta["last_id"] = meta.get("last_id", 0) + 1
        tid = f"T-{meta['last_id']}"
        now = now_ms()
        data.setdefault("tasks", []).append({
            "id": tid,
            "title": title.strip(),
            "description": description,
            "assignee": assignee,
            "creator": creator,
            "intent_id": intent_id,
            "status": DEFAULT_STATUS,
            "awaiting": "",
            "approval_note": "",
            "paused_by": "",
            "paused_at": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        })
        _save(data)
        return tid


def update(task_id: str, *, status: str | None = None,
           assignee: str | None = None, title: str | None = None,
           description: str | None = None) -> bool:
    """Apply non-None fields. Returns False if task_id not found.

    The generic path refuses to move a task INTO or OUT OF `需审批`: the
    approval-suspend state may only be entered via pause() and left via
    approve()/reject(), so a stray `task update --status` can never bypass
    the gate or mis-advance a suspended task. All other status moves must
    appear in LEGAL_TRANSITIONS — in particular terminals are frozen, so
    a finished/cancelled task can't be silently resurrected. Re-asserting
    the current status is a tolerated no-op (idempotent retries).
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status} (valid: {sorted(VALID_STATUSES)})")
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None:
            return False
        if status is not None and SUSPEND_STATUS in (status, task.get("status")):
            raise ValueError(
                "需审批 transitions must use task pause/approve/reject, not update")
        current = task.get("status", DEFAULT_STATUS)
        if status is not None and status != current:
            allowed = LEGAL_TRANSITIONS.get(current, set())
            if status not in allowed:
                raise ValueError(
                    f"illegal transition: {current} → {status}"
                    + (f" (from {current} only: {' / '.join(sorted(allowed))})"
                       if allowed else f" ({current} is terminal — "
                                       f"reopen by creating a new task)"))
        if status is not None and status != current:
            _set_status(task, status)
        if assignee is not None:
            task["assignee"] = assignee
        if title is not None:
            task["title"] = title.strip()
        if description is not None:
            task["description"] = description
        task["updated_at"] = now_ms()
        _save(data)
        return True


def get(task_id: str) -> dict | None:
    for task in _load().get("tasks", []):
        if task["id"] == task_id:
            return task
    return None


def list_tasks(*, status: str | None = None,
               assignee: str | None = None) -> list[dict]:
    """Return tasks filtered by status / assignee, sorted by id."""
    rows = list(_load().get("tasks", []))
    if status is not None:
        rows = [t for t in rows if t.get("status") == status]
    if assignee is not None:
        rows = [t for t in rows if t.get("assignee") == assignee]
    rows.sort(key=lambda t: int(t["id"].split("-")[1]) if "-" in t["id"] else 0)
    return rows


# ── intent records (immutable verbatim asks) ──────────────────────


def create_intent(raw_text: str, *, source_msg: str = "",
                   key_points: str = "", creator: str = "user") -> str:
    """Persist the boss's verbatim words as an immutable intent; return I-<n>.

    `raw_text` is never rewritten or compressed — tasks back-link to it so
    the original ask can be re-read after context drift / /compact."""
    if not raw_text.strip():
        raise ValueError("intent raw_text cannot be empty")
    with _locked():
        data = _load()
        meta = data.setdefault("_meta", {})
        meta["last_intent_id"] = meta.get("last_intent_id", 0) + 1
        iid = f"I-{meta['last_intent_id']}"
        data.setdefault("intents", []).append({
            "id": iid,
            "raw_text": raw_text,
            "source_msg": source_msg,
            "key_points": key_points,
            "creator": creator,
            "created_at": now_ms(),
        })
        _save(data)
        return iid


def get_intent(intent_id: str) -> dict | None:
    for intent in _load().get("intents", []):
        if intent.get("id") == intent_id:
            return intent
    return None


# ── approval-suspend state machine ────────────────────────────────


def pause(task_id: str, *, awaiting: str = "user",
          approval_note: str = "", paused_by: str = "") -> bool:
    """进行中 → 需审批 (hard suspend). Returns False if the task is missing
    or not currently 进行中 (so a suspend can't be forced from a bad state)."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") != "进行中":
            return False
        _set_status(task, SUSPEND_STATUS)
        task["awaiting"] = awaiting
        task["approval_note"] = approval_note
        task["paused_by"] = paused_by
        task["paused_at"] = now_ms()
        _save(data)
        return True


def approve(task_id: str, *, done: bool = False, note: str = "") -> bool:
    """需审批 → 进行中 (continue) or 已完成 (done=True). Returns False unless
    the task is currently 需审批 — guarding the gate from both sides.

    `note` carries the VERDICT (what the boss actually decided), symmetric
    with reject's `feedback`. It replaces the pending question in
    `approval_note` so the resumed task records what was decided, not just
    that a decision happened — e.g. the boss approves '鱼香肉丝' but the
    gate only relays '已批准·继续'; the worker resumes from an anchor
    holding the question alone and invents its own answer. Empty note
    keeps the historical clear-on-approve behavior.
    """
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") != SUSPEND_STATUS:
            return False
        _set_status(task, "已完成" if done else "进行中")
        task["awaiting"] = ""
        task["approval_note"] = note
        _save(data)
        return True


def reject(task_id: str, *, feedback: str = "", cancel: bool = False) -> bool:
    """需审批 → 进行中 (rework, with feedback) or 已取消 (cancel=True).
    Returns False unless the task is currently 需审批."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") != SUSPEND_STATUS:
            return False
        _set_status(task, "已取消" if cancel else "进行中")
        task["awaiting"] = ""
        task["approval_note"] = feedback
        _save(data)
        return True


def void(task_id: str, *, reason: str = "", voided_by: str = "") -> bool:
    """Tombstone a task → 已取消 from ANY state, including the frozen
    terminal 已完成. This is the ONLY path that can retire a
    mistakenly-completed task (e.g. a duplicate task was `done` and then
    had no exit — reject is 需审批-only and the generic update() path
    freezes terminals).

    Why this is safe even though update() deliberately freezes terminals:
    that freeze stops a *stray* `task update --status` from resurrecting +
    re-anchoring a finished task. A void is different in kind — explicit,
    audited, and one-directional toward a terminal. 已取消 is itself
    terminal and excluded from the anchor projection (identity only reads
    进行中/需审批 tasks), so voiding REMOVES the card from the assignee's
    CLAUDE.md anchor rather than reviving a stale intent. The
    terminal→active freeze stays fully intact; only terminal→已取消 is
    opened, and only through this verb.

    Returns False if the task is missing or already 已取消 (idempotent
    no-op — re-voiding never clobbers the original reason)."""
    with _locked():
        data = _load()
        task = _find(data, task_id)
        if task is None or task.get("status") == "已取消":
            return False
        _set_status(task, "已取消")
        task["awaiting"] = ""
        task["approval_note"] = reason
        task["paused_by"] = voided_by
        task["paused_at"] = now_ms()
        _save(data)
        return True
