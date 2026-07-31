"""Tests for `claudeteam init` — first-time bootstrap.

Post-config-unification: writes a single `claudeteam.toml` instead of
team.json + runtime_config.json. `--upgrade` reads existing legacy
files and merges them into a toml (legacy left as backup).
"""
from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from helpers import env_patch, isolated_env, run_cli


def _tmp_env():
    """isolated_env points CLAUDETEAM_CONFIG_FILE at a fresh tempdir's
    claudeteam.toml that doesn't exist yet (init writes it)."""
    return isolated_env()


def _read_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ── happy path ───────────────────────────────────────────────────


def test_init_writes_toml_and_returns_zero():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, out, _ = run_cli(["init"])
        assert rc == 0
        assert (tmp / "claudeteam.toml").exists()
        assert "wrote" in out
        assert "Next:" in out


def test_init_default_team_is_claude_only():
    """Default team needs only claude-code so a fresh user with just Claude
    installed gets a working team out of the box (agents reuse the local Claude
    login). codex / other-CLI workers are a commented example to uncomment when
    you have them — "用户有什么用什么", optional, not required."""
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        agents = cfg["team"]["agents"]
        assert set(agents) == {"manager", "worker_cc"}
        assert all(a["cli"] == "claude-code" for a in agents.values())


def test_init_emits_chat_publish_section():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["chat"]["publish"]["user_to_manager"] == "always"
        # defaults flipped to True/always so more chat is visible during
        # testing — see init.py header comment.
        assert cfg["chat"]["publish"]["manager_to_worker"] is True
        assert cfg["chat"]["publish"]["worker_to_manager"] is True
        assert cfg["chat"]["publish"]["worker_to_worker"] is True


def test_init_session_flag_overrides_default():
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init", "--session", "Alpha"])
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["team"]["session"] == "Alpha"


def test_init_default_session_is_per_team_derived():
    """Without --session, the written session derives from the config's
    location (not a bare "ClaudeTeam") so two teams on one host don't collide
    on one tmux session / cross-kill each other's panes."""
    with _tmp_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            run_cli(["init"])
        sess = _read_toml(tmp / "claudeteam.toml")["team"]["session"]
        assert sess.startswith("ClaudeTeam-") and sess != "ClaudeTeam"


# ── overwrite protection ─────────────────────────────────────────


def test_init_refuses_to_overwrite_existing_toml():
    with _tmp_env() as tmp:
        toml_path = tmp / "claudeteam.toml"
        toml_path.write_text('chat_id = "preserved"\n', encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml_path)):
            rc, _, err = run_cli(["init"])
        assert rc == 1
        assert "already exists" in err
        # untouched
        assert "preserved" in toml_path.read_text(encoding="utf-8")


def test_init_force_overwrites():
    with _tmp_env() as tmp:
        toml_path = tmp / "claudeteam.toml"
        toml_path.write_text('chat_id = "old"\n', encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml_path)):
            rc, _, _ = run_cli(["init", "--force"])
        assert rc == 0
        cfg = _read_toml(toml_path)
        assert cfg["chat_id"] == ""
        assert "manager" in cfg["team"]["agents"]


# ── --upgrade migration ──────────────────────────────────────────


def test_upgrade_merges_legacy_team_json():
    """Existing team.json + runtime_config.json → claudeteam.toml with
    the legacy team's agents, chat_id, profile preserved."""
    legacy_team = {
        "session": "OldTeam",
        "agents": {
            "boss": {"cli": "claude-code", "model": "opus", "role": "老大"},
            "alice": {"cli": "codex-cli", "model": "gpt-5.5", "role": "数据"},
        },
        "default_model": "sonnet",
    }
    legacy_runtime = {"chat_id": "oc_legacy", "lark_profile": "old-profile"}
    with isolated_env(team=legacy_team, runtime_config=legacy_runtime) as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, out, _ = run_cli(["init", "--upgrade"])
        assert rc == 0, out
        cfg = _read_toml(tmp / "claudeteam.toml")
        assert cfg["chat_id"] == "oc_legacy"
        assert cfg["lark_profile"] == "old-profile"
        assert cfg["default_model"] == "sonnet"
        assert cfg["team"]["session"] == "OldTeam"
        assert set(cfg["team"]["agents"]) == {"boss", "alice"}
        assert cfg["team"]["agents"]["alice"]["cli"] == "codex-cli"


def test_upgrade_errors_when_no_legacy_files():
    with isolated_env() as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, _, err = run_cli(["init", "--upgrade"])
        assert rc == 1
        assert "nothing to migrate" in err


def test_upgrade_emits_legacy_preserved_note():
    legacy_team = {"session": "X", "agents": {"a": {"cli": "claude-code"}}}
    legacy_runtime = {"chat_id": "oc_x"}
    with isolated_env(team=legacy_team, runtime_config=legacy_runtime) as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            rc, out, _ = run_cli(["init", "--upgrade"])
        assert rc == 0
        assert "preserved as backup" in out


# ── help / arg validation ────────────────────────────────────────


def test_init_unknown_arg_returns_one():
    with _tmp_env():
        rc, _, err = run_cli(["init", "--bogus"])
        assert rc == 1
        assert "unexpected args" in err


# ── template self-check ──────────────────────────────────────────


# ── first-run hook: init drives `feishu connect` (replaces bot-creator) ───────


def test_init_default_runs_feishu_connect():
    """On an interactive TTY, fresh init with no creds drops into the QR
    registration automatically. The harness has no real terminal, so the
    `_should_autoconnect` TTY gate is patched True to exercise the path."""
    from helpers import attr_patch
    from claudeteam.commands import feishu, init
    called = []
    with isolated_env(), env_patch(FEISHU_APP_ID=None), \
            attr_patch(init, _should_autoconnect=lambda *a: True), \
            attr_patch(feishu, main=lambda argv: called.append(list(argv)) or 0):
        rc, out, _ = run_cli(["init"])
    assert rc == 0
    assert called == [["connect"]]


def test_init_quick_passes_quick_to_feishu_connect():
    """`init --quick` routes the one-scan PersonalAgent device flow: it must
    pass `--quick` through to `feishu connect` (the convenient QR path)."""
    from helpers import attr_patch
    from claudeteam.commands import feishu, init
    called = []
    with isolated_env(), env_patch(FEISHU_APP_ID=None), \
            attr_patch(init, _should_autoconnect=lambda *a: True), \
            attr_patch(feishu, main=lambda argv: called.append(list(argv)) or 0):
        rc, out, _ = run_cli(["init", "--quick"])
    assert rc == 0
    assert called == [["connect", "--quick"]]


def test_init_no_connect_skips_registration_and_prints_step():
    """--no-connect (CI / scripted) must NOT launch the interactive scan, but
    still tells the operator the command to run later."""
    from helpers import attr_patch
    from claudeteam.commands import feishu
    called = []
    with isolated_env(), env_patch(FEISHU_APP_ID=None), \
            attr_patch(feishu, main=lambda argv: called.append(list(argv)) or 0):
        rc, out, _ = run_cli(["init", "--no-connect"])
    assert rc == 0
    assert called == []
    assert "feishu connect" in out


def test_init_skips_connect_when_creds_already_present():
    from helpers import attr_patch
    from claudeteam.commands import feishu
    called = []
    with isolated_env(), env_patch(FEISHU_APP_ID="cli_existing"), \
            attr_patch(feishu, main=lambda argv: called.append(list(argv)) or 0):
        rc, _, _ = run_cli(["init"])
    assert rc == 0
    assert called == []
