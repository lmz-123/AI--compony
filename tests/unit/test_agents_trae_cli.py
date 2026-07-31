"""Tests for the Trae adapter (agents/trae_cli.py)."""
from __future__ import annotations

import json

from claudeteam.agents import get_adapter
from claudeteam.agents.trae_cli import TraeCliAdapter


def test_registered_with_alias():
    a = get_adapter("trae")
    assert isinstance(a, TraeCliAdapter)
    assert get_adapter("trae-cli") is a


def test_spawn_uses_openrouter_provider_and_simple_console():
    cmd = TraeCliAdapter().spawn_cmd("worker_tr", "deepseek-v4-flash")
    # the simple console is tmux-drivable (the Textual `rich` one is not)
    assert "--console-type simple" in cmd
    # secret passed via flag, not written to the config on disk
    assert '--api-key "$OPENAI_API_KEY"' in cmd
    # config MUST be .yaml — trae routes *.json to its legacy parser (crashes)
    assert ".trae_config.yaml" in cmd
    assert ".trae_config.json" not in cmd
    cfg = json.loads(cmd[cmd.index("{"):cmd.rindex("}") + 1])
    prov = cfg["model_providers"]["ds"]
    # provider selects the openai-compatible client (default openrouter, env-overridable)
    assert prov["provider"] == "openrouter"
    # base_url/api_key are placeholders in the config — real values via flags from env
    assert prov["base_url"] == "set-via-flag"
    assert '--model-base-url "$OPENAI_BASE_URL"' in cmd
    assert cfg["models"]["trae_agent_model"]["model"] == "deepseek-v4-flash"
    assert cfg["agents"]["trae_agent"]["enable_lakeview"] is False


def test_spawn_is_env_driven_and_isolates_home():
    a = TraeCliAdapter()
    assert "api.deepseek.com" not in a.spawn_cmd("w", "m")   # endpoint not hardcoded
    assert a.spawn_cmd("aa", "m") != a.spawn_cmd("bb", "m")  # per-agent HOME


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert TraeCliAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_submit_markers_process():
    a = TraeCliAdapter()
    assert a.submit_keys()[0] == "Enter"
    assert a.ready_markers()
    assert a.process_name() == "trae-cli"
