"""Tests for the CodeWhale adapter (agents/codewhale_cli.py)."""
from __future__ import annotations

from claudeteam.agents import get_adapter
from claudeteam.agents.codewhale_cli import CodewhaleAdapter


def test_registered_under_codewhale_and_alias():
    a = get_adapter("codewhale")
    assert isinstance(a, CodewhaleAdapter)
    assert get_adapter("code-whale") is a


def test_spawn_provisions_auth_trust_then_launches():
    cmd = CodewhaleAdapter().spawn_cmd("worker_cw", "deepseek-v4-pro")
    # provisions ~/.codewhale/config.toml: provider (default openai) + key + trust
    assert "/.codewhale/config.toml" in cmd
    assert 'provider = "%s"' in cmd                  # provider is templated (env-driven)
    assert "openai" in cmd                           # default provider value passed as arg
    assert "$OPENAI_API_KEY" in cmd                 # key from operator env (agent_auth)
    assert '--base-url "$OPENAI_BASE_URL"' in cmd     # endpoint from env, not hardcoded
    assert 'trust_level = "trusted"' in cmd          # skips the trust onboarding step
    assert '[projects."%s"]' in cmd or "projects." in cmd
    assert "deepseek-v4-pro" in cmd                  # the team.json model
    # launches in unattended YOLO (auto-approve all tools) + skips the wizard
    assert "codewhale --yolo --skip-onboarding" in cmd
    assert "CODEWHALE_AGENT=worker_cw" in cmd


def test_spawn_is_env_driven_and_isolates_home():
    a = CodewhaleAdapter()
    assert "api.deepseek.com" not in a.spawn_cmd("w", "m")   # endpoint not hardcoded
    assert a.spawn_cmd("aa", "m") != a.spawn_cmd("bb", "m")  # per-agent HOME


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert CodewhaleAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_submit_enter_and_markers_process():
    a = CodewhaleAdapter()
    assert a.submit_keys()[0] == "Enter"
    assert a.ready_markers()
    assert a.process_name() == "codewhale"


def test_no_clear_or_compact_command():
    a = CodewhaleAdapter()
    # CodeWhale has no plain /clear or /compact — don't send invalid commands.
    assert a.clear_command() is None
    assert a.compact_command() is None
