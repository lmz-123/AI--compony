"""Layer A — automated, CI-runnable proof that a long-running task's active
task brief survives context compaction without leaking the boss's full original
ask into worker context.

WHAT THIS LAYER PROVES (and what it deliberately doesn't)
--------------------------------------------------------
A real `/compact` is a Claude Code runtime behaviour we can't invoke in-process.
What we CAN prove deterministically is the *substrate* that makes compaction
survivable: after compaction the only context that remains is (1) the agent's
always-loaded native `~/.claude/CLAUDE.md` (re-read every turn, never part of
the compacted transcript) and (2) the immutable intent store (`task intent
get`). This harness models "the conversation/init-prompt got compacted away"
by re-deriving the agent's loaded context *purely from those two durable
channels* — exactly what Claude Code re-reads on the first post-compaction turn
— and asserting workers get the current task brief while the boss's verbatim ask
stays out of always-loaded context.

It canNOT prove the real model re-ingests the file; that is Layer B (container,
real Claude Code, real `/compact` + `/clear`), run by qa — steps in
`.claudeteam/agents/dev/proposal-compaction-survival-test.md`.

OBJECTIVE JUDGES:
    A  承重墙在位   — harness reads the on-disk CLAUDE.md, asserts it carries
                      the current active task brief and does NOT carry the
                      boss's full raw_text.
    B  store 现读   — `tasks.get_intent` still returns the raw_text byte-identical
                      when a worker truly needs explicit lookup.
    C  正确 task 态 — the active intent-task is recovered (not a stale/completed
                      sibling), via the task store.
NEGATIVE CONTROLS (prove the assertions can actually fail → they discriminate):
    - anchor-off: a non-active task does NOT leak its ask/brief into the durable file.
    - done-drop : once the task completes, its now-stale ask vanishes from the
                  durable file (freshness — directly exercises the on-disk
                  refresh wiring).

Pure store + CLI + on-disk projection; no tmux / Feishu / real model.
"""
from __future__ import annotations

from pathlib import Path

from helpers import isolated_env, run_cli
from claudeteam.agents import identity
from claudeteam.runtime import paths
from claudeteam.store import tasks


# A canary the model could never emit on its own + a constraint a summariser is
# most likely to drop. The whole assertion reduces to "is this exact string
# still there?" → unambiguous, repeatable, zero human judgement.
NONCE = "[ANCHOR-7F3A2C9E]"
CONSTRAINT = "绝不加第三步"
RAW = f"把支付页改成两步结账：第一步选地址、第二步付款，{CONSTRAINT}。{NONCE}"

_TEAM = {"agents": {"worker_cc": {"cli": "claude-code", "model": "sonnet",
                                  "role": "员工"}}}


def _claude_md(agent: str) -> str:
    """The agent's always-loaded native memory file — the one channel that
    survives /compact. Read it the way Claude Code would on the next turn."""
    path = Path(paths.agent_home(agent)) / ".claude" / "CLAUDE.md"
    return path.read_text(encoding="utf-8")


def _anchor_section(text: str) -> str:
    if "## Active task anchor (brief only)" not in text:
        return ""
    tail = text.split("## Active task anchor (brief only)", 1)[1]
    return tail.split("## Memory maintenance", 1)[0]


def test_active_brief_survives_simulated_compaction_without_verbatim_leak():
    """Full Layer-A loop: an online worker on a long, drifting task; after we
    model the compaction (re-read durable channels only), all three objective
    judges hold byte-for-byte."""
    with isolated_env(team=_TEAM):
        # online worker (native CLAUDE.md provisioned)
        identity.write("worker_cc")

        # ① an active intent-task carrying the canary + drop-prone constraint
        run_cli(["task", "intent", "create", RAW])
        run_cli(["task", "create", "worker_cc", "重构结账流程", "--intent", "I-1"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])

        # ② model a long, drifting session: paraphrase the title, summarise the
        #    constraint out of the description, and pile on context noise — the
        #    sort of drift /compact produces. None of it may touch the intent.
        run_cli(["task", "update", "T-1", "--title", "支付改造"])
        run_cli(["task", "update", "T-1", "--desc", "两步结账即可"])  # drops 绝不加第三步
        for i in range(20):
            run_cli(["task", "create", "worker_cc", f"噪声任务{i}"])

        # ③ THE COMPACTION MODEL: ignore every prior prompt / transcript; re-read
        #    only the durable channels Claude Code would see post-/compact.

        # Judge A — 承重墙在位: durable file carries the active brief only.
        cm = _claude_md("worker_cc")
        anchor = _anchor_section(cm)
        assert anchor
        assert "两步结账即可" in anchor         # manager/task brief survived
        assert "intent I-1" in anchor
        assert NONCE not in anchor             # boss raw_text does not leak
        assert RAW not in anchor
        assert CONSTRAINT not in anchor

        # Judge B — store 现读 ground truth remains available on explicit lookup.
        assert tasks.get_intent("I-1")["raw_text"] == RAW

        # Judge C — recovered to the correct active task
        t = tasks.get("T-1")
        assert t["status"] == "进行中" and t["intent_id"] == "I-1"
        assert "支付改造" in anchor


def test_negative_control_inactive_task_leaks_no_verbatim():
    """Discriminating power: a task left 待处理 (anchor not engaged) puts NO
    verbatim ask in the durable file. So the positive test passes *because of*
    the anchor, not because the string is lying around anyway."""
    with isolated_env(team=_TEAM):
        identity.write("worker_cc")
        run_cli(["task", "intent", "create", RAW])
        run_cli(["task", "create", "worker_cc", "重构", "--intent", "I-1"])
        # never moved to 进行中 → not active → must not anchor
        cm = _claude_md("worker_cc")
        assert NONCE not in cm
        assert RAW not in cm


def test_completed_task_drops_verbatim_from_durable_file():
    """Freshness control (and a direct check of the on-disk refresh wiring):
    once the task completes, the stale ask must vanish from the durable file so
    a post-compaction reread can't resurrect a finished intent — while the
    immutable store keeps it for history."""
    with isolated_env(team=_TEAM):
        identity.write("worker_cc")
        run_cli(["task", "intent", "create", RAW])
        run_cli(["task", "create", "worker_cc", "重构", "--intent", "I-1"])
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        active_anchor = _anchor_section(_claude_md("worker_cc"))
        assert active_anchor
        assert "重构" in active_anchor
        assert NONCE not in active_anchor                # raw stays out

        run_cli(["task", "update", "T-1", "--status", "已完成"])
        cm = _claude_md("worker_cc")
        assert NONCE not in cm                            # stale ask dropped
        assert RAW not in cm
        assert tasks.get_intent("I-1")["raw_text"] == RAW  # store still has it


def test_recovers_active_intent_not_stale_completed_sibling():
    """Judge C, sharpened: with one active and one completed intent-task, the
    post-compaction durable context anchors ONLY the active ask — the agent
    comes back to the right task, never a finished one."""
    other = "把首页改成深色模式 [ANCHOR-OTHER-9999]"
    with isolated_env(team=_TEAM):
        identity.write("worker_cc")
        # active one
        run_cli(["task", "intent", "create", RAW])               # I-1
        run_cli(["task", "create", "worker_cc", "结账", "--intent", "I-1"])  # T-1
        run_cli(["task", "update", "T-1", "--status", "进行中"])
        # completed sibling
        run_cli(["task", "intent", "create", other])             # I-2
        run_cli(["task", "create", "worker_cc", "首页", "--intent", "I-2"])  # T-2
        run_cli(["task", "update", "T-2", "--status", "进行中"])
        run_cli(["task", "update", "T-2", "--status", "已完成"])

        cm = _claude_md("worker_cc")
        anchor = _anchor_section(cm)
        assert anchor
        assert "结账" in anchor and "intent I-1" in anchor  # active task brief present
        assert NONCE not in anchor and RAW not in anchor    # boss raw_text not injected
        assert "ANCHOR-OTHER-9999" not in anchor            # completed ask absent
        assert other not in anchor
        assert "首页" not in anchor                         # completed task brief absent
