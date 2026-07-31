"""Tests for the Hermes adapter (agents/hermes_cli.py)."""
from __future__ import annotations

from claudeteam.agents import get_adapter
from claudeteam.agents.hermes_cli import HermesCliAdapter


def test_registered():
    assert isinstance(get_adapter("hermes"), HermesCliAdapter)


def test_spawn_provisions_config_and_env_then_launches():
    cmd = HermesCliAdapter().spawn_cmd("worker_hm", "deepseek-v4-pro")
    assert "/.hermes/config.yaml" in cmd
    assert "/.hermes/.env" in cmd
    assert "$OPENAI_API_KEY" in cmd            # key written to .env from env
    assert 'provider: "custom"' in cmd          # chat/completions (no /responses 404)
    assert "deepseek-v4-pro" in cmd
    assert cmd.rstrip().endswith("hermes")
    assert "HERMES_AGENT=worker_hm" in cmd


def test_spawn_is_env_driven_and_isolates_home():
    a = HermesCliAdapter()
    cmd = a.spawn_cmd("w", "m")
    assert "$OPENAI_BASE_URL" in cmd          # endpoint from env, not hardcoded
    assert "api.deepseek.com" not in cmd
    assert "m" in cmd                          # model from team.json
    assert a.spawn_cmd("aa", "m") != a.spawn_cmd("bb", "m")  # per-agent HOME


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert HermesCliAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_submit_markers_process():
    a = HermesCliAdapter()
    assert a.submit_keys()[0] == "Enter"
    assert a.ready_markers()
    assert a.process_name() == "hermes"


def test_clear_and_compact_are_hermes_specific():
    a = HermesCliAdapter()
    assert a.clear_command() == "/new"        # Hermes: /new, not /clear
    assert a.compact_command() == "/compress"  # Hermes: /compress, not /compact
