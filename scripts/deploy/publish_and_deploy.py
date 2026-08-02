#!/usr/bin/env python3
"""Publish a local project worktree, then deploy the pushed ref.

This keeps `deployer` on a single deterministic path:
1. validate local worktree under projects_root
2. commit tracked/untracked changes with an explicit message
3. push the chosen branch
4. deploy the pushed ref through run_deploy.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.deploy.run_deploy import (  # type: ignore
    DEFAULT_CONFIG_PATH,
    DeployError,
    _build_target_config,
    _load_config,
    _resolve_target,
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=False)


def _git_output(cmd: list[str], cwd: Path) -> str:
    proc = _run(cmd, cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or "git command failed"
        raise DeployError(detail)
    return proc.stdout.strip()


def _projects_root(data: dict[str, Any], config_path: Path) -> Path:
    policy = data.get("policy")
    if not isinstance(policy, dict):
        raise DeployError(f"Missing [policy] in {config_path}.")
    root = policy.get("projects_root")
    if not isinstance(root, str) or not root.strip():
        raise DeployError(f"Missing policy.projects_root in {config_path}.")
    return Path(root.strip())


def _local_project_root(projects_root: Path, project_name: str) -> Path:
    root = projects_root / project_name
    if not root.is_dir():
        raise DeployError(f"Local project directory not found: {root}")
    if not (root / ".git").exists():
        raise DeployError(f"Local project directory is not a git repository: {root}")
    return root


def _ensure_branch(cwd: Path, branch: str) -> None:
    current = _git_output(["git", "branch", "--show-current"], cwd)
    if current == branch:
        return
    proc = _run(["git", "checkout", branch], cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"cannot checkout {branch}"
        raise DeployError(detail)


def _worktree_status(cwd: Path) -> str:
    return _git_output(["git", "status", "--porcelain=v1"], cwd)


def _publish_local_changes(cwd: Path, branch: str, message: str) -> dict[str, Any]:
    _ensure_branch(cwd, branch)
    status_before = _worktree_status(cwd)
    if not status_before:
        pushed_ref = _git_output(["git", "rev-parse", "HEAD"], cwd)
        return {
            "published": False,
            "branch": branch,
            "commit": pushed_ref,
            "status_summary": "no local changes",
        }

    add_proc = _run(["git", "add", "-A"], cwd)
    if add_proc.returncode != 0:
        raise DeployError((add_proc.stderr or add_proc.stdout).strip() or "git add failed")

    commit_proc = _run(["git", "commit", "-m", message], cwd)
    if commit_proc.returncode != 0:
        raise DeployError((commit_proc.stderr or commit_proc.stdout).strip() or "git commit failed")

    pushed_ref = _git_output(["git", "rev-parse", "HEAD"], cwd)
    push_proc = _run(["git", "push", "origin", f"HEAD:{branch}"], cwd)
    if push_proc.returncode != 0:
        raise DeployError((push_proc.stderr or push_proc.stdout).strip() or "git push failed")

    return {
        "published": True,
        "branch": branch,
        "commit": pushed_ref,
        "status_summary": status_before,
    }


def _run_deploy_subprocess(config_path: Path, target: str, project: str, ref: str, allow_production: bool) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "/app/scripts/deploy/run_deploy.py",
        "--config",
        str(config_path),
        "--target",
        target,
        "--project",
        project,
        "--ref",
        ref,
        "--json",
    ]
    if allow_production:
        cmd.append("--allow-production")
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode not in (0, 2, 3):
        detail = (proc.stderr or proc.stdout).strip() or "deploy subprocess failed"
        raise DeployError(detail)
    try:
        payload = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        detail = proc.stdout.strip() or proc.stderr.strip() or "invalid deploy JSON"
        raise DeployError(detail) from exc
    payload["deploy_exit_code"] = proc.returncode
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Commit/push local changes, then deploy the pushed ref")
    parser.add_argument("--target", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--message", required=True, help="Git commit message for the publish step")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    data = _load_config(config_path)
    raw_target, raw_project = _resolve_target(data, args.target, args.project)
    cfg = _build_target_config(raw_target, raw_project, config_path)
    local_root = _local_project_root(_projects_root(data, config_path), args.project)
    publish = _publish_local_changes(local_root, cfg.project.branch, args.message)
    deploy = _run_deploy_subprocess(config_path, args.target, args.project, publish["commit"], args.allow_production)

    result = {
        "target": args.target,
        "project": args.project,
        "local_project_root": str(local_root),
        "published": publish["published"],
        "branch": publish["branch"],
        "published_ref": publish["commit"],
        "status_summary": publish["status_summary"],
        "deploy": deploy,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(deploy.get("deploy_exit_code", 1))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
