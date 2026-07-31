"""Tests for commands/team.py — roster-filtered status display.

`team` reads status.json (a row for every agent that *ever* reported). The
display must be intersected with the CURRENT roster, else de-rostered / renamed
agents — or leftovers from an earlier config on the same state dir — appear as
phantom team members.
"""
from __future__ import annotations

import json

from claudeteam.store import local_facts
from helpers import isolated_env, run_cli


def test_team_shows_reported_roster_agents():
    team = {"agents": {"manager": {"cli": "claude-code"},
                       "worker_cc": {"cli": "claude-code"}}}
    with isolated_env(team=team):
        local_facts.upsert_status("manager", "running", "lead")
        local_facts.upsert_status("worker_cc", "standby", "idle")
        rc, out, _ = run_cli(["team"])
    assert rc == 0
    assert "manager" in out and "worker_cc" in out


def test_team_filters_phantom_non_roster_agents():
    """REGRESSION: a stale status.json row for an agent no longer in
    claudeteam.toml must NOT show up as a phantom team member."""
    team = {"agents": {"manager": {"cli": "claude-code"},
                       "worker_cc": {"cli": "claude-code"}}}
    with isolated_env(team=team):
        local_facts.upsert_status("manager", "running", "lead")
        local_facts.upsert_status("worker_cc", "running", "work")
        local_facts.upsert_status("worker_ghost", "running", "stale")  # not in roster
        rc, out, _ = run_cli(["team"])
    assert rc == 0
    assert "manager" in out and "worker_cc" in out
    assert "worker_ghost" not in out


def test_team_json_also_filters_to_roster():
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    with isolated_env(team=team):
        local_facts.upsert_status("manager", "running", "lead")
        local_facts.upsert_status("worker_ghost", "running", "stale")
        rc, out, _ = run_cli(["team", "--json"])
    assert rc == 0
    assert {r["agent"] for r in json.loads(out)} == {"manager"}
