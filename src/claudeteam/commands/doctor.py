"""Environment doctor for AI Company deployments.

Inspired by cloud-agent environment checks: keep common host/project problems
out of agent prompt space by turning them into a small structured report.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudeteam.runtime import config, paths, watchdog
from claudeteam.runtime.agent_auth import load_secrets
from claudeteam.util import maybe_print_help, now_ms, print_json


USAGE = "usage: claudeteam doctor [run] [--json] [--fix] [--scan-root PATH]"
DEFAULT_SCAN_ROOT = Path("/workspace/projects")
DEFAULT_ARTIFACT_DIR = Path("/data/artifacts")


@dataclass
class Check:
    id: str
    status: str
    summary: str
    detail: str = ""
    action: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
            "action": self.action,
        }


def _check(ok: bool, cid: str, summary: str, *, detail: str = "", action: str = "",
           warn: bool = False) -> Check:
    return Check(cid, "ok" if ok else ("warn" if warn else "fail"), summary, detail, action)


def _state_path() -> Path:
    return paths.state_dir() / "doctor-last.json"


def _port_open(host: str, port: int, *, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _find_backend_projects(scan_root: Path) -> list[Path]:
    if not scan_root.exists():
        return []
    found: list[Path] = []
    for root, dirs, files in os.walk(scan_root):
        current = Path(root)
        if "requirements-dev.txt" in files and current.name == "tripcanvas-backend":
            found.append(current)
        if ".git" in dirs:
            dirs.remove(".git")
        if ".venv" in dirs:
            dirs.remove(".venv")
    return sorted(found)


def _prepare_backend_env(scan_root: Path) -> tuple[bool, str]:
    script = Path("/app/scripts/dev/prepare_backend_env.py")
    if not script.exists():
        script = Path.cwd() / "scripts" / "dev" / "prepare_backend_env.py"
    if not script.exists():
        return False, "prepare_backend_env.py not found"
    proc = subprocess.run(
        [sys.executable, str(script), "--scan-root", str(scan_root)],
        text=True,
        capture_output=True,
        check=False,
    )
    output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return proc.returncode == 0, output[-1200:]


def _config_checks() -> list[Check]:
    team = config.load_team()
    agents = team.get("agents", {}) or {}
    return [
        _check(bool(agents), "team.roster", f"{len(agents)} agent(s) configured",
               action="check claudeteam.toml [team.agents]"),
        _check(bool(config.chat_id()), "feishu.chat_id", "chat_id configured",
               action="run claudeteam feishu connect or edit claudeteam.toml"),
        _check(paths.config_file().exists(), "config.file", str(paths.config_file()),
               action="ensure CLAUDETEAM_CONFIG_FILE points to claudeteam.toml"),
    ]


def _secret_checks() -> list[Check]:
    secrets = load_secrets()
    return [
        _check(bool(secrets.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")),
               "secrets.openai", "OPENAI_API_KEY available",
               action="add OPENAI_API_KEY to /data/state/.env"),
        _check(bool(secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")),
               "secrets.github", "GITHUB_TOKEN available",
               action="add GITHUB_TOKEN to /data/state/.env", warn=True),
    ]


def _project_checks(scan_root: Path, *, fix: bool) -> list[Check]:
    rows: list[Check] = []
    rows.append(_check(scan_root.exists(), "projects.root", str(scan_root),
                       action="mount projects to /workspace/projects"))
    projects = _find_backend_projects(scan_root)
    if not projects:
        rows.append(Check("projects.backend", "warn", "no tripcanvas-backend project found",
                          action="clone MyAPPs under /workspace/projects or /root/AI--compony/projects"))
        return rows
    warmed = 0
    stale = 0
    for project in projects:
        state = project / ".claudeteam-env-state.json"
        venv_py = project / ".venv" / "bin" / "python"
        if state.exists() and venv_py.exists():
            warmed += 1
        else:
            stale += 1
    rows.append(_check(stale == 0, "projects.backend_env",
                       f"{warmed} warmed, {stale} need prepare",
                       detail=", ".join(str(p) for p in projects[:5]),
                       action="run python /app/scripts/dev/prepare_backend_env.py --scan-root /workspace/projects",
                       warn=True))
    if fix and stale:
        ok, output = _prepare_backend_env(scan_root)
        rows.append(_check(ok, "projects.backend_env.fix", "prepare backend env",
                           detail=output, action="inspect prepare_backend_env output"))
    return rows


def _ssh_checks() -> list[Check]:
    ssh_dir = Path("/root/.ssh")
    key = ssh_dir / "deployer_ed25519"
    known = ssh_dir / "known_hosts"
    return [
        _check(ssh_dir.exists(), "ssh.dir", str(ssh_dir), action="mount server-secrets/ssh to /root/.ssh"),
        _check(key.exists(), "ssh.deployer_key", "deployer private key exists",
               action="place deployer_ed25519 in server-secrets/ssh", warn=True),
        _check(known.exists(), "ssh.known_hosts", "known_hosts exists",
               action="ssh-keyscan github.com and deployment host into server-secrets/ssh/known_hosts", warn=True),
    ]


def _artifact_checks(artifact_dir: Path) -> list[Check]:
    rows = [
        _check(artifact_dir.exists(), "artifacts.dir", str(artifact_dir),
               action="mount /srv/ai-company-artifacts to /data/artifacts"),
    ]
    if artifact_dir.exists():
        rows.append(_check(os.access(artifact_dir, os.R_OK | os.X_OK | os.W_OK),
                           "artifacts.permissions", "artifact dir readable/writable/executable",
                           action="chmod 755 /srv/ai-company-artifacts and fix ownership", warn=True))
    return rows


def _daemon_checks() -> list[Check]:
    rows: list[Check] = []
    for spec in watchdog.all_known_specs():
        pid = spec.pid_file.read_text().strip() if spec.pid_file.exists() else ""
        alive = watchdog.is_alive(spec) if pid else False
        rows.append(_check(alive, f"daemon.{spec.name}", f"{spec.name} {pid or 'missing'}",
                           action="restart container or run claudeteam up", warn=True))
    return rows


def _port_checks() -> list[Check]:
    ports = {
        "port.monitor": int(os.environ.get("CLAUDETEAM_MONITOR_PORT", "8765")),
        "port.admin": int(os.environ.get("CLAUDETEAM_ADMIN_PORT", "8766")),
        "port.artifacts": int(os.environ.get("CLAUDETEAM_ARTIFACT_PORT", "8081")),
    }
    rows = []
    for cid, port in ports.items():
        rows.append(_check(_port_open("127.0.0.1", port), cid, f"127.0.0.1:{port}",
                           action="check service/nginx listener and firewall", warn=True))
    return rows


def run_doctor(*, scan_root: Path = DEFAULT_SCAN_ROOT, artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
               fix: bool = False) -> dict[str, Any]:
    checks: list[Check] = []
    checks.extend(_config_checks())
    checks.extend(_secret_checks())
    checks.extend(_project_checks(scan_root, fix=fix))
    checks.extend(_ssh_checks())
    checks.extend(_artifact_checks(artifact_dir))
    checks.extend(_daemon_checks())
    checks.extend(_port_checks())

    counts = {
        "ok": sum(1 for c in checks if c.status == "ok"),
        "warn": sum(1 for c in checks if c.status == "warn"),
        "fail": sum(1 for c in checks if c.status == "fail"),
    }
    report = {
        "generated_at_ms": now_ms(),
        "ok": counts["fail"] == 0,
        "counts": counts,
        "checks": [c.to_dict() for c in checks],
    }
    try:
        paths.ensure_state_dir()
        _state_path().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    except OSError:
        pass
    return report


def _print_human(report: dict[str, Any]) -> None:
    counts = report["counts"]
    print(f"doctor: {counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail")
    for row in report["checks"]:
        mark = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}.get(row["status"], row["status"])
        print(f"{mark:4} {row['id']}: {row['summary']}")
        if row.get("detail") and row["status"] != "ok":
            print(f"     detail: {row['detail']}")
        if row.get("action") and row["status"] != "ok":
            print(f"     action: {row['action']}")


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if rest and rest[0] == "run":
        rest = rest[1:]
    parser = argparse.ArgumentParser(prog="claudeteam doctor run", add_help=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--scan-root", default=str(DEFAULT_SCAN_ROOT))
    parser.add_argument("--artifact-dir", default=str(DEFAULT_ARTIFACT_DIR))
    ns = parser.parse_args(rest)
    report = run_doctor(scan_root=Path(ns.scan_root), artifact_dir=Path(ns.artifact_dir),
                        fix=ns.fix)
    if ns.json:
        print_json(report)
    else:
        _print_human(report)
    return 0 if report["ok"] else 1
