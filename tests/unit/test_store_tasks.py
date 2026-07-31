"""Tests for store/tasks.py — local task store."""
from __future__ import annotations


from helpers import isolated_env
from claudeteam.store import tasks


# ── create ────────────────────────────────────────────────────────


def test_create_returns_task_id_and_persists():
    with isolated_env():
        tid = tasks.create("worker", "do thing")
        assert tid == "T-1"
        rows = tasks.list_tasks()
        assert len(rows) == 1
        assert rows[0]["title"] == "do thing"
        assert rows[0]["status"] == "待处理"


def test_create_with_metadata_persists_creator_and_description():
    with isolated_env():
        tid = tasks.create("worker", "fix X",
                           description="root cause is Y",
                           creator="manager")
        t = tasks.get(tid)
        assert t["creator"] == "manager"
        assert t["description"] == "root cause is Y"


def test_create_empty_title_rejects():
    with isolated_env():
        try:
            tasks.create("worker", "   ")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError on empty title")


# ── update ────────────────────────────────────────────────────────


def test_update_status_advances_state():
    with isolated_env():
        tid = tasks.create("w", "task")
        assert tasks.update(tid, status="进行中") is True
        assert tasks.get(tid)["status"] == "进行中"


def test_update_invalid_status_rejects():
    with isolated_env():
        tid = tasks.create("w", "task")
        try:
            tasks.update(tid, status="not-a-status")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


def test_update_missing_task_returns_false():
    with isolated_env():
        assert tasks.update("T-99", status="已完成") is False


def test_update_terminal_status_sets_completed_at():
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成")
        t = tasks.get(tid)
        assert t["completed_at"] is not None


def test_update_terminal_is_frozen_no_resurrection():
    """A finished/cancelled task can't be silently revived via update —
    reopening means creating a new task. (Previously legal; a revived
    task would re-anchor a stale intent into the assignee's CLAUDE.md.)"""
    with isolated_env():
        for terminal in ("已完成", "已取消"):
            tid = tasks.create("w", "x")
            tasks.update(tid, status=terminal)
            for target in ("待处理", "进行中"):
                try:
                    tasks.update(tid, status=target)
                except ValueError as e:
                    assert "illegal transition" in str(e)
                    assert "terminal" in str(e)
                else:
                    raise AssertionError(f"{terminal} → {target} should raise")
            assert tasks.get(tid)["status"] == terminal


def test_update_same_status_is_idempotent_noop():
    """Re-asserting the current status is tolerated (idempotent retries)
    and doesn't refresh completed_at."""
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成")
        stamp = tasks.get(tid)["completed_at"]
        assert tasks.update(tid, status="已完成") is True
        assert tasks.get(tid)["completed_at"] == stamp


def test_update_nonstatus_fields_still_editable_on_terminal():
    """Freezing terminals only constrains STATUS — bookkeeping edits
    (title / description / assignee) stay allowed on finished tasks."""
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成")
        assert tasks.update(tid, title="x (归档备注)") is True
        assert tasks.get(tid)["title"] == "x (归档备注)"
        assert tasks.get(tid)["status"] == "已完成"


def test_update_only_changes_specified_fields():
    with isolated_env():
        tid = tasks.create("w1", "title-1", description="d-1", creator="c-1")
        tasks.update(tid, status="进行中")
        t = tasks.get(tid)
        # other fields untouched
        assert t["assignee"] == "w1"
        assert t["title"] == "title-1"
        assert t["description"] == "d-1"
        assert t["creator"] == "c-1"


def test_update_can_reassign_and_retitle():
    with isolated_env():
        tid = tasks.create("w1", "old", description="old-d")
        tasks.update(tid, assignee="w2", title="new", description="new-d")
        t = tasks.get(tid)
        assert (t["assignee"], t["title"], t["description"]) == ("w2", "new", "new-d")


# ── list/get ──────────────────────────────────────────────────────


def test_list_filters_by_status():
    with isolated_env():
        a = tasks.create("w", "a")
        b = tasks.create("w", "b")
        tasks.update(a, status="已完成")
        only_done = tasks.list_tasks(status="已完成")
        only_open = tasks.list_tasks(status="待处理")
        assert [t["id"] for t in only_done] == [a]
        assert [t["id"] for t in only_open] == [b]


def test_list_filters_by_assignee():
    with isolated_env():
        tasks.create("alice", "task-a")
        tasks.create("bob", "task-b")
        tasks.create("alice", "task-a2")
        out = tasks.list_tasks(assignee="alice")
        assert {t["title"] for t in out} == {"task-a", "task-a2"}


def test_list_returns_empty_when_store_missing():
    with isolated_env():
        assert tasks.list_tasks() == []


def test_get_returns_none_for_unknown_id():
    with isolated_env():
        assert tasks.get("T-doesnotexist") is None


# ── intent records (immutable verbatim asks) ──────────────────────


def test_create_intent_returns_id_and_persists_verbatim():
    with isolated_env():
        iid = tasks.create_intent("把首页做成深色模式", source_msg="msg_1")
        assert iid == "I-1"
        intent = tasks.get_intent(iid)
        assert intent["raw_text"] == "把首页做成深色模式"
        assert intent["source_msg"] == "msg_1"
        assert intent["creator"] == "user"


def test_create_intent_empty_rejects():
    with isolated_env():
        try:
            tasks.create_intent("   ")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError on empty raw_text")


def test_get_intent_unknown_returns_none():
    with isolated_env():
        assert tasks.get_intent("I-404") is None


def test_task_intent_id_backlink_persists():
    with isolated_env():
        iid = tasks.create_intent("原话")
        tid = tasks.create("w", "子任务", intent_id=iid)
        assert tasks.get(tid)["intent_id"] == iid


def test_intent_raw_text_immutable_across_task_churn():
    with isolated_env():
        iid = tasks.create_intent("逐字原话不可变")
        tid = tasks.create("w", "t", intent_id=iid)
        tasks.update(tid, status="进行中")
        tasks.update(tid, title="改名", description="改描述")
        # no API mutates raw_text; it must remain byte-identical
        assert tasks.get_intent(iid)["raw_text"] == "逐字原话不可变"


# ── approval-suspend state machine ────────────────────────────────


def test_pause_suspends_only_in_progress():
    with isolated_env():
        tid = tasks.create("w", "t")
        # 待处理 cannot be paused
        assert tasks.pause(tid) is False
        tasks.update(tid, status="进行中")
        assert tasks.pause(tid, approval_note="要老板拍板", paused_by="w") is True
        t = tasks.get(tid)
        assert t["status"] == "需审批"
        assert t["awaiting"] == "user"
        assert t["approval_note"] == "要老板拍板"
        assert t["paused_by"] == "w"
        assert t["paused_at"] is not None


def test_pause_missing_task_returns_false():
    with isolated_env():
        assert tasks.pause("T-99") is False


def test_approve_continue_returns_to_in_progress():
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid)
        assert tasks.approve(tid) is True
        t = tasks.get(tid)
        assert t["status"] == "进行中"
        assert t["awaiting"] == ""


def test_approve_done_marks_complete():
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid)
        assert tasks.approve(tid, done=True) is True
        t = tasks.get(tid)
        assert t["status"] == "已完成"
        assert t["completed_at"] is not None


def test_approve_note_carries_verdict():
    """approve(note=) must persist the VERDICT into approval_note —
    symmetric with reject's feedback. REGRESSION: a worker resumed from
    a question-only anchor and invented the answer the boss never gave;
    the decision content must ride the state machine, not a free-text
    relay."""
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid, approval_note="第三行写什么？")
        assert tasks.approve(tid, note="写鱼香肉丝") is True
        t = tasks.get(tid)
        assert t["status"] == "进行中"
        assert t["approval_note"] == "写鱼香肉丝"     # verdict replaces question


def test_approve_without_note_keeps_clear_behavior():
    """Empty note preserves the historical clear-on-approve semantics."""
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid, approval_note="要不要加第三行")
        assert tasks.approve(tid) is True
        assert tasks.get(tid)["approval_note"] == ""


def test_reject_rework_keeps_feedback():
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid)
        assert tasks.reject(tid, feedback="方向错了") is True
        t = tasks.get(tid)
        assert t["status"] == "进行中"
        assert t["approval_note"] == "方向错了"


def test_reject_cancel_terminates():
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid)
        assert tasks.reject(tid, cancel=True) is True
        t = tasks.get(tid)
        assert t["status"] == "已取消"
        assert t["completed_at"] is not None


def test_approve_reject_reject_non_suspended():
    with isolated_env():
        tid = tasks.create("w", "t")          # 待处理
        assert tasks.approve(tid) is False
        assert tasks.reject(tid) is False
        tasks.update(tid, status="进行中")
        assert tasks.approve(tid) is False     # not 需审批 → no-op


def test_update_cannot_advance_suspended_task():
    """The gate: a suspended task may not be moved by generic update()."""
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid)
        for bad in ("进行中", "已完成", "已取消"):
            try:
                tasks.update(tid, status=bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"update should refuse 需审批→{bad}")
        assert tasks.get(tid)["status"] == "需审批"   # untouched


def test_update_cannot_force_into_suspend():
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        try:
            tasks.update(tid, status="需审批")
        except ValueError:
            pass
        else:
            raise AssertionError("update should refuse moving INTO 需审批")


def test_update_nonstatus_fields_allowed_while_suspended():
    """Editing title/assignee on a suspended task is harmless and allowed;
    only status transitions are gated."""
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid)
        assert tasks.update(tid, assignee="w2", title="renamed") is True
        t = tasks.get(tid)
        assert t["status"] == "需审批" and t["assignee"] == "w2"


def test_reject_from_non_suspended_returns_false():
    """reject() only leaves 需审批; from any other state it's a no-op so a
    stray reject can't mutate a task that was never up for approval."""
    with isolated_env():
        tid = tasks.create("w", "t")               # 待处理
        assert tasks.reject(tid, feedback="x") is False
        tasks.update(tid, status="进行中")
        assert tasks.reject(tid, feedback="x") is False
        assert tasks.get(tid)["status"] == "进行中"  # untouched
        tasks.update(tid, status="已完成")
        assert tasks.reject(tid, cancel=True) is False
        assert tasks.get(tid)["status"] == "已完成"


def test_double_pause_returns_false():
    """A task already 需审批 cannot be paused again (pause only from 进行中),
    so a second pause can't reset paused_at / clobber the pending note."""
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        assert tasks.pause(tid, approval_note="first") is True
        first_paused_at = tasks.get(tid)["paused_at"]
        assert tasks.pause(tid, approval_note="second") is False
        t = tasks.get(tid)
        assert t["approval_note"] == "first"
        assert t["paused_at"] == first_paused_at


# ── multi-intent / multi-task isolation ───────────────────────────


def test_multi_intent_multi_task_isolation():
    """Several intents + tasks coexist without cross-contamination: each
    task keeps its own intent back-link, and list filters isolate cleanly."""
    with isolated_env():
        i1 = tasks.create_intent("意图一：改结账")
        i2 = tasks.create_intent("意图二：改登录")
        t1 = tasks.create("alice", "结账子任务", intent_id=i1)
        t2 = tasks.create("bob", "登录子任务", intent_id=i2)
        t3 = tasks.create("alice", "结账子任务二", intent_id=i1)
        # back-links stay distinct
        assert tasks.get(t1)["intent_id"] == i1
        assert tasks.get(t2)["intent_id"] == i2
        assert tasks.get(t3)["intent_id"] == i1
        # intents read back verbatim and independently
        assert tasks.get_intent(i1)["raw_text"] == "意图一：改结账"
        assert tasks.get_intent(i2)["raw_text"] == "意图二：改登录"
        # assignee filter isolates without leaking the other owner's tasks
        alice = {t["id"] for t in tasks.list_tasks(assignee="alice")}
        assert alice == {t1, t3}
        assert t2 not in alice
        # suspending one task never touches a sibling sharing its intent
        tasks.update(t1, status="进行中")
        tasks.pause(t1)
        assert tasks.get(t1)["status"] == "需审批"
        assert tasks.get(t3)["status"] == "待处理"


# ── persistence: suspend state survives a store reload ────────────


def test_suspend_state_persists_to_disk_across_reload():
    """The store keeps no in-memory cache: after pause(), the suspend state
    + bookkeeping must be on disk so a fresh process reads 需审批 back."""
    import json
    from claudeteam.runtime import paths
    with isolated_env():
        tid = tasks.create("w", "t")
        tasks.update(tid, status="进行中")
        tasks.pause(tid, approval_note="等老板", paused_by="w")
        # read the raw JSON straight off disk — no API in between
        raw = json.loads((paths.state_dir() / "tasks.json").read_text("utf-8"))
        persisted = next(t for t in raw["tasks"] if t["id"] == tid)
        assert persisted["status"] == "需审批"
        assert persisted["approval_note"] == "等老板"
        assert persisted["paused_by"] == "w"
        assert persisted["paused_at"] is not None
        # and a plain get() (which re-reads the file) agrees
        assert tasks.get(tid)["status"] == "需审批"


# ── boundary / hostile input ──────────────────────────────────────


def test_intent_raw_text_preserves_special_chars_and_length():
    """raw_text is stored verbatim: newlines, quotes, emoji, CJK and a very
    long body must all round-trip byte-identical (no trimming/escaping)."""
    with isolated_env():
        nasty = (
            "第一行\n第二行\t制表\n"
            "引号\"双\" '单' `反引号` $(whoami) ${HOME}\n"
            "emoji 🚀✅ — 破折号\n"
        ) + ("长" * 5000)
        iid = tasks.create_intent(nasty, source_msg="msg_x")
        assert tasks.get_intent(iid)["raw_text"] == nasty


def test_create_intent_persists_key_points():
    with isolated_env():
        iid = tasks.create_intent("原话", key_points="要点A; 要点B")
        assert tasks.get_intent(iid)["key_points"] == "要点A; 要点B"


def test_title_with_special_chars_roundtrips():
    """Only surrounding whitespace is stripped; interior special characters
    survive untouched."""
    with isolated_env():
        tid = tasks.create("w", "  改 \"支付\" 页 $(x) 🚀  ")
        assert tasks.get(tid)["title"] == "改 \"支付\" 页 $(x) 🚀"


def test_whitespace_only_intent_rejected_at_various_forms():
    """Empty / pure-whitespace (spaces, tabs, newlines) raw_text all reject —
    the guard uses .strip() so every blank form is caught."""
    with isolated_env():
        for blank in ("", "   ", "\t\t", "\n \n"):
            try:
                tasks.create_intent(blank)
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for {blank!r}")


# ── backward compatibility (old tasks.json without new fields) ─────


def test_reads_legacy_task_without_new_fields():
    """A tasks.json written before this feature lacks intent_id / suspend
    fields. Reads must not raise and must surface safe defaults."""
    import json
    from claudeteam.runtime import paths
    with isolated_env():
        legacy = {
            "tasks": [{
                "id": "T-1", "title": "old", "description": "",
                "assignee": "w", "creator": "", "status": "进行中",
                "created_at": 1, "updated_at": 1, "completed_at": None,
            }],
            "_meta": {"last_id": 1},
        }
        f = paths.state_dir() / "tasks.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        t = tasks.get("T-1")
        assert t.get("intent_id", "") == ""
        assert t.get("awaiting", "") == ""
        # legacy file has no intents array / last_intent_id — still creatable
        iid = tasks.create_intent("新原话")
        assert iid == "I-1"
        # and the legacy task is still updatable normally
        assert tasks.update("T-1", status="已完成") is True


# ── intent creator attribution ────────────────────────────────────


def test_create_intent_creator_override():
    """An agent recording a derived/relayed ask can attribute the intent
    to itself via creator=; the CLI's --by surfaces this. Default stays
    'user' (the canonical boss-verbatim case)."""
    with isolated_env():
        iid = tasks.create_intent("派生需求", creator="manager")
        assert tasks.get_intent(iid)["creator"] == "manager"
        # default unchanged
        assert tasks.get_intent(tasks.create_intent("老板原话"))["creator"] == "user"


# ── void / tombstone ──────────────────────────────────────────────


def test_void_retires_a_mistakenly_completed_task():
    """The ONLY path out of a 已完成 card: the generic update() path freezes
    terminals, reject() is 需审批-only — void() opens 已完成 → 已取消."""
    with isolated_env():
        tid = tasks.create("w", "误建的重复活")
        tasks.update(tid, status="已完成")
        assert tasks.void(tid, reason="重复任务，作废", voided_by="manager") is True
        t = tasks.get(tid)
        assert t["status"] == "已取消"
        assert t["completed_at"] is not None      # terminal bookkeeping
        assert t["approval_note"] == "重复任务，作废"
        assert t["paused_by"] == "manager"


def test_void_works_from_any_non_cancelled_state():
    with isolated_env():
        for setup in (
            lambda tid: None,                                  # 待处理
            lambda tid: tasks.update(tid, status="进行中"),
            lambda tid: tasks.update(tid, status="已完成"),
        ):
            tid = tasks.create("w", "x")
            setup(tid)
            assert tasks.void(tid) is True
            assert tasks.get(tid)["status"] == "已取消"


def test_void_idempotent_noop_preserves_reason():
    """Re-voiding an already-cancelled task is a no-op and never clobbers
    the original reason."""
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.void(tid, reason="第一次作废理由")
        assert tasks.void(tid, reason="覆盖?") is False
        assert tasks.get(tid)["approval_note"] == "第一次作废理由"


def test_void_missing_task_returns_false():
    with isolated_env():
        assert tasks.void("T-404") is False


def test_void_does_not_thaw_the_generic_update_freeze():
    """void() is the only doorway terminal→已取消; the generic update()
    path stays frozen on terminals so a stray --status can't resurrect."""
    with isolated_env():
        tid = tasks.create("w", "x")
        tasks.update(tid, status="已完成")
        for target in ("待处理", "进行中"):
            try:
                tasks.update(tid, status=target)
            except ValueError as e:
                assert "terminal" in str(e)
            else:
                raise AssertionError(f"已完成 → {target} via update should still raise")
