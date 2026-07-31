"""Tests for the opencode adapter (agents/opencode_cli.py)."""
from __future__ import annotations

import json

from claudeteam.agents import get_adapter
from claudeteam.agents.opencode_cli import OpencodeAdapter


def test_registered():
    assert isinstance(get_adapter("opencode"), OpencodeAdapter)


def test_spawn_writes_valid_json_config_then_launches():
    cmd = OpencodeAdapter().spawn_cmd("worker_oc", "deepseek-v4-pro")
    assert "/.config/opencode/opencode.json" in cmd
    assert cmd.rstrip().endswith("opencode")
    assert "OPENCODE_AGENT=worker_oc" in cmd
    # the embedded config is valid JSON with env-substituted endpoint + the model
    start, end = cmd.index("{"), cmd.rindex("}") + 1
    cfg = json.loads(cmd[start:end])
    # neutral provider id (NOT a vendor name); model is the one from team.json
    assert cfg["model"] == "compat/deepseek-v4-pro"
    prov = cfg["provider"]["compat"]
    assert prov["options"]["baseURL"] == "{env:OPENAI_BASE_URL}"
    assert prov["options"]["apiKey"] == "{env:OPENAI_API_KEY}"
    assert "deepseek-v4-pro" in prov["models"]


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert OpencodeAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_provider_not_named_openai():
    # naming the provider "openai" makes opencode use the /responses API,
    # which DeepSeek lacks — must stay a custom name.
    cmd = OpencodeAdapter().spawn_cmd("w", "m")
    cfg = json.loads(cmd[cmd.index("{"):cmd.rindex("}") + 1])
    assert "openai" not in cfg["provider"]


def test_spawn_isolates_home_per_agent():
    a, b = OpencodeAdapter().spawn_cmd("aa", "m"), OpencodeAdapter().spawn_cmd("bb", "m")
    assert "HOME=" in a and "HOME=" in b
    assert a != b   # different agent_home → different config path


def test_submit_enter_first_and_markers_process():
    a = OpencodeAdapter()
    assert a.submit_keys()[0] == "Enter"
    assert a.ready_markers()
    assert a.process_name() == "opencode"


def test_clear_is_new_session():
    assert OpencodeAdapter().clear_command() == "/new"
