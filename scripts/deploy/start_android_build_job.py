#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from scripts.deploy._async_launcher import launch_async  # type: ignore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start a background Android build job")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--agent", default="deployer")
    parser.add_argument("--notify", default="manager")
    parser.add_argument("--priority", default="高")
    parser.add_argument("--config", default="/data/build-targets.toml")
    parser.add_argument("--target", required=True)
    parser.add_argument("--ref", default="")
    parser.add_argument("--build-type", choices=("debug", "release"), default="debug")
    parser.add_argument("--format", dest="artifact_format", choices=("apk", "aab"), default="apk")
    parser.add_argument("--api-base-url", default="")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--send-to-feishu", action="store_true")
    args = parser.parse_args(argv)

    cmd = [
        sys.executable,
        "/app/skills/build-tripcanvas-android/scripts/build_android.py",
        "--config", args.config,
        "--target", args.target,
        "--build-type", args.build_type,
        "--format", args.artifact_format,
        "--timeout", str(args.timeout),
        "--poll-seconds", str(args.poll_seconds),
        "--require-download-url",
    ]
    if args.ref:
        cmd += ["--ref", args.ref]
    if args.api_base_url:
        cmd += ["--api-base-url", args.api_base_url]
    if args.send_to_feishu:
        cmd.append("--send-to-feishu")
    return launch_async(
        agent=args.agent,
        notify=args.notify,
        task_id=args.task_id,
        label=f"android-build {args.target}",
        command=cmd,
        priority=args.priority,
    )


if __name__ == "__main__":
    raise SystemExit(main())
