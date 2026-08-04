"""Tests for `claudeteam doctor` environment checks."""
from __future__ import annotations

import json

from helpers import attr_patch, isolated_env, run_cli
from claudeteam.commands import doctor
from claudeteam.runtime import watchdog


def test_doctor_json_reports_config_and_writes_last_report(tmp_path):
    with isolated_env(team={"session": "S", "agents": {"manager": {"cli": "codex-cli"}}},
                      runtime_config={"chat_id": "oc_x"}) as env:
        rc, out, err = run_cli([
            "doctor", "run", "--json",
            "--scan-root", str(tmp_path / "missing-projects"),
            "--artifact-dir", str(tmp_path / "missing-artifacts"),
        ])
        assert rc == 1, err
        data = json.loads(out)
        assert data["counts"]["fail"] >= 1
        assert any(row["id"] == "team.roster" for row in data["checks"])
        assert (env / "state" / "doctor-last.json").exists()


def test_doctor_project_env_warns_when_backend_not_warmed(tmp_path):
    backend = tmp_path / "MyAPPs" / "tripcanvas-backend"
    backend.mkdir(parents=True)
    (backend / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    with isolated_env(team={"agents": {"manager": {"cli": "codex-cli"}}}):
        report = doctor.run_doctor(scan_root=tmp_path, artifact_dir=tmp_path)
    rows = {row["id"]: row for row in report["checks"]}
    assert rows["projects.backend_env"]["status"] == "warn"
    assert "need prepare" in rows["projects.backend_env"]["summary"]


def test_doctor_fix_invokes_prepare_backend_env(tmp_path):
    backend = tmp_path / "MyAPPs" / "tripcanvas-backend"
    backend.mkdir(parents=True)
    (backend / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    with isolated_env(team={"agents": {"manager": {"cli": "codex-cli"}}}):
        with attr_patch(doctor, _prepare_backend_env=lambda scan_root: (True, "prepared")):
            report = doctor.run_doctor(scan_root=tmp_path, artifact_dir=tmp_path, fix=True)
    rows = {row["id"]: row for row in report["checks"]}
    assert rows["projects.backend_env.fix"]["status"] == "ok"
    assert rows["projects.backend_env.fix"]["detail"] == "prepared"


def test_doctor_daemon_checks_can_be_ok(tmp_path):
    spec = watchdog.ProcessSpec(
        name="router",
        pid_file=tmp_path / "router.pid",
        expected_cmdline="claudeteam router",
        spawn_cmd=["claudeteam", "router"],
    )
    spec.pid_file.write_text("123\n", encoding="utf-8")
    with isolated_env(team={"agents": {"manager": {"cli": "codex-cli"}}}):
        with attr_patch(watchdog, all_known_specs=lambda: [spec], is_alive=lambda s: True):
            report = doctor.run_doctor(scan_root=tmp_path, artifact_dir=tmp_path)
    rows = {row["id"]: row for row in report["checks"]}
    assert rows["daemon.router"]["status"] == "ok"
