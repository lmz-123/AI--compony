"""Tests for store/team_memory.py — the shared team-experience pool."""
from __future__ import annotations

from helpers import attr_patch, isolated_env
from claudeteam.store import team_memory


def test_append_then_list_round_trips():
    with isolated_env():
        team_memory.append("测试用 python3 tests/run.py",
                           kind="learning", by="worker_cc")
        rows = team_memory.list_recent()
        assert len(rows) == 1
        assert rows[0]["content"] == "测试用 python3 tests/run.py"
        assert rows[0]["kind"] == "learning"
        assert rows[0]["by"] == "worker_cc"


def test_render_for_prompt_marks_contributor_and_ref():
    with isolated_env():
        team_memory.append("用两步结账", kind="decision", by="manager",
                           ref="I-7", pin=True)   # only pinned entries inject
        block = team_memory.render_for_prompt()
        assert "团队共享经验" in block
        assert "[decision] 用两步结账 (@manager) (ref=I-7)" in block


def test_render_empty_is_blank():
    """Callers branch on `if block:` — an empty pool must render to ''."""
    with isolated_env():
        assert team_memory.render_for_prompt() == ""


def test_clear_drops_all_and_is_idempotent():
    with isolated_env():
        team_memory.append("x", by="a")
        team_memory.append("y", by="b")
        assert team_memory.clear() == 2
        assert team_memory.list_recent() == []
        assert team_memory.clear() == 0


def test_cap_truncates_from_front():
    """Past the retention cap, oldest entries drop first (bounded growth)."""
    with isolated_env(), attr_patch(team_memory, _max=lambda: 2):
        for i in range(4):
            team_memory.append(f"e{i}", by="a")
        assert [r["content"] for r in team_memory.list_recent()] == ["e2", "e3"]


# ── update / remove (living KB) ──────────────────────────────────


def test_update_modifies_entry_in_place():
    with isolated_env():
        e = team_memory.append("测试用 python3", kind="learning", by="worker_cc")
        rec = team_memory.update(e["id"], content="测试用 python3 tests/run.py",
                                 by="manager")
        assert rec is not None
        rows = team_memory.list_recent()
        assert len(rows) == 1                      # edited in place, not appended
        assert rows[0]["content"] == "测试用 python3 tests/run.py"
        assert rows[0]["updated_by"] == "manager"
        assert "updated_at" in rows[0]


def test_update_unknown_id_returns_none():
    with isolated_env():
        team_memory.append("x", by="a")
        assert team_memory.update("E-99", content="nope") is None


def test_remove_drops_one_entry():
    with isolated_env():
        team_memory.append("keep", by="a")
        b = team_memory.append("retire", by="b")
        assert team_memory.remove(b["id"]) is True
        assert [r["content"] for r in team_memory.list_recent()] == ["keep"]


def test_remove_unknown_id_returns_false():
    with isolated_env():
        team_memory.append("x", by="a")
        assert team_memory.remove("E-99") is False


# ── curated-core injection (pin) ─────────────────────────────────


def test_render_injects_only_pinned_plus_pull_pointer():
    with isolated_env():
        team_memory.append("全队都要知道", kind="learning", by="a", pin=True)
        team_memory.append("一次性细节", kind="note", by="b")     # unpinned
        block = team_memory.render_for_prompt()
        assert "全队都要知道" in block            # pinned → injected
        assert "一次性细节" not in block          # unpinned → NOT injected
        assert "另有 1 条" in block               # footer points at the rest


def test_render_unpinned_only_is_just_a_pointer():
    with isolated_env():
        team_memory.append("x", by="a")           # unpinned
        block = team_memory.render_for_prompt()
        assert "置顶 · 全队常驻" not in block      # no pinned section
        assert "另有 1 条" in block               # but agents are told it exists


def test_enforce_cap_never_drops_pinned():
    with isolated_env(), attr_patch(team_memory, _max=lambda: 2):
        team_memory.append("pinned-keep", by="a", pin=True)
        for i in range(3):
            team_memory.append(f"u{i}", by="b")   # unpinned churn
        contents = [r["content"] for r in team_memory.list_recent(limit=99)]
        assert "pinned-keep" in contents          # pinned never evicted
        assert "u2" in contents                   # newest unpinned kept
        assert "u0" not in contents and "u1" not in contents  # oldest dropped


def test_update_pin_promotes_into_injection():
    with isolated_env():
        e = team_memory.append("later pinned", by="a")   # unpinned
        assert "later pinned" not in team_memory.render_for_prompt()
        team_memory.update(e["id"], pin=True)
        assert "later pinned" in team_memory.render_for_prompt()
