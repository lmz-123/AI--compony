"""Single console-scripts entry point for the `claudeteam` command.

Subcommands are registered in COMMANDS as `name → handler(argv)` pairs.  Each
handler returns an int exit code or None (treated as 0).  This module owns
the top-level dispatch, usage text, and process exit; subcommand modules
own their own argv parsing and side effects.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

from claudeteam.commands import (
    init, send, inbox, read, status, log, team, workspace,
    start, hire, fire, restart, up, down, reset, reidentify, switch,
    say, router, watchdog, task, teamctl, remember, recall, forget, peek,
    health, usage, install_hooks, version, feishu, monitor, doctor,
    radio, learn,
)
from claudeteam.util import error_exit


# Runtime type alias (evaluated at import) — keep it PEP 604-free so the module
# loads on Python 3.9 too: `int | None` would TypeError there. Annotations
# elsewhere are fine (they're strings under `from __future__ import annotations`).
CommandHandler = Callable[[list[str]], Optional[int]]


# Subcommand registry, structured as ordered (group_label, [(name, fn)…])
# pairs so `claudeteam --help` can render commands in semantic groups
# instead of a flat 26-line wall. Adding a new command:
# write a module under claudeteam.commands with `main(argv)`, then
# append the (name, fn) pair into the appropriate group below.
_COMMAND_GROUPS: list[tuple[str, list[tuple[str, CommandHandler]]]] = [
    ("bootstrap", [
        ("init", init.main),
    ]),
    ("local store I/O", [
        ("send", send.main),
        ("inbox", inbox.main),
        ("read", read.main),
        ("status", status.main),
        ("log", log.main),
        ("team", team.main),
        ("workspace", workspace.main),
        ("peek", peek.main),
        ("radio", radio.main),
    ]),
    ("team lifecycle", [
        ("start", start.main),
        ("hire", hire.main),
        ("fire", fire.main),
        ("restart", restart.main),
        ("up", up.main),
        ("down", down.main),
        ("team-shutdown", teamctl.shutdown_main),
        ("team-restart", teamctl.restart_main),
        ("reset", reset.main),
        ("reidentify", reidentify.main),
        ("switch", switch.main),
    ]),
    ("feishu transport", [
        ("feishu", feishu.main),
        ("say", say.main),
        ("router", router.main),
    ]),
    ("supervision", [
        ("watchdog", watchdog.main),
    ]),
    ("task tracking", [
        ("task", task.main),
    ]),
    ("durable agent memory", [
        ("remember", remember.main),
        ("recall", recall.main),
        ("forget", forget.main),
        ("learn", learn.main),
    ]),
    ("operational", [
        ("health", health.main),
        ("monitor", monitor.main),
        ("doctor", doctor.main),
        ("usage", usage.main),
        ("install-hooks", install_hooks.main),
        ("version", version.main),
    ]),
]

# Flat dict for fast dispatch. Built from _COMMAND_GROUPS so the two
# views can never drift — adding a command in one place automatically
# updates the other.
COMMANDS: dict[str, CommandHandler] = {
    name: fn for _, pairs in _COMMAND_GROUPS for name, fn in pairs
}


def _usage() -> str:
    lines = [
        "usage: claudeteam <command> [args...]",
        "",
        "commands:",
    ]
    for group_label, pairs in _COMMAND_GROUPS:
        lines.append(f"  [{group_label}]")
        for name, _ in pairs:
            lines.append(f"    {name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    # `-v`/`--version` are the flags operators reflexively try; alias them
    # to the `version` subcommand so they don't bounce off "unknown command".
    if args[0] in ("-v", "--version"):
        return int(version.main([]) or 0)
    cmd, rest = args[0], args[1:]
    handler = COMMANDS.get(cmd)
    if handler is None:
        return error_exit(f"unknown command: {cmd}\n\n{_usage()}")
    try:
        return int(handler(rest) or 0)
    except KeyboardInterrupt:
        # Ctrl-C from user; standard SIGINT exit code, no Python traceback
        print(file=sys.stderr)  # newline so the prompt doesn't glue to ^C
        return 130
    except Exception as e:
        # Friendly one-liner by default; full traceback when debugging.
        # Without this, every unhandled handler exception dumps a 30-line
        # traceback at the operator — useless for non-Python-fluent ops.
        import os
        if os.environ.get("CLAUDETEAM_DEBUG") == "1":
            raise
        return error_exit(
            f"❌ {cmd}: unhandled error: {type(e).__name__}: {e}\n"
            f"   set CLAUDETEAM_DEBUG=1 to see the full traceback")


if __name__ == "__main__":
    raise SystemExit(main())
