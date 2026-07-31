"""Tests for the openclaw adapter (agents/openclaw_cli.py)."""
from __future__ import annotations

import json

from claudeteam.agents import get_adapter
from claudeteam.agents.openclaw_cli import OpenclawAdapter


def test_registered():
    assert isinstance(get_adapter("openclaw"), OpenclawAdapter)


def test_spawn_writes_config_with_allowlisted_model_then_launches():
    cmd = OpenclawAdapter().spawn_cmd("worker_ow", "deepseek-v4-pro")
    assert "/.openclaw/openclaw.json" in cmd
    assert cmd.rstrip().endswith("openclaw chat")
    assert "OPENCLAW_AGENT=worker_ow" in cmd
    cfg = json.loads(cmd[cmd.index("{"):cmd.rindex("}") + 1])
    fq = "compat/deepseek-v4-pro"   # neutral provider id + the team.json model
    # model is the primary AND allowlisted (openclaw rejects un-allowlisted models)
    assert cfg["agents"]["defaults"]["model"]["primary"] == fq
    assert fq in cfg["agents"]["defaults"]["models"]
    prov = cfg["models"]["providers"]["compat"]
    assert prov["api"] == "openai-completions"
    # endpoint + key from env (openclaw substitutes ${...} at runtime)
    assert prov["baseUrl"] == "${OPENAI_BASE_URL}"
    assert prov["apiKey"] == "${OPENAI_API_KEY}"
    assert any(mm["id"] == "deepseek-v4-pro" for mm in prov["models"])


def test_spawn_is_env_driven_and_isolates_home():
    a = OpenclawAdapter()
    cmd = a.spawn_cmd("w", "m")
    assert "api.deepseek.com" not in cmd and "deepseek" not in cmd  # no hardcode
    assert a.spawn_cmd("aa", "m") != a.spawn_cmd("bb", "m")  # per-agent HOME


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert OpenclawAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_submit_markers_process():
    a = OpenclawAdapter()
    assert a.submit_keys()[0] == "Enter"
    assert a.ready_markers()
    assert a.process_name() == "openclaw-tui"
