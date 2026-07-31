"""Tests for the MiniMax Mini-Agent adapter (agents/minimax_agent.py)."""
from __future__ import annotations

from claudeteam.agents import get_adapter
from claudeteam.agents.minimax_agent import MiniMaxAgentAdapter


def test_registered_under_minimax_and_mini_agent_alias():
    a = get_adapter("minimax")
    assert isinstance(a, MiniMaxAgentAdapter)
    assert get_adapter("mini-agent") is a   # alias → same instance


def test_spawn_provisions_config_from_env_then_launches():
    cmd = MiniMaxAgentAdapter().spawn_cmd("worker_mm", "deepseek-v4-flash")
    # Mini-Agent has no endpoint env var → spawn writes config.yaml from the
    # operator's OPENAI_* env + the agent's model, THEN launches the binary.
    # HOME is pinned under the agent's isolated home so two minimax agents (or
    # teams) don't share one global ~/.mini-agent (the leak this closed).
    assert "agents/worker_mm" in cmd and "HOME=" in cmd
    assert "/.mini-agent/config" in cmd and "$HOME/.mini-agent" not in cmd
    assert "$OPENAI_API_KEY" in cmd and "$OPENAI_BASE_URL" in cmd
    assert "provider: " in cmd            # yaml: provider: "openai"
    assert "config.yaml" in cmd
    assert "deepseek-v4-flash" in cmd     # the agent's model lands in the write
    assert cmd.rstrip().endswith("mini-agent")
    assert "MINI_AGENT_AGENT=worker_mm" in cmd


def test_spawn_uses_passed_model_no_vendor_hardcode():
    a = MiniMaxAgentAdapter()
    assert "my-model-x" in a.spawn_cmd("w", "my-model-x")  # config model = team.json
    # provider-agnostic: no vendor base_url / key / model baked into source
    cmd = a.spawn_cmd("w", "")
    assert "deepseek" not in cmd and "api.deepseek.com" not in cmd


def test_auth_slots_route_key_through_agent_auth():
    from claudeteam.agents.base import OPENAI_COMPAT_AUTH
    assert MiniMaxAgentAdapter().auth_slots() is OPENAI_COMPAT_AUTH


def test_spawn_quotes_agent_with_special_chars():
    assert "'worker x'" in MiniMaxAgentAdapter().spawn_cmd("worker x", "m")


def test_submit_leads_with_enter_never_cj():
    keys = MiniMaxAgentAdapter().submit_keys()
    assert keys[0] == "Enter"
    assert "C-j" not in keys   # C-j inserts a newline in mini-agent, never submits


def test_markers_and_process_name():
    a = MiniMaxAgentAdapter()
    markers = a.ready_markers()
    assert markers and all(isinstance(m, str) for m in markers)
    assert "Mini Agent" in " ".join(markers)
    assert a.process_name() == "mini-agent"


def test_clear_yes_compact_no():
    a = MiniMaxAgentAdapter()
    assert a.clear_command() == "/clear"   # base default; mini-agent has it
    assert a.compact_command() is None     # mini-agent has NO /compact


def test_display_model_shows_real_model_not_team_alias():
    a = MiniMaxAgentAdapter()
    assert a.display_model("deepseek-v4-pro") == "deepseek-v4-pro"
    assert a.display_model("").strip() != ""   # neutral label when unset
