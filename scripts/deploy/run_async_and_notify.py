#!/usr/bin/env python3
"""Launch a long-running command in the background, then notify a teammate.

Designed for deployer's slow fixed scripts (deploy / APK build). The starter
returns immediately with a job id + log path, while a detached child keeps
running the command and sends a single inbox message to manager when the job
finishes. This avoids wasting model turns staring at a long subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claudeteam.commands import send as send_cmd
from claudeteam.store import local_facts
from claudeteam.store import tasks


JOBS_DIR = Path("/data/state/async-jobs")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_id() -> str:
    return "job-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)


def _safe_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tail_lines(text: str, *, max_lines: int = 6, max_chars: int = 800) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    summary = " | ".join(lines[-max_lines:])
    return summary[-max_chars:]


def _parse_json_payload(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _summarize_payload(payload: dict[str, Any] | None, *, rc: int, log_path: Path) -> str:
    if not payload:
        return f"exit={rc}；日志：{log_path}"

    if "build_result" in payload and isinstance(payload["build_result"], dict):
        r = payload["build_result"]
        parts = [f"exit={rc}"]
        if r.get("download_url"):
            parts.append(f"下载链接：{r['download_url']}")
        if r.get("file"):
            parts.append(f"文件：{Path(str(r['file'])).name}")
        if r.get("sha256"):
            parts.append(f"SHA256={r['sha256']}")
        if r.get("workflow_url") and not r.get("download_url"):
            parts.append(f"workflow={r['workflow_url']}")
        parts.append(f"日志：{log_path}")
        return "；".join(parts)

    if "deploy_ok" in payload or "health_ok" in payload:
        parts = [
            f"exit={rc}",
            f"resolved_ref={payload.get('resolved_ref', '')}",
            f"deploy_ok={payload.get('deploy_ok')}",
            f"health_ok={payload.get('health_ok')}",
        ]
        if payload.get("rollback_attempted") is not None:
            parts.append(f"rollback_attempted={payload.get('rollback_attempted')}")
        for key in ("deploy_error_summary", "health_error_summary", "rollback_error_summary"):
            if payload.get(key):
                parts.append(f"{key}={payload[key]}")
        parts.append(f"日志：{log_path}")
        return "；".join(str(p) for p in parts if p not in ("", None))

    if "deploy" in payload and isinstance(payload["deploy"], dict):
        deploy = payload["deploy"]
        parts = [
            f"exit={rc}",
            f"published_ref={payload.get('published_ref', '')}",
            f"branch={payload.get('branch', '')}",
            f"deploy_ok={deploy.get('deploy_ok')}",
            f"health_ok={deploy.get('health_ok')}",
        ]
        for key in ("deploy_error_summary", "health_error_summary", "rollback_error_summary"):
            if deploy.get(key):
                parts.append(f"{key}={deploy[key]}")
        parts.append(f"日志：{log_path}")
        return "；".join(str(p) for p in parts if p not in ("", None))

    return f"exit={rc}；日志：{log_path}"


def _notify(recipient: str, sender: str, task_id: str, priority: str, message: str) -> None:
    cmd = ["claudeteam", "send", recipient, sender, message, priority]
    if task_id:
        cmd += ["--task", task_id]
    subprocess.run(cmd, text=True, capture_output=True, check=False)


def _mark_background(task_id: str, *, agent: str, note: str) -> bool:
    if not task_id:
        return False
    try:
        return tasks.background(task_id, note=note, by=agent)
    except Exception:
        return False


def _auto_dispatch_next(agent: str) -> dict[str, str]:
    """Best-effort backend auto-dispatch once a worker hands work to background.

    Returns a small result dict for status/log shaping:
      {"state": "claimed"|"empty"|"busy"|"send_failed", "task_id": "...", "title": "..."}
    """
    try:
        state, task = tasks.claim_next(agent, allow_busy=False)
    except Exception:
        return {"state": "error", "task_id": "", "title": ""}
    if state != "claimed" or not task:
        return {"state": state, "task_id": "", "title": ""}
    message = task.get("description") or task.get("title", "")
    if task.get("id") and task.get("title"):
        message = f"{task['id']} {task['title']}\n\n{message}".strip()
    rc = send_cmd.main([agent, "manager", message, "高", "--task", task["id"]])
    if rc != 0:
        try:
            tasks.update(task["id"], status="待处理")
        except Exception:
            pass
        return {"state": "send_failed", "task_id": task["id"], "title": task.get("title", "")}
    return {"state": "claimed", "task_id": task["id"], "title": task.get("title", "")}


def _start(args: argparse.Namespace) -> int:
    if not args.command:
        raise SystemExit("ERROR: missing command after --")
    job_id = _job_id()
    log_path = JOBS_DIR / f"{job_id}.log"
    meta_path = JOBS_DIR / f"{job_id}.json"
    payload = {
        "job_id": job_id,
        "agent": args.agent,
        "notify": args.notify,
        "task_id": args.task_id,
        "label": args.label,
        "priority": args.priority,
        "workdir": args.workdir,
        "command": args.command,
        "status": "queued",
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "log_path": str(log_path),
        "payload": None,
    }
    _safe_write_json(meta_path, payload)
    worker_cmd = [
        sys.executable,
        __file__,
        "--run-job",
        str(meta_path),
    ]
    proc = subprocess.Popen(
        worker_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    payload["worker_pid"] = proc.pid
    _safe_write_json(meta_path, payload)
    background_note = f"{args.label} job_id={job_id}"
    backgrounded = _mark_background(args.task_id, agent=args.agent, note=background_note)
    dispatch = {"state": "skipped", "task_id": "", "title": ""}
    if backgrounded:
        dispatch = _auto_dispatch_next(args.agent)
        task_text = f"{args.task_id} 已转后台中"
        if dispatch["state"] == "claimed":
            status_text = f"{dispatch['task_id']} {dispatch['title']}（{args.task_id} 后台中）".strip()
            log_text = f"{task_text}；自动续派 {dispatch['task_id']} {dispatch['title']}".strip()
        else:
            status_text = f"后台中：{args.task_id}"
            log_text = task_text
        local_facts.upsert_status(args.agent, "进行中", status_text)
        local_facts.append_log(args.agent, "task_background", log_text, ref=args.task_id)
    print(json.dumps({
        "job_id": job_id,
        "worker_pid": proc.pid,
        "status": "queued",
        "log_path": str(log_path),
        "meta_path": str(meta_path),
        "task_backgrounded": backgrounded,
        "auto_dispatch": dispatch,
    }, ensure_ascii=False))
    return 0


def _run_job(meta_path: Path) -> int:
    meta = _load_json(meta_path)
    log_path = Path(str(meta["log_path"]))
    cmd = [str(item) for item in meta.get("command", [])]
    workdir = str(meta.get("workdir") or "/app")
    meta["status"] = "running"
    meta["started_at"] = _now_iso()
    _safe_write_json(meta_path, meta)

    proc = subprocess.run(
        cmd,
        cwd=workdir,
        text=True,
        capture_output=True,
        check=False,
    )
    combined = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    log_path.write_text(combined, encoding="utf-8")

    payload = _parse_json_payload(proc.stdout or "")
    meta["status"] = "finished"
    meta["finished_at"] = _now_iso()
    meta["exit_code"] = proc.returncode
    meta["payload"] = payload
    _safe_write_json(meta_path, meta)

    summary = _summarize_payload(payload, rc=proc.returncode, log_path=log_path)
    title = f"【异步完成·{meta.get('label', '后台任务')}】"
    if proc.returncode == 0:
        message = f"{title} 成功。{summary}"
    else:
        fallback = _tail_lines(combined)
        extra = f"；错误摘要：{fallback}" if fallback else ""
        message = f"{title} 失败。{summary}{extra}"
    _notify(
        str(meta.get("notify") or "manager"),
        str(meta.get("agent") or "system"),
        str(meta.get("task_id") or ""),
        str(meta.get("priority") or "高"),
        message,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch a long-running command and notify on completion")
    parser.add_argument("--agent", default="deployer")
    parser.add_argument("--notify", default="manager")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--priority", default="高")
    parser.add_argument("--workdir", default="/app")
    parser.add_argument("--run-job", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.run_job:
        return _run_job(Path(args.run_job))

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    args.command = command
    if not args.label:
        args.label = "后台任务"
    return _start(args)


if __name__ == "__main__":
    raise SystemExit(main())
