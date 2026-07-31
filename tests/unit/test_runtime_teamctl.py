"""Tests for runtime/teamctl.py — safety gates + detached spawn + notify."""
from __future__ import annotations

import subprocess

from helpers import attr_patch, env_patch, isolated_env
from claudeteam.runtime import teamctl, tunables
from claudeteam.feishu import chat as chat_mod


# ── default-deny safety gates ──────────────────────────────────────


def test_lifecycle_and_login_disabled_by_default():
    """No opt-in in config → both chat-control surfaces are inert. This is
    the live-maintenance-team posture: never opted in, never controllable."""
    with isolated_env():
        assert teamctl.lifecycle_slash_enabled() is False
        assert teamctl.login_slash_enabled() is False


def test_flags_are_independent_via_toml():
    """The two flags are separate so /shutdown+/restart can be enabled
    while /login stays off (creds-not-isolated posture)."""
    with isolated_env() as tmp:
        toml = tmp / "controls.toml"
        toml.write_text("[controls]\nallow_lifecycle_slash = true\n",
                        encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml)):
            tunables.reset_cache()
            assert teamctl.lifecycle_slash_enabled() is True
            assert teamctl.login_slash_enabled() is False   # NOT enabled


def test_login_flag_enables_only_login():
    with isolated_env() as tmp:
        toml = tmp / "controls.toml"
        toml.write_text("[controls]\nallow_login_slash = true\n",
                        encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml)):
            tunables.reset_cache()
            assert teamctl.login_slash_enabled() is True
            assert teamctl.lifecycle_slash_enabled() is False


# ── detached spawn ─────────────────────────────────────────────────


def test_spawn_detached_uses_new_session_and_detached_stdio():
    """The runner must outlive `down` killing the router → its own session,
    stdio fully detached (it reports via notify, not stdout)."""
    captured = {}

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return None

    teamctl.spawn_detached(["team-restart"], popen=fake_popen)
    assert captured["argv"][-1] == "team-restart"
    assert "claudeteam.cli" in captured["argv"]      # invoked via module
    assert captured["kw"]["start_new_session"] is True
    assert captured["kw"]["stdout"] is subprocess.DEVNULL
    assert captured["kw"]["stderr"] is subprocess.DEVNULL
    assert captured["kw"]["stdin"] is subprocess.DEVNULL


# ── completion notify (best-effort) ────────────────────────────────


def test_notify_swallows_send_errors():
    """A failed completion card must never look like a failed lifecycle op."""
    with isolated_env(runtime_config={"chat_id": "oc_test"}):
        def boom(*a, **kw):
            raise RuntimeError("lark down")
        with attr_patch(chat_mod, send_card=boom):
            teamctl.notify({"any": "card"})   # must not raise


def test_notify_noops_without_chat_id():
    """No chat_id configured → nothing sent, no error."""
    sent = []
    with isolated_env():   # no runtime_config → chat_id unset
        with attr_patch(chat_mod, send_card=lambda *a, **kw: sent.append(a)):
            teamctl.notify({"any": "card"})
        assert sent == []


# ── /login per-CLI isolation allowlist ─────────────────────────────


def test_login_allowed_clis_default_is_isolated_set():
    """Default allowlist = the host-isolated CLIs (claude-code,
    codex-cli). kimi (shared ~/.kimi) is NOT in it."""
    with isolated_env():
        allowed = teamctl.login_allowed_clis()
        assert "claude-code" in allowed
        assert "codex-cli" in allowed
        assert "kimi-code" not in allowed


def test_login_allowed_clis_tunable_override():
    with isolated_env() as tmp:
        toml = tmp / "controls.toml"
        toml.write_text('[controls]\nlogin_allowed_clis = "claude-code,kimi-code"\n',
                        encoding="utf-8")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml)):
            tunables.reset_cache()
            allowed = teamctl.login_allowed_clis()
            assert allowed == frozenset({"claude-code", "kimi-code"})
            assert "codex-cli" not in allowed   # replaced, not merged
