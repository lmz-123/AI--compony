"""End-to-end approval-gate integration test for the tasks feature.

Exercises the full architecture loop with a real (isolated) store:
    create_intent → create(intent_id) → 进行中 → pause (需审批) → inbox to boss
    → approve --done → inbox back to assignee → audit log present
plus the two reliability invariants the design promises:
    - verbatim re-read: intent.raw_text survives task churn byte-identical
    - the gate: a suspended task cannot be advanced by generic `task update`

Pure store + CLI; no tmux / Feishu. Mirrors the discipline in
test_inprocess_chain.py (real modules, isolated tempdir).
"""
from __future__ import annotations

from helpers import isolated_env, run_cli
from claudeteam.store import local_facts, tasks


def test_full_approval_lifecycle_and_invariants():
    with isolated_env():
        # 1. boss's verbatim ask is persisted immutably, task back-links it
        rc, out, _ = run_cli(["task", "intent", "create",
                              "把支付页改成两步结账", "--src", "msg_42"])
        assert rc == 0 and "I-1" in out
        run_cli(["task", "create", "dev", "重构结账流程", "--intent", "I-1"])
        assert tasks.get("T-1")["intent_id"] == "I-1"

        # 2. agent starts work, then hits a decision needing the boss
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        rc, _, _ = run_cli(["task", "pause", "T-1",
                            "--note", "两步还是三步？", "--by", "dev"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "需审批"

        # 3. an approval request reached the boss inbox, tagged with task_id
        boss_msgs = local_facts.list_messages("user")
        assert any(m["task_id"] == "T-1" for m in boss_msgs)

        # 4. THE GATE: while suspended, generic update must not advance it
        rc, _, err = run_cli(["task", "update", "T-1", "--status", "已完成"])
        assert rc == 1 and "需审批" in err
        assert tasks.get("T-1")["status"] == "需审批"

        # 5. boss approves-and-completes; decision echoes to the assignee
        rc, _, _ = run_cli(["task", "approve", "T-1", "--done"])
        assert rc == 0
        assert tasks.get("T-1")["status"] == "已完成"
        assert any(m["task_id"] == "T-1"
                   for m in local_facts.list_messages("dev"))

        # 6. VERBATIM RE-READ: original ask is still byte-identical after
        #    all the churn — this is the anti-drift guarantee
        assert tasks.get_intent("I-1")["raw_text"] == "把支付页改成两步结账"

        # 7. every transition is auditable / replayable
        logs = local_facts.list_logs("dev")
        refs = [(l["type"], l["ref"]) for l in logs]
        assert refs.count(("task_transition", "T-1")) >= 2
