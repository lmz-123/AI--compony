#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from scripts.deploy._async_launcher import launch_async  # type: ignore
from scripts.deploy.run_deploy import DEFAULT_CONFIG_PATH  # type: ignore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start a background deploy job")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--agent", default="deployer")
    parser.add_argument("--notify", default="manager")
    parser.add_argument("--priority", default="高")
    parser.add_argument("--target", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args(argv)

    cmd = [
        sys.executable,
        "/app/scripts/deploy/run_deploy.py",
        "--target", args.target,
        "--project", args.project,
        "--ref", args.ref,
        "--config", args.config,
        "--json",
    ]
    if args.allow_production:
        cmd.append("--allow-production")
    return launch_async(
        agent=args.agent,
        notify=args.notify,
        task_id=args.task_id,
        label=f"deploy {args.project}",
        command=cmd,
        priority=args.priority,
    )


if __name__ == "__main__":
    raise SystemExit(main())
