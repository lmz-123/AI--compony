#!/usr/bin/env python3
"""Prepare and reuse Python backend virtualenvs under /workspace/projects.

This keeps dependency setup deterministic and moves pip/network churn out of
agent prompt space. It is safe to run repeatedly: when the requirements hash
matches, the script exits without reinstalling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
STATE_FILENAME = ".claudeteam-env-state.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _state_path(project_root: Path) -> Path:
    return project_root / STATE_FILENAME


def _load_state(project_root: Path) -> dict[str, str]:
    path = _state_path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(project_root: Path, state: dict[str, str]) -> None:
    _state_path(project_root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _venv_python(project_root: Path) -> Path:
    return project_root / ".venv" / "bin" / "python"


def _prepare_one(project_root: Path, index_url: str, timeout: str) -> str:
    req = project_root / "requirements-dev.txt"
    if not req.exists():
        return f"skip: {project_root} has no requirements-dev.txt"

    venv_python = _venv_python(project_root)
    state = _load_state(project_root)
    req_hash = _sha256(req)
    expected = {
        "requirements_dev_sha256": req_hash,
        "index_url": index_url,
    }
    if venv_python.exists() and all(state.get(k) == v for k, v in expected.items()):
        return f"ok: {project_root} already warmed"

    env = os.environ.copy()
    env.setdefault("PIP_INDEX_URL", index_url)
    env.setdefault("PIP_TIMEOUT", timeout)

    if not venv_python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(project_root / ".venv")], check=True, env=env)

    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        check=True,
        env=env,
    )
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(req)],
        check=True,
        env=env,
    )

    _save_state(project_root, expected)
    return f"prepared: {project_root}"


def _discover_projects(scan_root: Path) -> list[Path]:
    found: list[Path] = []
    if not scan_root.exists():
        return found
    for child in sorted(scan_root.iterdir()):
        candidate = child / "tripcanvas-backend"
        if candidate.is_dir():
            found.append(candidate)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Warm Python backend virtualenvs for AI Company")
    parser.add_argument("--project-root", action="append", default=[], help="Backend project root containing requirements-dev.txt")
    parser.add_argument("--scan-root", default="/workspace/projects", help="Root to scan for */tripcanvas-backend")
    parser.add_argument("--index-url", default=os.environ.get("PIP_INDEX_URL", DEFAULT_INDEX_URL))
    parser.add_argument("--timeout", default=os.environ.get("PIP_TIMEOUT", "120"))
    args = parser.parse_args(argv)

    projects = [Path(p).resolve() for p in args.project_root]
    if not projects:
        projects = _discover_projects(Path(args.scan_root))
    if not projects:
        print("skip: no backend projects found")
        return 0

    failed = False
    for project in projects:
        try:
            print(_prepare_one(project, args.index_url, args.timeout))
        except subprocess.CalledProcessError as exc:
            failed = True
            print(f"error: {project} prepare failed with exit code {exc.returncode}", file=sys.stderr)
        except Exception as exc:  # pragma: no cover - defensive
            failed = True
            print(f"error: {project} {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
