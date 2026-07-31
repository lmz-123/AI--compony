"""Tests for agents/gemini_cli.py — Google Gemini CLI adapter shape."""
from __future__ import annotations

from claudeteam.agents.gemini_cli import GeminiCliAdapter
from claudeteam.agents import known_clis, get_adapter


def test_spawn_cmd_uses_yolo_approval_and_tags_agent():
    """Mirror codex/claude-code's auto-approve pattern so the pane runs
    unattended; tag agent name via env for /proc correlation."""
    adapter = GeminiCliAdapter()
    cmd = adapter.spawn_cmd("worker_gemini", "gemini-2.0-flash-exp")
    assert "GEMINI_AGENT='worker_gemini'" in cmd or "GEMINI_AGENT=worker_gemini" in cmd
    assert "--approval-mode=yolo" in cmd
    assert "DISABLE_UPDATE_CHECK=1" in cmd  # avoid blocking startup prompt


def test_spawn_cmd_drops_model_arg():
    """gemini-cli selects model via env, not argv. Adapter must NOT inject
    --model into spawn_cmd or the CLI will reject it."""
    adapter = GeminiCliAdapter()
    cmd = adapter.spawn_cmd("worker_gemini", "gemini-2.0-flash-exp")
    assert "--model" not in cmd
    # The model literal shouldn't accidentally show up either
    assert "gemini-2.0-flash-exp" not in cmd


def test_spawn_cmd_quotes_agent_name_with_special_chars():
    """shlex.quote defends against agent names with spaces / quotes."""
    adapter = GeminiCliAdapter()
    cmd = adapter.spawn_cmd("with space", "")
    assert "with space" in cmd
    # Either single-quoted or escaped (shlex chooses)
    assert "'with space'" in cmd or "with\\ space" in cmd


def test_ready_markers_present():
    adapter = GeminiCliAdapter()
    markers = adapter.ready_markers()
    assert "Gemini>" in markers
    assert any("Gemini" in m for m in markers)


def test_submit_keys_use_multiline_form():
    """Ink-based UIs need M-Enter to commit (Enter inserts newline);
    same pattern as Codex / Kimi. Plain Enter as fallback."""
    keys = GeminiCliAdapter().submit_keys()
    assert "M-Enter" in keys
    assert "Enter" in keys


def test_registered_in_agents_init():
    """`gemini-cli` should be accepted as a `cli` value in team.json."""
    assert "gemini-cli" in known_clis()
    adapter = get_adapter("gemini-cli")
    assert isinstance(adapter, GeminiCliAdapter)


def test_spawn_cmd_sets_per_agent_home():
    """HOME=<agent_home> isolates each pane's ~/.gemini so sibling panes
    don't clobber one shared auth cache / GEMINI.md."""
    from claudeteam.runtime.paths import agent_home
    cmd = GeminiCliAdapter().spawn_cmd("worker_gemini", "")
    assert f"HOME={agent_home('worker_gemini')}" in cmd


def test_native_memory_path_is_gemini_md_under_agent_home():
    from claudeteam.runtime.paths import agent_home
    path = GeminiCliAdapter().native_memory_path("worker_gemini")
    assert path == f"{agent_home('worker_gemini')}/.gemini/GEMINI.md"
