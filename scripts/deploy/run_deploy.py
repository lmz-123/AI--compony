#!/usr/bin/env python3
"""Run a guarded deployment defined in /data/deploy-targets.toml.

The deployer agent should only need to choose target / project / ref and read
back the result. All SSH, git, deploy, healthcheck, and rollback execution is
centralized here to reduce prompt variance and token spend.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover - py39/py310 fallback
    import tomli as tomllib  # type: ignore


DEFAULT_CONFIG_PATH = Path("/data/deploy-targets.toml")


class DeployError(RuntimeError):
    """Expected deployment failure with a user-facing message."""


@dataclass
class ProjectConfig:
    name: str
    repository: str
    directory: str
    branch: str
    deploy_command: str
    healthcheck_command: str
    rollback_command: str


@dataclass
class TargetConfig:
    name: str
    environment: str
    ssh_alias: str
    base_dir: str
    project: ProjectConfig


def _tail_summary(text: str, *, max_lines: int = 3, max_chars: int = 400) -> str:
    cleaned = [line.strip() for line in text.splitlines() if line.strip()]
    if not cleaned:
        return ""
    summary = " | ".join(cleaned[-max_lines:])
    return summary[-max_chars:]


def _load_config(config_path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeployError(f"Cannot read deploy target config: {config_path} is missing.") from exc
    except OSError as exc:
        raise DeployError(f"Cannot read deploy target config: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise DeployError(f"Deploy target config is invalid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise DeployError("Deploy target config root must be a TOML table.")
    return data


def _as_str(mapping: dict[str, Any], key: str, config_path: Path) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeployError(f"Missing or invalid `{key}` in {config_path}.")
    return value.strip()


def _resolve_target(data: dict[str, Any], target_name: str, project_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise DeployError("No deployment targets are configured.")
    for target in targets:
        if not isinstance(target, dict):
            continue
        if str(target.get("name", "")).strip() != target_name:
            continue
        projects = target.get("projects")
        if not isinstance(projects, list):
            raise DeployError(f"Target `{target_name}` has no `projects` list.")
        for project in projects:
            if not isinstance(project, dict):
                continue
            if str(project.get("name", "")).strip() == project_name:
                return target, project
        raise DeployError(f"Target `{target_name}` does not allow project `{project_name}`.")
    raise DeployError(f"Unknown deployment target `{target_name}`.")


def _build_target_config(raw: dict[str, Any], raw_project: dict[str, Any], config_path: Path) -> TargetConfig:
    project = ProjectConfig(
        name=_as_str(raw_project, "name", config_path),
        repository=_as_str(raw_project, "repository", config_path),
        directory=_as_str(raw_project, "directory", config_path),
        branch=_as_str(raw_project, "branch", config_path),
        deploy_command=_as_str(raw_project, "deploy_command", config_path),
        healthcheck_command=_as_str(raw_project, "healthcheck_command", config_path),
        rollback_command=_as_str(raw_project, "rollback_command", config_path),
    )
    return TargetConfig(
        name=_as_str(raw, "name", config_path),
        environment=_as_str(raw, "environment", config_path),
        ssh_alias=_as_str(raw, "ssh_alias", config_path),
        base_dir=_as_str(raw, "base_dir", config_path),
        project=project,
    )


def _run_ssh(alias: str, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = ["ssh", alias, "bash", "-s", "--", *args]
    return subprocess.run(
        cmd,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def _check_remote_target(cfg: TargetConfig) -> None:
    script = r"""
set -euo pipefail
repo_dir="$1"
expected_repo="$2"

if [ ! -d "$repo_dir/.git" ]; then
  echo "ERROR: repo_not_found:$repo_dir"
  exit 20
fi

actual_repo="$(git -C "$repo_dir" config --get remote.origin.url || true)"
if [ "$actual_repo" != "$expected_repo" ]; then
  echo "ERROR: repo_mismatch expected=$expected_repo actual=$actual_repo"
  exit 21
fi

git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null
git -C "$repo_dir" rev-parse HEAD
"""
    proc = _run_ssh(cfg.ssh_alias, script, cfg.project.directory, cfg.project.repository)
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout).strip() or "remote validation failed"
        raise DeployError(f"Remote target validation failed: {details}")


def _deploy_remote(cfg: TargetConfig, ref: str) -> dict[str, Any]:
    script = r"""
set -euo pipefail
repo_dir="$1"
ref="$2"
deploy_cmd="$3"
health_cmd="$4"
rollback_cmd_template="$5"
allowed_branch="$6"

tail_summary() {
  python3 - "$1" <<'PY'
import sys

text = sys.argv[1]
cleaned = [line.strip() for line in text.splitlines() if line.strip()]
if not cleaned:
    print("")
else:
    summary = " | ".join(cleaned[-3:])
    print(summary[-400:])
PY
}

cd "$repo_dir"

current_head="$(git rev-parse HEAD)"
git fetch --prune origin '+refs/heads/*:refs/remotes/origin/*' '+refs/tags/*:refs/tags/*'

resolved_ref="$(git rev-parse --verify --quiet "${ref}^{commit}" || true)"
if [ -z "$resolved_ref" ]; then
  resolved_ref="$(git rev-parse --verify --quiet "origin/${ref}^{commit}" || true)"
fi
if [ -z "$resolved_ref" ]; then
  echo "ERROR: unable to resolve ref ${ref}" >&2
  exit 30
fi

allowed_tip="$(git rev-parse --verify --quiet "origin/${allowed_branch}^{commit}" || true)"
if [ -z "$allowed_tip" ]; then
  echo "ERROR: unable to resolve allowed branch origin/${allowed_branch}" >&2
  exit 31
fi
if ! git merge-base --is-ancestor "$resolved_ref" "$allowed_tip"; then
  echo "ERROR: ref ${ref} (${resolved_ref}) is not contained in origin/${allowed_branch}" >&2
  exit 32
fi

git checkout --detach "$resolved_ref"

deploy_status=0
health_status=0
rollback_status=0
deploy_log=""
health_log=""
rollback_log=""

set +e
deploy_log="$(bash -lc "$deploy_cmd" 2>&1)"
deploy_status=$?
if [ "$deploy_status" -eq 0 ]; then
  health_log="$(bash -lc "$health_cmd" 2>&1)"
  health_status=$?
fi

if [ "$deploy_status" -ne 0 ] || [ "$health_status" -ne 0 ]; then
  rollback_cmd="${rollback_cmd_template//<previous-commit>/$current_head}"
  rollback_log="$(bash -lc "$rollback_cmd" 2>&1)"
  rollback_status=$?
fi
set -e

deploy_summary="$(tail_summary "$deploy_log")"
health_summary="$(tail_summary "$health_log")"
rollback_summary="$(tail_summary "$rollback_log")"

export DEPLOY_SUMMARY="$deploy_summary"
export HEALTH_SUMMARY="$health_summary"
export ROLLBACK_SUMMARY="$rollback_summary"

python3 - "$resolved_ref" "$current_head" "$deploy_status" "$health_status" "$rollback_status" <<'PY'
import json
import os
import sys

result = {
    "resolved_ref": sys.argv[1],
    "previous_head": sys.argv[2],
    "deploy_status": int(sys.argv[3]),
    "health_status": int(sys.argv[4]),
    "rollback_status": int(sys.argv[5]),
    "deploy_error_summary": os.environ.get("DEPLOY_SUMMARY", ""),
    "health_error_summary": os.environ.get("HEALTH_SUMMARY", ""),
    "rollback_error_summary": os.environ.get("ROLLBACK_SUMMARY", ""),
}
print(json.dumps(result, ensure_ascii=False))
PY
"""
    proc = subprocess.run(
        ["ssh", cfg.ssh_alias, "bash", "-s", "--",
         cfg.project.directory,
         ref,
         cfg.project.deploy_command,
         cfg.project.healthcheck_command,
         cfg.project.rollback_command,
         cfg.project.branch],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout).strip() or "remote deployment failed"
        raise DeployError(f"Deployment transport failed: {details}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        detail = proc.stdout.strip() or proc.stderr.strip() or "missing JSON result"
        raise DeployError(f"Deployment result parsing failed: {detail}") from exc


def _summarize(result: dict[str, Any], cfg: TargetConfig, requested_ref: str) -> dict[str, Any]:
    deploy_ok = int(result.get("deploy_status", 1)) == 0
    health_ok = int(result.get("health_status", 1)) == 0
    rollback_status = int(result.get("rollback_status", 0))
    rollback_attempted = not (deploy_ok and health_ok)
    success = deploy_ok and health_ok
    return {
        "target": cfg.name,
        "environment": cfg.environment,
        "project": cfg.project.name,
        "requested_ref": requested_ref,
        "resolved_ref": str(result.get("resolved_ref", "")).strip(),
        "previous_head": str(result.get("previous_head", "")).strip(),
        "deploy_ok": deploy_ok,
        "health_ok": health_ok,
        "rollback_attempted": rollback_attempted,
        "rollback_ok": rollback_status == 0 if rollback_attempted else False,
        "deploy_status": int(result.get("deploy_status", 1)),
        "health_status": int(result.get("health_status", 1)),
        "rollback_status": rollback_status,
        "deploy_command": cfg.project.deploy_command,
        "healthcheck_command": cfg.project.healthcheck_command,
        "rollback_command": cfg.project.rollback_command,
        "deploy_error_summary": _tail_summary(str(result.get("deploy_error_summary", ""))),
        "health_error_summary": _tail_summary(str(result.get("health_error_summary", ""))),
        "rollback_error_summary": _tail_summary(str(result.get("rollback_error_summary", ""))),
    }


def _print_text(summary: dict[str, Any]) -> None:
    print(f"target: {summary['target']} ({summary['environment']})")
    print(f"project: {summary['project']}")
    print(f"requested_ref: {summary['requested_ref']}")
    print(f"resolved_ref: {summary['resolved_ref']}")
    print(f"previous_head: {summary['previous_head']}")
    print(f"deploy_ok: {'yes' if summary['deploy_ok'] else 'no'}")
    print(f"health_ok: {'yes' if summary['health_ok'] else 'no'}")
    print(f"rollback_attempted: {'yes' if summary['rollback_attempted'] else 'no'}")
    if summary["rollback_attempted"]:
        print(f"rollback_ok: {'yes' if summary['rollback_ok'] else 'no'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute a guarded deploy from /data/deploy-targets.toml")
    parser.add_argument("--target", required=True, help="Target name from deploy-targets.toml")
    parser.add_argument("--project", required=True, help="Project name under the chosen target")
    parser.add_argument("--ref", required=True, help="Verified branch / tag / commit to deploy")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to deploy-targets.toml (defaults to /data/deploy-targets.toml)",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Required when the selected target is production and policy requires explicit approval.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    data = _load_config(config_path)
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    require_prod_approval = bool(policy.get("require_explicit_production_approval", False))
    raw_target, raw_project = _resolve_target(data, args.target, args.project)
    cfg = _build_target_config(raw_target, raw_project, config_path)

    if (
        require_prod_approval
        and cfg.environment.lower() == "production"
        and not args.allow_production
    ):
        raise DeployError(
            f"Target `{cfg.name}` is production. Re-run with --allow-production only after explicit approval."
        )

    _check_remote_target(cfg)
    result = _deploy_remote(cfg, args.ref)
    summary = _summarize(result, cfg, args.ref)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_text(summary)

    if summary["deploy_ok"] and summary["health_ok"]:
        return 0
    if summary["rollback_attempted"] and summary["rollback_ok"]:
        return 2
    return 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
