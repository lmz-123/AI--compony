"""`claudeteam hire <agent>`

Add a single agent to a running team: create the tmux window, spawn its
CLI, mark status.  Errors out if the team isn't running yet (use
`claudeteam start` first).

Doubles as the rehire path for a fired agent: if `agent` isn't in the
roster but `runtime/archive.py` has an archive for it (left by
`claudeteam fire`), hire restores the stashed roster entry + moves the
archived workspace back, then provisions normally — "被裁的人能从归档恢复".
"""
from __future__ import annotations

from claudeteam.runtime import archive, config, lifecycle, tmux
from claudeteam.util import error_exit, maybe_print_help, usage_error, warn


USAGE = "usage: claudeteam hire <agent>"


def _rehire_from_archive(agent: str) -> tuple[dict | None, str]:
    """If `agent` has an archive, re-add its roster entry + restore its
    workspace. Returns (cfg, message); cfg is None when there's nothing to
    restore (caller then reports 'unknown agent').

    Prefers the VERBATIM `_roster.toml` block (byte-faithful restore — keeps
    alignment / in-block comments / multi-line values, immune to
    dict-reserialization corruption). Falls back to the
    `_roster.json` dict when there's no block stash (legacy archive, or a
    non-toml deployment)."""
    arc = archive.find_archived(agent)
    if arc is None:
        return None, ""
    block = archive.read_roster_block(arc)
    ok, msg = (config.restore_agent_block(agent, block) if block else (False, ""))
    if not ok:  # no block, or toml isn't the active source → dict fallback
        cfg = archive.read_roster_stash(arc) or {"cli": "claude-code"}
        ok, msg = config.add_agent(agent, cfg)
    if not ok:
        return None, f"archive found but roster re-add failed: {msg}"
    archive.restore_workspace(arc, agent)
    return config.agent_config(agent), f"rehired from archive ({msg})"


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if len(argv) < 1:
        return usage_error(USAGE)
    agent = argv[0]

    try:
        cfg = config.agent_config(agent)
    except KeyError:
        cfg, note = _rehire_from_archive(agent)
        if cfg is None:
            tail = f": {note}" if note else " (not in roster, no archive to restore)"
            return error_exit(f"❌ cannot hire {agent}{tail}")
        print(f"♻️  {note}")
    cli = cfg.get("cli", "claude-code")

    session = config.session_name()
    if not tmux.has_session(session):
        return error_exit(
            f"❌ tmux session {session} not running; run `claudeteam start` first")

    target = tmux.Target(session, agent)
    if tmux.has_window(target):
        print(f"⚠️  {agent} already has a pane")
        return 0
    if not tmux.new_window(target):
        return error_exit(f"❌ failed to create window for {agent}")

    outcome = lifecycle.provision_pane(agent, target)
    if outcome == lifecycle.LAZY:
        print(f"✅ hired (lazy): {agent} ({cli}) → {target}")
        return 0
    if outcome == lifecycle.CONFIG_ERROR:
        return error_exit(
            f"❌ {agent}: bad cli config in claudeteam.toml (see warning above); "
            f"hire aborted, fix claudeteam.toml and retry")
    if outcome == lifecycle.SPAWN_FAILED:
        return error_exit(f"❌ failed to spawn CLI in {agent} pane")
    if outcome == lifecycle.READY_NO_INIT:
        warn(f"⚠️  {agent} CLI didn't show ready marker in time; "
             f"identity init prompt skipped")
    print(f"✅ hired: {agent} ({cli}) → {target}")
    return 0
