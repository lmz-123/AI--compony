#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


RUN_ASYNC = Path("/app/scripts/deploy/run_async_and_notify.py")


def launch_async(*, agent: str, notify: str, task_id: str, label: str,
                 command: list[str], workdir: str = "/app",
                 priority: str = "高") -> int:
    argv = [
        sys.executable,
        str(RUN_ASYNC),
        "--agent", agent,
        "--notify", notify,
        "--label", label,
        "--workdir", workdir,
        "--priority", priority,
    ]
    if task_id:
        argv += ["--task-id", task_id]
    argv += ["--", *command]
    proc = subprocess.run(argv, text=True, check=False)
    return proc.returncode
