"""Tests for `claudeteam install-hooks` — Claude Code slash-command markdowns."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from helpers import isolated_env, run_cli, tmux_patch


# ── happy path ──────────────────────────────────────────────────


def test_install_hooks_creates_md_per_command():
    with tempfile.TemporaryDirectory() as tmp:
        rc, out, _ = run_cli(["install-hooks", tmp])
        assert rc == 0

        cmds_dir = Path(tmp) / ".claude" / "commands"
        assert cmds_dir.exists()
        # remember/recall and peek keep slash dispatch consistent with
        # the manager identity's 巡视 cadence (was hard-coded raw tmux
        # capture-pane).
        for name in ("inbox", "team", "status", "say", "task", "health",
                     "remember", "recall", "peek"):
            assert (cmds_dir / f"{name}.md").exists(), f"missing {name}.md"
        assert "wrote 9 slash command" in out


def test_install_hooks_remember_md_documents_kind_vocabulary():
    """The remember hook must teach which `kind` values are convention
    so agents don't invent free-form labels (still works but breaks
    cross-agent consistency for the boss reading recall output)."""
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        body = (Path(tmp) / ".claude" / "commands" / "remember.md").read_text(
            encoding="utf-8")
        for kind in ("task_assigned", "task_completed", "learning",
                     "blocker", "decision", "note"):
            assert kind in body
        assert "claudeteam remember" in body


def test_install_hooks_say_md_documents_card_only_after_R169():
    """Removed --no-card escape hatch — every chat message is a card.
    The hook doc no longer mentions any plain-text path so claude
    agents don't try to opt out."""
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        body = (Path(tmp) / ".claude" / "commands" / "say.md").read_text(
            encoding="utf-8")
        # Card-only messaging surfaced
        assert "v2 card" in body
        assert "--no-card" not in body  # escape hatch gone
        # Invocation form documented
        assert "claudeteam say <your-name>" in body
        # Threading caveat surfaced
        assert "thread" in body.lower() or "ignored" in body.lower()


def test_install_hooks_idempotent_overwrites_existing_files():
    with tempfile.TemporaryDirectory() as tmp:
        run_cli(["install-hooks", tmp])
        # tweak one to test overwrite
        team_path = Path(tmp) / ".claude" / "commands" / "team.md"
        team_path.write_text("STALE", encoding="utf-8")

        rc, out, _ = run_cli(["install-hooks", tmp])
        assert rc == 0
        assert "overwritten" in out
        assert "STALE" not in team_path.read_text(encoding="utf-8")


# ── parsing ──────────────────────────────────────────────────────


def test_install_hooks_too_many_args_returns_one():
    rc, _, err = run_cli(["install-hooks", "/a", "/b"])
    assert rc == 1
    assert "usage:" in err


# ── pane-staleness warning ────────────────────────────────────────


def test_install_hooks_warns_when_session_already_running():
    """REGRESSION: running install-hooks AFTER \`claudeteam up\` is the
    wrong order; existing claude-code panes have already cached their
    slash commands and won't pick up the new files. install-hooks should
    warn loudly."""
    team = {"session": "ClaudeTeam", "agents": {"manager": {}}}
    with isolated_env(team=team) as tmp, \
            tmux_patch(has_session=lambda s: s == "ClaudeTeam"):
        rc, _, err = run_cli(["install-hooks", str(tmp)])
        assert rc == 0
        # warning lands on stderr (via util.warn)
        assert "tmux session 'ClaudeTeam' is already running" in err
        assert "claudeteam down && claudeteam up" in err


def test_install_hooks_silent_when_no_session():
    team = {"session": "ClaudeTeam", "agents": {"manager": {}}}
    with isolated_env(team=team) as tmp, \
            tmux_patch(has_session=lambda s: False):
        rc, _, err = run_cli(["install-hooks", str(tmp)])
        assert rc == 0
        assert "already running" not in err
