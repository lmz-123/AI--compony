"""Tests for `claudeteam remember` — agent durable memory entry point."""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.store import memory


def test_remember_appends_entry_with_kind_and_content():
    with isolated_env():
        rc, out, _ = run_cli(["remember", "manager", "learning",
                              "auth uses bcrypt"])
        assert rc == 0
        assert "🧠 remembered" in out
        assert "manager/learning" in out
        rows = memory.list_recent("manager")
        assert len(rows) == 1
        assert rows[0]["kind"] == "learning"
        assert rows[0]["content"] == "auth uses bcrypt"
        assert rows[0]["ref"] == ""


def test_remember_threads_ref_when_passed():
    """--ref ties the memory to an external artefact (message_id, task_id).
    Renders as `(ref=om_xx)` suffix in render_for_prompt."""
    with isolated_env():
        rc, _, _ = run_cli(["remember", "worker_cc", "task_assigned",
                            "fix login flow", "--ref", "om_42"])
        assert rc == 0
        rows = memory.list_recent("worker_cc")
        assert rows[0]["ref"] == "om_42"


def test_remember_joins_multi_word_content():
    """Convenience: callers can pass an unquoted message; everything after
    kind gets joined so the agent doesn't have to babysit shell quoting."""
    with isolated_env():
        rc, _, _ = run_cli(["remember", "worker_codex", "decision",
                            "use", "openai-fn", "calling", "for", "this"])
        assert rc == 0
        rows = memory.list_recent("worker_codex")
        assert rows[0]["content"] == "use openai-fn calling for this"


def test_remember_too_few_args_returns_usage_error():
    with isolated_env():
        rc, _, err = run_cli(["remember", "manager", "learning"])  # no content
        assert rc == 1
        assert "usage:" in err


def test_remember_team_writes_to_shared_pool_not_per_agent():
    """--team redirects the entry to the shared team experience, recording
    the agent as the contributor (`by`) and leaving the per-agent store
    untouched."""
    from claudeteam.store import team_memory
    with isolated_env():
        rc, out, _ = run_cli(["remember", "worker_cc", "learning",
                              "测试用 python3 tests/run.py", "--team"])
        assert rc == 0
        assert "🤝 team experience" in out
        assert memory.list_recent("worker_cc") == []   # not in the per-agent store
        rows = team_memory.list_recent()
        assert len(rows) == 1
        assert rows[0]["by"] == "worker_cc"
        assert rows[0]["content"] == "测试用 python3 tests/run.py"


def test_remember_team_update_edits_existing_entry():
    """--team --update <id> modifies an entry in place (refine a lesson)
    instead of appending a contradicting one."""
    from claudeteam.store import team_memory
    with isolated_env():
        run_cli(["remember", "worker_cc", "learning", "old wording", "--team"])
        rc, out, _ = run_cli(["remember", "manager", "learning",
                              "sharper wording", "--team", "--update", "E-1"])
        assert rc == 0
        assert "✏️" in out and "E-1" in out
        rows = team_memory.list_recent()
        assert len(rows) == 1
        assert rows[0]["content"] == "sharper wording"
        assert rows[0]["updated_by"] == "manager"


def test_remember_team_update_unknown_id_errors():
    with isolated_env():
        rc, _, err = run_cli(["remember", "manager", "learning", "x",
                              "--team", "--update", "E-9"])
        assert rc == 1
        assert "no team experience entry" in err


def test_remember_update_without_team_errors():
    """--update is meaningless for per-agent memory (append-only)."""
    with isolated_env():
        rc, _, err = run_cli(["remember", "manager", "learning", "x",
                              "--update", "E-1"])
        assert rc == 1
        assert "--team" in err


def test_remember_team_pin_marks_entry_resident():
    """--pin promotes an entry into the curated core that renders into
    every agent's prompt."""
    from claudeteam.store import team_memory
    with isolated_env():
        rc, out, _ = run_cli(["remember", "worker_cc", "learning",
                              "测试用 python3 tests/run.py", "--team", "--pin"])
        assert rc == 0
        assert "📌" in out
        assert team_memory.list_recent()[0]["pin"] is True
        assert "测试用 python3 tests/run.py" in team_memory.render_for_prompt()


def test_remember_pin_without_team_errors():
    with isolated_env():
        rc, _, err = run_cli(["remember", "manager", "learning", "x", "--pin"])
        assert rc == 1
        assert "--team" in err


def test_remember_team_update_can_repin():
    from claudeteam.store import team_memory
    with isolated_env():
        run_cli(["remember", "worker_cc", "learning", "x", "--team"])  # E-1 unpinned
        rc, _, _ = run_cli(["remember", "manager", "learning", "x",
                            "--team", "--update", "E-1", "--pin"])
        assert rc == 0
        assert team_memory.list_recent()[0]["pin"] is True
