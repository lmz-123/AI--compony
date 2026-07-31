"""Tests for the Pi adapter (agents/pi_cli.py)."""
from __future__ import annotations

from claudeteam.agents import get_adapter
from claudeteam.agents.pi_cli import PiCliAdapter


def test_registered():
    assert isinstance(get_adapter("pi"), PiCliAdapter)
    assert isinstance(get_adapter("pi-cli"), PiCliAdapter)


def test_spawn_uses_env_provider_flags_and_no_config_file():
    cmd = PiCliAdapter().spawn_cmd("worker_pi", "deepseek-v4-pro")
    assert "pi --provider openai" in cmd            # default provider, env-overridable
    assert "--model deepseek-v4-pro" in cmd          # the team.json model
    assert '--api-key "$OPENAI_API_KEY"' in cmd      # key from env (agent_auth), not on disk
    assert "PI_AGENT=worker_pi" in cmd
    # no vendor hardcode
    assert "deepseek" not in cmd.replace("deepseek-v4-pro", "")
    # Pi needs no config file — flags only (unlike the other workers).
    assert "printf" not in cmd
    assert ".json" not in cmd and ".yaml" not in cmd


def test_spawn_isolates_home_per_agent():
    a = PiCliAdapter()
    assert a.spawn_cmd("aa", "m") != a.spawn_cmd("bb", "m")  # per-agent HOME


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert PiCliAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_submit_markers_process():
    a = PiCliAdapter()
    assert a.submit_keys()[0] == "Enter"
    assert a.ready_markers()
    assert a.process_name() == "pi"


def test_clear_and_compact_are_none():
    a = PiCliAdapter()
    assert a.clear_command() is None
    assert a.compact_command() is None
