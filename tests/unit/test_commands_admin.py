"""Tests for the writable admin dashboard helpers."""
from __future__ import annotations

import json

from helpers import attr_patch, isolated_env, run_cli
from claudeteam.commands import admin
from claudeteam.runtime import config, tmux
from claudeteam.store import local_facts, radio, tasks


def test_admin_state_includes_roster_and_known_clis():
    with isolated_env(team={"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}):
        data = admin._state()
        assert data["roster"]["session"] == "S"
        assert data["roster"]["agents"]["manager"]["cli"] == "codex-cli"
        assert "codex-cli" in data["roster"]["known_clis"]
        assert data["learning"]["drafts"] == 0


def test_admin_cli_default_prints_json_state():
    with isolated_env(team={"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}):
        rc, out, err = run_cli(["admin"])
    assert rc == 0, err
    data = json.loads(out)
    assert data["roster"]["agents"]["manager"]["cli"] == "codex-cli"


def test_agent_detail_includes_pane_inbox_tasks_and_logs():
    with isolated_env(team={"session": "S", "agents": {"developer": {"cli": "codex-cli"}}}):
        tasks.create("developer", "do work")
        local_facts.append_message("developer", "manager", "hello")
        radio.append_update("developer", "T-1", "msg_1", "manager", "radio hello")
        local_facts.append_log("developer", "note", "log row")
        with attr_patch(tmux,
                        has_session=lambda session: True,
                        has_window=lambda target: True,
                        capture_pane=lambda target, lines=160: "pane text"):
            data = admin._agent_detail("developer")
        assert data["pane"]["text"] == "pane text"
        assert data["tasks"][0]["title"] == "do work"
        assert data["inbox"][0]["content"] == "hello"
        assert data["radio"][0]["task_id"] == "T-1"
        assert data["radio_updates"][0]["summary"] == "radio hello"
        assert data["logs"][0]["content"] == "log row"


def test_create_agent_adds_roster_and_hires_when_session_running():
    with isolated_env(team={"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}):
        calls = []
        with attr_patch(tmux, has_session=lambda session: True), attr_patch(admin.hire, main=lambda argv: calls.append(argv) or 0):
            out = admin._create_agent({
                "name": "ops2",
                "cli": "codex-cli",
                "model": "gpt-5.4-mini",
                "role": "ops helper",
            })
        assert out["lifecycle"] == "hired"
        assert calls == [["ops2"]]
        assert config.agent_config("ops2")["model"] == "gpt-5.4-mini"


def test_update_agent_restarts_when_requested():
    with isolated_env(team={"session": "S", "agents": {"ops2": {"cli": "codex-cli"}}}):
        calls = []
        with attr_patch(admin.restart, main=lambda argv: calls.append(argv) or 0):
            out = admin._update_agent("ops2", {"model": "gpt-5.4", "restart": True})
        assert out["lifecycle"] == "restarted"
        assert calls == [["ops2"]]
        assert config.agent_config("ops2")["model"] == "gpt-5.4"


def test_delete_agent_refuses_manager():
    with isolated_env(team={"session": "S", "agents": {"manager": {"cli": "codex-cli"}}}):
        try:
            admin._delete_agent("manager")
        except ValueError as e:
            assert "manager" in str(e)
        else:
            raise AssertionError("expected ValueError")
