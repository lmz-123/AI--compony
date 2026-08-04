"""Task learning drafts.

The learning store turns raw task traces into small, reviewable drafts. It is
deliberately NOT a giant memory DB and does not mutate skills automatically:

  task/log/inbox evidence → L-n draft → optional promote to memory/team KB

This keeps experience reusable and governable, while avoiding the classic
"save every chat line forever" token sink.
"""
from __future__ import annotations

import re
from pathlib import Path

from claudeteam.runtime import paths
from claudeteam.store import local_facts, memory, tasks
from claudeteam.util import flock, now_ms, read_json, write_json


MAX_TEXT = 360
MAX_EVIDENCE = 8


def _dir() -> Path:
    return paths.state_dir() / "learn"


def _file() -> Path:
    return _dir() / "drafts.json"


def _locked():
    return flock(_dir() / "drafts.lock")


def _empty() -> dict:
    return {"drafts": [], "_meta": {"last_id": 0}}


def _cap(text: str, *, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _next_id(rows: list[dict]) -> str:
    n = 0
    for row in rows:
        lid = str(row.get("id", ""))
        if lid.startswith("L-") and lid[2:].isdigit():
            n = max(n, int(lid[2:]))
    return f"L-{n + 1}"


def _existing_for_task(rows: list[dict], task_id: str) -> dict | None:
    for row in rows:
        if row.get("task_id") == task_id:
            return row
    return None


def _task_kind(t: dict) -> str:
    status = t.get("status", "")
    if status == "已完成":
        return "task_completed"
    if status in {"需审批", "已取消"}:
        return "blocker"
    return "learning"


def _category(t: dict, evidence_text: str) -> str:
    assignee = (t.get("assignee") or "").lower()
    blob = f"{t.get('title', '')} {t.get('description', '')} {evidence_text}".lower()
    if assignee == "manager" or any(k in blob for k in ("dispatch", "派单", "调度", "拆解")):
        return "orchestrator"
    if any(k in blob for k in ("deploy", "部署", "push", "apk", "artifact", "github actions")):
        return "release"
    if any(k in blob for k in ("log", "日志", "redis", "postgres", "sls", "排障", "health")):
        return "ops"
    if any(k in blob for k in ("test", "pytest", "flutter", "dart", "代码", "ui", "backend")):
        return "development"
    return "general"


def _suggested_scope(t: dict) -> str:
    assignee = t.get("assignee") or ""
    if assignee == "manager":
        return "manager"
    if assignee:
        return assignee
    return "team"


def _evidence_for_task(t: dict) -> list[dict]:
    task_id = t.get("id", "")
    assignee = t.get("assignee", "")
    rows: list[dict] = []
    for msg in local_facts.list_task_messages(task_id, limit=40):
        rows.append({
            "source": "inbox",
            "from": msg.get("from", ""),
            "to": msg.get("to", ""),
            "content": _cap(msg.get("content", "")),
            "created_at": msg.get("created_at"),
        })
    for log in local_facts.list_logs(assignee, limit=120):
        if log.get("ref") == task_id or task_id in str(log.get("content", "")):
            rows.append({
                "source": "log",
                "type": log.get("type", ""),
                "content": _cap(log.get("content", "")),
                "created_at": log.get("created_at"),
            })
    for mem in memory.list_recent(assignee, limit=120):
        if mem.get("ref") == task_id or task_id in str(mem.get("content", "")):
            rows.append({
                "source": "memory",
                "kind": mem.get("kind", ""),
                "content": _cap(mem.get("content", "")),
                "created_at": mem.get("created_at"),
            })
    rows.sort(key=lambda r: r.get("created_at") or 0)
    return rows[-MAX_EVIDENCE:]


def _lesson(t: dict, evidence: list[dict]) -> str:
    status = t.get("status", "")
    title = _cap(t.get("title", ""), limit=140)
    assignee = t.get("assignee", "")
    if status == "已完成":
        prefix = "完成类经验"
    elif status == "已取消":
        prefix = "取消/废弃类经验"
    elif status == "需审批":
        prefix = "审批阻塞类经验"
    else:
        prefix = "过程类经验"
    hint = ""
    joined = " ".join(str(e.get("content", "")) for e in evidence)
    if re.search(r"artifact|apk|download_url|下载", joined, re.I):
        hint = "；下次交付服务器下载链接、manifest 与 SHA256，不尝试飞书大文件直传"
    elif re.search(r"ssh|host key|permission denied|publickey", joined, re.I):
        hint = "；下次优先检查 SSH key、known_hosts 与远端权限"
    elif re.search(r"pytest|test|测试|analyze|format", joined, re.I):
        hint = "；下次保留最小验证证据，避免重复跑无关套件"
    elif re.search(r"deploy|health|rollback|部署|回滚", joined, re.I):
        hint = "；下次用固定部署脚本并回报 deploy/health/rollback 摘要"
    return _cap(f"{prefix}: {assignee} 处理 {t.get('id', '')}「{title}」状态为 {status}{hint}。")


def create_from_task(task_id: str, *, by: str = "system", force: bool = False) -> dict | None:
    """Create or return a reviewable learning draft for a task."""
    t = tasks.get(task_id)
    if not t:
        return None
    _dir().mkdir(parents=True, exist_ok=True)
    with _locked():
        data = read_json(_file(), _empty())
        rows = data.setdefault("drafts", [])
        existing = _existing_for_task(rows, task_id)
        if existing and not force:
            return existing
        if existing and force:
            rows.remove(existing)
        evidence = _evidence_for_task(t)
        evidence_text = " ".join(str(e.get("content", "")) for e in evidence)
        draft = {
            "id": _next_id(rows),
            "task_id": task_id,
            "kind": _task_kind(t),
            "status": "draft",
            "category": _category(t, evidence_text),
            "suggested_scope": _suggested_scope(t),
            "suggested_agent": t.get("assignee", ""),
            "title": _cap(t.get("title", ""), limit=160),
            "lesson": _lesson(t, evidence),
            "task_status": t.get("status", ""),
            "assignee": t.get("assignee", ""),
            "creator": t.get("creator", ""),
            "evidence": evidence,
            "created_by": by,
            "created_at": now_ms(),
            "promoted_at": None,
            "promoted_to": "",
        }
        rows.append(draft)
        data["_meta"] = {"last_id": int(draft["id"].split("-")[1])}
        write_json(_file(), data)
        return draft


def list_drafts(*, status: str = "", agent: str = "", limit: int = 30) -> list[dict]:
    rows = list(read_json(_file(), _empty()).get("drafts", []))
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if agent:
        rows = [r for r in rows if r.get("suggested_agent") == agent or r.get("assignee") == agent]
    rows.sort(key=lambda r: r.get("created_at") or 0)
    return rows[-max(1, limit):]


def get(draft_id: str) -> dict | None:
    for row in read_json(_file(), _empty()).get("drafts", []):
        if row.get("id") == draft_id:
            return row
    return None


def mark_promoted(draft_id: str, promoted_to: str) -> dict | None:
    with _locked():
        data = read_json(_file(), _empty())
        hit = next((r for r in data.get("drafts", []) if r.get("id") == draft_id), None)
        if hit is None:
            return None
        hit["status"] = "promoted"
        hit["promoted_at"] = now_ms()
        hit["promoted_to"] = promoted_to
        write_json(_file(), data)
        return hit


def write_skill_draft(draft_id: str, skill_name: str) -> Path | None:
    draft = get(draft_id)
    if not draft:
        return None
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", skill_name.strip()).strip("-") or "skill"
    out_dir = _dir() / "skill-drafts"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{safe}-{draft_id}.md"
    evidence = "\n".join(
        f"- {e.get('source')}: {e.get('content', '')}" for e in draft.get("evidence", [])
    )
    text = (
        f"# Skill draft: {safe}\n\n"
        f"- Source draft: {draft_id}\n"
        f"- Source task: {draft.get('task_id', '')}\n"
        f"- Suggested scope: {draft.get('suggested_scope', '')}\n"
        f"- Category: {draft.get('category', '')}\n\n"
        "## Lesson\n\n"
        f"{draft.get('lesson', '')}\n\n"
        "## Evidence\n\n"
        f"{evidence or '- no compact evidence captured'}\n\n"
        "## Proposed SKILL.md addition\n\n"
        "- When a similar situation appears, apply the lesson above.\n"
        "- Prefer existing fixed scripts and short evidence summaries over long chat/log replay.\n"
        "- Keep this draft under review before copying into a committed skill.\n"
    )
    path.write_text(text, encoding="utf-8")
    return path
