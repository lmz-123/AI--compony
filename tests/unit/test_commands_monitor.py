"""Tests for read-only monitor snapshot."""
from __future__ import annotations

import json

from claudeteam.store import local_facts, tasks
from helpers import isolated_env, run_cli


def test_monitor_json_reports_agents_queue_and_daemons():
    team = {"session": "S", "agents": {
        "manager": {"cli": "claude-code", "model": "opus", "role": "主管"},
        "developer": {"cli": "claude-code", "model": "sonnet", "role": "开发"},
    }}
    with isolated_env(team=team, runtime_config={"chat_id": "oc_x"}):
        local_facts.upsert_status("manager", "进行中", "ready")
        local_facts.touch_heartbeat("manager")
        tasks.create("developer", "实现功能", description="brief", creator="manager")
        rc, out, err = run_cli(["monitor", "--json"])
    assert rc == 0, err
    data = json.loads(out)
    assert data["config"]["agent_count"] == 2
    assert data["config"]["chat_id_set"] is True
    assert data["queue"]["pending"] == 1
    agents = {row["agent"]: row for row in data["agents"]}
    assert agents["manager"]["status"] == "进行中"
    assert agents["manager"]["heartbeat_age_sec"] is not None
    assert agents["developer"]["pending_count"] == 1
    assert "router" in data["daemons"] and "watchdog" in data["daemons"]


def test_monitor_default_is_json_snapshot():
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}):
        rc, out, err = run_cli(["monitor"])
    assert rc == 0, err
    assert json.loads(out)["config"]["agent_count"] == 1
