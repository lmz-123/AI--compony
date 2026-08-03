"""Tests for the async deploy/build notifier helper."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from helpers import isolated_env
from claudeteam.store import tasks
from claudeteam.store import local_facts

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "run_async_and_notify.py"
SPEC = importlib.util.spec_from_file_location("async_notify_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_parse_json_payload_accepts_full_stdout_json():
    payload = runner._parse_json_payload('{"hello":"world"}')
    assert payload == {"hello": "world"}


def test_parse_json_payload_accepts_last_json_line():
    payload = runner._parse_json_payload("workflow status: completed\n{\"build_result\":{\"download_url\":\"https://x\"}}")
    assert payload == {"build_result": {"download_url": "https://x"}}


def test_summarize_payload_prefers_download_url_for_build(tmp_path: Path):
    log_path = tmp_path / "job.log"
    summary = runner._summarize_payload(
        {
            "build_result": {
                "download_url": "https://download.example/app.apk",
                "file": "/data/artifacts/ct-1/app.apk",
                "sha256": "abc123",
                "workflow_url": "https://github.com/run/1",
            }
        },
        rc=0,
        log_path=log_path,
    )
    assert "https://download.example/app.apk" in summary
    assert "app.apk" in summary
    assert "abc123" in summary


def test_summarize_payload_keeps_deploy_health_fields(tmp_path: Path):
    log_path = tmp_path / "job.log"
    summary = runner._summarize_payload(
        {
            "deploy_ok": True,
            "health_ok": False,
            "resolved_ref": "abc",
            "rollback_attempted": True,
            "health_error_summary": "curl failed",
        },
        rc=2,
        log_path=log_path,
    )
    assert "deploy_ok=True" in summary
    assert "health_ok=False" in summary
    assert "curl failed" in summary


def test_start_backgrounds_task_and_notifies_manager(tmp_path: Path, monkeypatch):
    with isolated_env():
        tid = tasks.create("deployer", "build apk")
        tasks.update(tid, status="进行中")
        next_tid = tasks.create("deployer", "deploy next")
        monkeypatch.setattr(runner, "JOBS_DIR", tmp_path)

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 43210

        monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)

        args = SimpleNamespace(
            agent="deployer",
            notify="manager",
            task_id=tid,
            label="TripCanvas debug APK 构建",
            priority="高",
            workdir="/app",
            command=["python", "build.py"],
        )
        monkeypatch.setattr(runner.send_cmd, "main", lambda argv: 0)
        rc = runner._start(args)
        assert rc == 0
        task = tasks.get(tid)
        assert task["status"] == tasks.BACKGROUND_STATUS
        assert "job_id=" in task["background_note"]
        assert tasks.get(next_tid)["status"] == "进行中"
        snap = local_facts.get_status("deployer")
        assert next_tid in snap["task"]
        logs = local_facts.list_logs("deployer", limit=5)
        assert any("自动续派" in row["content"] for row in logs)
