"""Tests for `claudeteam task` subcommand dispatcher."""
from __future__ import annotations

import io

from helpers import isolated_env, run_cli, attr_patch
from claudeteam.commands import task as task_cmd
from claudeteam.runtime import tmux as tmux_mod, wake as wake_mod
from claudeteam.runtime import pane_probe as pp_mod
from claudeteam.store import local_facts, tasks


# ── create ────────────────────────────────────────────────────────


def test_task_create_minimal():
    with isolated_env():
        rc, out, _ = run_cli(["task", "create", "worker", "do task X"])
        assert rc == 0
        assert "created T-1" in out
        rows = tasks.list_tasks()
        assert rows[0]["title"] == "do task X"
        assert rows[0]["assignee"] == "worker"


def test_task_create_with_by_and_desc():
    with isolated_env():
        run_cli(["task", "create", "worker", "task name",
              "--by", "manager", "--desc", "root cause Y"])
        t = tasks.list_tasks()[0]
        assert t["creator"] == "manager"
        assert t["description"] == "root cause Y"


def test_task_create_with_auto_dispatch():
    with isolated_env():
        run_cli(["task", "create", "worker", "task name",
              "--auto-dispatch", "deployer"])
        t = tasks.list_tasks()[0]
        assert t["auto_dispatch_assignee"] == "deployer"


def test_task_create_missing_args_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "create", "worker"])
        assert rc == 1
        assert "usage:" in err


# ── update ────────────────────────────────────────────────────────


def test_task_update_status():
    with isolated_env():
        tasks.create("w", "x")
        rc, out, _ = run_cli(["task", "update", "T-1", "--status", "进行中"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "进行中"


def test_task_update_invalid_status_returns_one():
    with isolated_env():
        tasks.create("w", "x")
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "bogus"])
        assert rc == 1
        assert "invalid status" in err


def test_task_update_unknown_id_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "update", "T-99", "--status", "已完成"])
        assert rc == 1
        assert "no such task" in err


def test_task_update_can_reassign_and_retitle():
    with isolated_env():
        tasks.create("w1", "old")
        run_cli(["task", "update", "T-1", "--assignee", "w2", "--title", "new"])
        t = tasks.get("T-1")
        assert t["assignee"] == "w2"
        assert t["title"] == "new"


def test_task_update_can_change_auto_dispatch():
    with isolated_env():
        tasks.create("w1", "old")
        run_cli(["task", "update", "T-1", "--auto-dispatch", "deployer"])
        t = tasks.get("T-1")
        assert t["auto_dispatch_assignee"] == "deployer"


# ── done shortcut ────────────────────────────────────────────────


def test_task_done_marks_completed():
    with isolated_env():
        tasks.create("w", "x")
        tasks.update("T-1", status="进行中")
        local_facts.append_message("w", "manager", "dispatch", task_id="T-1")
        from claudeteam.store import radio
        radio.append_update("w", "T-1", "msg_radio", "manager", "supplement")
        rc, out, _ = run_cli(["task", "done", "T-1"])
        assert rc == 0
        t = tasks.get("T-1")
        assert t["status"] == "已完成"
        assert t["completed_at"] is not None
        assert not local_facts.list_messages("w", unread_only=True)
        assert radio.agent_threads("w") == []
        archived = radio.agent_threads("w", include_archived=True)
        assert archived[0]["archived"] is True


def test_task_done_notifies_manager_and_suggests_next_dispatch():
    with isolated_env():
        run_cli(["task", "create", "w", "first", "--by", "manager"])
        run_cli(["task", "create", "w", "second", "--by", "manager"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        rc, _, _ = run_cli(["task", "done", "T-1"])
        assert rc == 0
        msgs = [m for m in local_facts.list_messages("manager") if m["task_id"] == "T-1"]
        assert msgs, "manager should receive a backend completion receipt"
        content = msgs[-1]["content"]
        assert "任务完成回执" in content
        assert "T-2 second" in content
        assert "dispatch-next w --by manager" in content


def test_task_done_auto_dispatches_next_worker_task_without_manager_receipt():
    with isolated_env():
        run_cli(["task", "create", "developer", "dev done",
                 "--by", "manager", "--auto-dispatch", "deployer"])
        run_cli(["task", "create", "deployer", "deploy it", "--by", "manager"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        rc, _, _ = run_cli(["task", "done", "T-1"])
        assert rc == 0
        assert tasks.get("T-2")["status"] == "进行中"
        deployer_msgs = [m for m in local_facts.list_messages("deployer")
                         if m["task_id"] == "T-2"]
        assert deployer_msgs
        manager_msgs = [m for m in local_facts.list_messages("manager")
                        if m["task_id"] == "T-1"]
        assert not manager_msgs


def test_task_done_auto_dispatch_failure_notifies_manager():
    with isolated_env(), attr_patch(task_cmd, _dispatch_next_backend=lambda assignee, by="manager": ("empty", None)):
        run_cli(["task", "create", "developer", "dev done",
                 "--by", "manager", "--auto-dispatch", "deployer"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        rc, _, _ = run_cli(["task", "done", "T-1"])
        assert rc == 0
        manager_msgs = [m for m in local_facts.list_messages("manager")
                        if m["task_id"] == "T-1"]
        assert manager_msgs
        assert "自动续派异常" in manager_msgs[-1]["content"]


def test_task_dispatch_next_send_failure_requeues_task():
    with isolated_env():
        tasks.create("w", "x")
        with attr_patch(task_cmd, _refresh_anchor=lambda *agents: None):
            from claudeteam.commands import send as send_cmd
            with attr_patch(send_cmd, main=lambda argv: 1):
                rc, _, _ = run_cli(["task", "dispatch-next", "w", "--by", "manager"])
        assert rc == 1
        assert tasks.get("T-1")["status"] == "待处理"


# ── list / get ────────────────────────────────────────────────────


def test_task_list_shows_count_and_each_row():
    with isolated_env():
        tasks.create("w", "first task")
        tasks.create("w", "second task")
        rc, out, _ = run_cli(["task", "list"])
        assert rc == 0
        assert "2 tasks" in out
        assert "first task" in out and "second task" in out


def test_task_list_filter_by_status_and_assignee():
    with isolated_env():
        tasks.create("alice", "a-task")
        tasks.create("bob", "b-task")
        tasks.create("alice", "a-done")
        tasks.update("T-3", status="已完成")

        rc, out, _ = run_cli(["task", "list", "--assignee", "alice"])
        assert rc == 0
        assert "a-task" in out and "a-done" in out
        assert "b-task" not in out

        rc, out, _ = run_cli(["task", "list", "--status", "已完成"])
        assert rc == 0
        assert "a-done" in out
        assert "a-task" not in out


def test_task_get_existing_renders_full_card():
    with isolated_env():
        tasks.create("w", "task one", description="d")
        rc, out, _ = run_cli(["task", "get", "T-1"])
        assert rc == 0
        assert "T-1" in out and "task one" in out
        assert "desc: d" in out


# ── dispatcher ───────────────────────────────────────────────────


def test_task_unknown_subcommand_returns_one():
    rc, _, err = run_cli(["task", "invent"])
    assert rc == 1
    assert "unknown task subcommand" in err


# ── intent ────────────────────────────────────────────────────────


def test_task_intent_create_and_get():
    with isolated_env():
        rc, out, _ = run_cli(["task", "intent", "create", "把首页改深色",
                              "--src", "msg_9"])
        assert rc == 0 and "I-1" in out
        rc, out, _ = run_cli(["task", "intent", "get", "I-1"])
        assert rc == 0
        assert "把首页改深色" in out


def test_task_create_with_intent_backlink():
    with isolated_env():
        tasks.create_intent("原话")            # I-1
        run_cli(["task", "create", "w", "子任务", "--intent", "I-1"])
        assert tasks.get("T-1")["intent_id"] == "I-1"


def test_task_intent_get_unknown_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "intent", "get", "I-99"])
        assert rc == 1
        assert "no such intent" in err


# ── pause / approve / reject ──────────────────────────────────────


def _make_in_progress(assignee="w"):
    tasks.create(assignee, "t")
    tasks.update("T-1", status="进行中")


def test_task_pause_suspends_and_routes_to_approver():
    with isolated_env():
        _make_in_progress()
        rc, out, _ = run_cli(["task", "pause", "T-1", "--note", "要拍板",
                              "--by", "w"])
        assert rc == 0 and "需审批" in out
        assert tasks.get("T-1")["status"] == "需审批"
        # an approval-request message lands in the boss inbox, tagged task_id
        msgs = local_facts.list_messages("user")
        assert any(m["task_id"] == "T-1" for m in msgs)


def test_task_pause_non_in_progress_returns_one():
    with isolated_env():
        tasks.create("w", "t")                # 待处理
        rc, _, err = run_cli(["task", "pause", "T-1"])
        assert rc == 1
        assert "cannot pause" in err


def test_task_approve_continue():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, out, _ = run_cli(["task", "approve", "T-1"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "进行中"
        # decision echoed back to the assignee inbox
        assert any(m["task_id"] == "T-1" for m in local_facts.list_messages("w"))


def test_task_approve_done():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli(["task", "approve", "T-1", "--done"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "已完成"


def test_task_approve_done_auto_dispatches_next_worker_task():
    with isolated_env():
        run_cli(["task", "create", "developer", "dev done",
                 "--by", "manager", "--auto-dispatch", "deployer"])
        run_cli(["task", "create", "deployer", "deploy it", "--by", "manager"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli(["task", "approve", "T-1", "--done"])
        assert rc == 0
        assert tasks.get("T-2")["status"] == "进行中"
        manager_msgs = [m for m in local_facts.list_messages("manager")
                        if m["task_id"] == "T-1"]
        assert not manager_msgs


def test_task_approve_note_reaches_receipt_and_audit():
    """The verdict must ride the gated channel — assignee receipt and
    audit row both carry `--note`, and the task's approval_note holds it
    for the anchor to surface."""
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1", "--note", "第三行写什么？"])
        rc, _, _ = run_cli(["task", "approve", "T-1",
                            "--note", "写鱼香肉丝"])
        assert rc == 0
        assert tasks.get("T-1")["approval_note"] == "写鱼香肉丝"
        receipts = [m for m in local_facts.list_messages("w")
                    if m["task_id"] == "T-1"]
        assert any("写鱼香肉丝" in m["content"] for m in receipts)
        logs = local_facts.list_logs("w")
        assert any("写鱼香肉丝" in r["content"] for r in logs)


def test_task_approve_non_suspended_returns_one():
    with isolated_env():
        _make_in_progress()
        rc, _, err = run_cli(["task", "approve", "T-1"])
        assert rc == 1
        assert "cannot approve" in err


def test_task_reject_rework_with_feedback():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli(["task", "reject", "T-1", "方向", "错了"])
        assert rc == 0
        t = tasks.get("T-1")
        assert t["status"] == "进行中"
        assert t["approval_note"] == "方向 错了"


def test_task_reject_cancel():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, _ = run_cli(["task", "reject", "T-1", "不做了", "--cancel"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "已取消"


def test_task_update_cannot_bypass_gate_via_cli():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "已完成"])
        assert rc == 1
        assert "需审批" in err
        assert tasks.get("T-1")["status"] == "需审批"


def test_task_transition_writes_audit_log():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        run_cli(["task", "approve", "T-1", "--done"])
        logs = local_facts.list_logs("w")
        kinds = [(l["type"], l["ref"]) for l in logs]
        assert ("task_transition", "T-1") in kinds


def test_task_reject_non_suspended_returns_one():
    with isolated_env():
        _make_in_progress()                       # 进行中, not 需审批
        rc, _, err = run_cli(["task", "reject", "T-1", "无效打回"])
        assert rc == 1
        assert "cannot reject" in err
        assert tasks.get("T-1")["status"] == "进行中"


def test_task_reject_rework_echoes_to_assignee_and_logs():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        run_cli(["task", "reject", "T-1", "方向错了"])
        # decision echoes back to the assignee inbox, tagged with task_id
        assert any(m["task_id"] == "T-1"
                   for m in local_facts.list_messages("w"))
        # and the transition is audited
        kinds = [(l["type"], l["ref"]) for l in local_facts.list_logs("w")]
        assert ("task_transition", "T-1") in kinds


def test_task_approve_done_notifies_manager():
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "update", "T-1", "--title", "ship it"])
        run_cli(["task", "update", "T-1", "--assignee", "w"])
        run_cli(["task", "update", "T-1", "--desc", "d"])
        tasks.update("T-1", assignee="w")  # keep deterministic local object for tests
        tasks.update("T-1", title="ship it")
        run_cli(["task", "pause", "T-1", "--by", "w"])
        rc, _, _ = run_cli(["task", "approve", "T-1", "--done", "--note", "OK"])
        assert rc == 0
        msgs = [m for m in local_facts.list_messages("manager") if m["task_id"] == "T-1"]
        assert msgs
        assert "任务完成回执" in msgs[-1]["content"]
        assert "已批准完成" in msgs[-1]["content"]


def test_task_pause_routes_to_explicit_approver_via_to():
    """`--to manager` sends the approval request to that inbox (not boss),
    and records awaiting=manager on the task."""
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1", "--note", "拍板", "--to", "manager"])
        assert tasks.get("T-1")["awaiting"] == "manager"
        assert any(m["task_id"] == "T-1"
                   for m in local_facts.list_messages("manager"))
        # boss inbox should NOT receive it when routed elsewhere
        assert not any(m["task_id"] == "T-1"
                       for m in local_facts.list_messages("user"))


def test_task_intent_create_empty_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "intent", "create", "   "])
        assert rc == 1
        assert "empty" in err


# ── gate: no CLI path may bypass the 需审批 suspend ────────────────


def test_task_done_shortcut_cannot_bypass_gate():
    """`task done` is sugar for `update --status 已完成`; on a suspended task
    it must hit the same gate and refuse."""
    with isolated_env():
        _make_in_progress()
        run_cli(["task", "pause", "T-1"])
        rc, _, err = run_cli(["task", "done", "T-1"])
        assert rc == 1
        assert "需审批" in err
        assert tasks.get("T-1")["status"] == "需审批"


def test_task_update_cannot_force_into_suspend_via_cli():
    with isolated_env():
        _make_in_progress()                       # 进行中
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "需审批"])
        assert rc == 1
        assert "需审批" in err
        assert tasks.get("T-1")["status"] == "进行中"


# ── reidentify fallback (G) ───────────────────────────────────────
# _refresh_anchor calls _reidentify_stale_anchor for every affected
# assignee. For CLIs that re-read their native file mid-session
# (claude/gemini) it's a no-op; for the rest (codex/qwen/kimi) it
# pushes init_prompt into an *idle* pane so the live intent anchor
# reaches an agent that won't re-read disk. Everything is best-effort:
# no failure mode may bubble up and fail the triggering task command.


def _capture_injects():
    """Return (sink, fake_inject) recording every tmux.inject call."""
    sink: list[dict] = []

    def fake_inject(target, text, *, submit_keys=None):
        sink.append({"target": target, "text": text, "submit_keys": submit_keys})

    return sink, fake_inject


def test_reidentify_stale_anchor_skips_reloading_cli():
    """claude-code re-reads its native file after /compact, so the disk
    rewrite already reaches it — no pane inject."""
    with isolated_env(team={"agents": {"worker_cc": {"cli": "claude-code"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(wake_mod, is_ready=lambda t, a: True):
            task_cmd._reidentify_stale_anchor("worker_cc")
        assert sink == []


def test_reidentify_stale_anchor_skips_manager_to_preserve_live_context():
    """Manager is a long-lived coordinator: task churn should not keep
    re-injecting the heavy init prompt into its live session."""
    with isolated_env(team={"agents": {"manager": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True):
            task_cmd._reidentify_stale_anchor("manager")
        assert sink == []


def test_reidentify_stale_anchor_injects_into_idle_non_reloading_pane():
    """codex does NOT re-read mid-session → an idle ready pane gets the
    init prompt re-injected (carrying the live anchor inline)."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(pp_mod, probe=lambda t: pp_mod.IDLE):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert len(sink) == 1
        assert sink[0]["target"].window == "worker_codex"
        assert sink[0]["text"]            # non-empty init prompt
        assert sink[0]["submit_keys"]     # codex submit keys threaded through


def test_reidentify_stale_anchor_skips_busy_pane():
    """A busy pane (mid-turn) must never be derailed — idle gate refuses."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(pp_mod, probe=lambda t: pp_mod.BUSY):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert sink == []


def test_reidentify_stale_anchor_skips_when_not_ready():
    """Dormant / dead pane (probe != IDLE) → nothing to inject into."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: True,
                        has_window=lambda t: True), \
             attr_patch(pp_mod, probe=lambda t: pp_mod.DEAD):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert sink == []


def test_reidentify_stale_anchor_best_effort_when_no_session():
    """No tmux session yet (agents not started) → silent no-op, no raise."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject,
                        has_session=lambda s: False,
                        has_window=lambda t: True), \
             attr_patch(wake_mod, is_ready=lambda t, a: True):
            task_cmd._reidentify_stale_anchor("worker_codex")
        assert sink == []


def test_reidentify_stale_anchor_swallows_unknown_agent():
    """A ghost agent (not in team.json) makes adapter_for_agent raise
    KeyError; best-effort contract swallows it so the task command that
    triggered the refresh still succeeds."""
    with isolated_env(team={"agents": {"worker_codex": {"cli": "codex-cli"}}}):
        sink, fake_inject = _capture_injects()
        with attr_patch(tmux_mod, inject=fake_inject):
            task_cmd._reidentify_stale_anchor("ghost")   # must not raise
        assert sink == []


# ── intent --by attribution ───────────────────────────────────────


def test_task_intent_create_by_attributes_creator():
    """--by stamps the real author; without it the intent stays 'user'
    (the boss-verbatim default)."""
    with isolated_env():
        run_cli(["task", "intent", "create", "manager 代记的派生需求",
                 "--by", "manager"])
        assert tasks.get_intent("I-1")["creator"] == "manager"
        run_cli(["task", "intent", "create", "老板逐字原话"])
        assert tasks.get_intent("I-2")["creator"] == "user"


# ── void ──────────────────────────────────────────────────────────


def test_task_void_retires_completed_task():
    with isolated_env():
        run_cli(["task", "create", "w", "重复活"])
        run_cli(["task", "done", "T-1"])
        rc, out, _ = run_cli(["task", "void", "T-1", "--note", "重复，作废",
                              "--by", "manager"])
        assert rc == 0 and "voided T-1" in out
        t = tasks.get("T-1")
        assert t["status"] == "已取消"
        assert t["approval_note"] == "重复，作废"


def test_task_void_unknown_or_already_cancelled_returns_one():
    with isolated_env():
        rc, _, err = run_cli(["task", "void", "T-404"])
        assert rc == 1 and "cannot void" in err
        run_cli(["task", "create", "w", "x"])
        run_cli(["task", "void", "T-1"])
        rc, _, err = run_cli(["task", "void", "T-1"])   # already 已取消
        assert rc == 1 and "cannot void" in err


def test_task_void_writes_audit_log():
    with isolated_env():
        run_cli(["task", "create", "w", "x"])
        run_cli(["task", "done", "T-1"])
        run_cli(["task", "void", "T-1", "--note", "作废"])
        kinds = [(l["type"], l["ref"]) for l in local_facts.list_logs("w")]
        assert ("task_transition", "T-1") in kinds


# ── auto-memory on task lifecycle ──────────────────────────────────


def test_create_auto_records_task_assigned_to_assignee():
    from claudeteam.store import memory
    with isolated_env():
        run_cli(["task", "intent", "create", "老板原话"])          # I-1
        run_cli(["task", "create", "worker_cc", "做 X", "--by", "manager", "--intent", "I-1"])
        rows = memory.list_recent("worker_cc")
    assert len(rows) == 1
    assert rows[0]["kind"] == "task_assigned"
    assert "T-1" in rows[0]["content"] and "做 X" in rows[0]["content"]
    assert rows[0]["ref"] == "T-1"


def test_done_auto_records_task_completed_once():
    from claudeteam.store import memory
    with isolated_env():
        run_cli(["task", "create", "worker_cc", "活"])
        run_cli(["task", "done", "T-1"])
        run_cli(["task", "done", "T-1"])      # idempotent re-assert
        rows = memory.list_recent("worker_cc")
    completed = [r for r in rows if r["kind"] == "task_completed"]
    assert len(completed) == 1               # NOT double-recorded
    assert "已完成" in completed[0]["content"] and completed[0]["ref"] == "T-1"


def test_pause_auto_records_blocker():
    from claudeteam.store import memory
    with isolated_env():
        run_cli(["task", "create", "worker_cc", "活"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        run_cli(["task", "pause", "T-1", "--note", "等老板拍板", "--to", "user"])
        rows = memory.list_recent("worker_cc")
    blockers = [r for r in rows if r["kind"] == "blocker"]
    assert len(blockers) == 1
    assert "需审批" in blockers[0]["content"] and "等老板拍板" in blockers[0]["content"]


def test_approve_done_auto_records_task_completed():
    from claudeteam.store import memory
    with isolated_env():
        run_cli(["task", "create", "worker_cc", "活"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        run_cli(["task", "pause", "T-1", "--note", "q"])
        run_cli(["task", "approve", "T-1", "--done", "--note", "ok"])
        rows = memory.list_recent("worker_cc")
    assert any(r["kind"] == "task_completed" and r["ref"] == "T-1" for r in rows)


def test_auto_memory_is_best_effort_never_fails_command():
    """A memory write blowing up must not fail the task command — _auto_memory
    swallows. Force memory.append to raise and verify create still rc==0."""
    from claudeteam.store import memory as memory_mod

    def boom(*a, **k):
        raise RuntimeError("disk full")
    with isolated_env():
        with attr_patch(memory_mod, append=boom):
            rc, out, _ = run_cli(["task", "create", "worker_cc", "活"])
        assert rc == 0 and "created T-1" in out
        assert tasks.get("T-1") is not None        # task itself persisted
