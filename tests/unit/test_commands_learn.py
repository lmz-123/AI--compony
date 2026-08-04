"""Tests for `claudeteam learn` — reviewable task learning drafts."""
from __future__ import annotations

import json

from helpers import isolated_env, run_cli
from claudeteam.store import learning, local_facts, memory, tasks, team_memory


def _finished_task():
    tasks.create("deployer", "发布并打包 APK", creator="manager",
                 description="push + deploy + build artifact")
    tasks.update("T-1", status="进行中")
    local_facts.append_message("deployer", "manager",
                               "请用固定脚本部署，交付下载链接和 SHA256",
                               task_id="T-1")
    local_facts.append_log("deployer", "deploy",
                           "T-1 deploy ok; artifact download_url ready", ref="T-1")
    tasks.update("T-1", status="已完成")


def test_learn_task_creates_reviewable_draft_from_task_trace():
    with isolated_env():
        _finished_task()
        rc, out, err = run_cli(["learn", "task", "T-1"])
        assert rc == 0, err
        assert "learning draft L-1" in out
        draft = learning.get("L-1")
        assert draft is not None
        assert draft["task_id"] == "T-1"
        assert draft["suggested_agent"] == "deployer"
        assert draft["kind"] == "task_completed"
        assert draft["evidence"]
        assert "下载链接" in draft["lesson"] or "artifact" in draft["lesson"]


def test_task_done_auto_creates_learning_draft_once():
    with isolated_env():
        tasks.create("developer", "修复 UI")
        tasks.update("T-1", status="进行中")
        rc, _, err = run_cli(["task", "done", "T-1"])
        assert rc == 0, err
        rows = learning.list_drafts()
        assert len(rows) == 1
        assert rows[0]["task_id"] == "T-1"
        # Re-running learn task without --force returns the existing draft.
        run_cli(["learn", "task", "T-1"])
        assert len(learning.list_drafts()) == 1


def test_learn_list_and_get_json():
    with isolated_env():
        _finished_task()
        run_cli(["learn", "task", "T-1"])
        rc, out, err = run_cli(["learn", "list", "--json"])
        assert rc == 0, err
        rows = json.loads(out)
        assert rows[0]["id"] == "L-1"

        rc, out, err = run_cli(["learn", "get", "L-1", "--json"])
        assert rc == 0, err
        assert json.loads(out)["task_id"] == "T-1"


def test_learn_promote_to_agent_memory():
    with isolated_env():
        _finished_task()
        run_cli(["learn", "task", "T-1"])
        rc, out, err = run_cli(["learn", "promote", "L-1", "--agent", "deployer"])
        assert rc == 0, err
        assert "promoted L-1" in out
        rows = memory.list_recent("deployer")
        assert rows and rows[-1]["ref"] == "T-1"
        assert learning.get("L-1")["status"] == "promoted"


def test_learn_promote_to_team_experience_with_pin():
    with isolated_env():
        _finished_task()
        run_cli(["learn", "task", "T-1"])
        rc, out, err = run_cli(["learn", "promote", "L-1", "--team", "--pin"])
        assert rc == 0, err
        assert "team E-1" in out
        rows = team_memory.list_recent()
        assert rows[0]["pin"] is True
        assert rows[0]["ref"] == "T-1"


def test_learn_skill_draft_writes_review_file():
    with isolated_env() as tmp:
        _finished_task()
        run_cli(["learn", "task", "T-1"])
        rc, out, err = run_cli(["learn", "skill-draft", "L-1", "--skill", "deploy-tripcanvas"])
        assert rc == 0, err
        assert "skill draft written" in out
        path = tmp / "state" / "learn" / "skill-drafts" / "deploy-tripcanvas-L-1.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "Proposed SKILL.md addition" in text
        assert "T-1" in text
