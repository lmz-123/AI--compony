"""Layer A — automated, CI-runnable proof that a long-running task's verbatim
intent survives context compaction (the /compact anti-drift guarantee).

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
— and asserting the boss's verbatim ask is still byte-identical.

It canNOT prove the real model re-ingests the file; that is Layer B (container,
real Claude Code, real `/compact` + `/clear`), run by qa — steps in
`.claudeteam/agents/dev/proposal-compaction-survival-test.md`.

OBJECTIVE JUDGES (all byte-exact on a canary nonce, no subjective "do you
remember?"):
    A  承重墙在位   — harness reads the on-disk CLAUDE.md, asserts it carries
                      the verbatim raw_text incl. the nonce + the drop-prone
                      hard constraint.
    B  store 现读   — `tasks.get_intent` returns the raw_text byte-identical
                      after heavy task-field drift (the immutable ground truth).
    C  正确 task 态 — the active intent-task is recovered (not a stale/completed
                      sibling), via the task store.
NEGATIVE CONTROLS (prove the assertions can actually fail → they discriminate):
    - anchor-off: a non-active task does NOT leak its ask into the durable file.
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


def test_verbatim_intent_survives_simulated_compaction():
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

        # Judge A — 承重墙在位: durable file carries the verbatim ask
        cm = _claude_md("worker_cc")
        assert NONCE in cm
        assert RAW in cm                       # byte-identical, whole string
        assert CONSTRAINT in cm                # the drop-prone detail survived

        # Judge B — store 现读 ground truth unscathed by the drift
        assert tasks.get_intent("I-1")["raw_text"] == RAW

        # Judge C — recovered to the correct active task
        t = tasks.get("T-1")
        assert t["status"] == "进行中" and t["intent_id"] == "I-1"
        # drift lived only in mutable task fields, never in the anchored ask
        assert "支付改造" not in cm


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
        assert NONCE in _claude_md("worker_cc")          # active → anchored

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
        assert NONCE in cm and RAW in cm          # active ask present
        assert "ANCHOR-OTHER-9999" not in cm      # completed ask absent
        assert other not in cm
