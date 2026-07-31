"""Tests for runtime/agent_reaper.py — detect + respawn dead agent CLIs."""
from __future__ import annotations

from helpers import FakeProc
from claudeteam.runtime import agent_reaper


def _run(fg_by_window: dict):
    """Fake run: pane_command (display-message) returns each window's
    foreground process. Defaults to 'bash' (a dead shell) for unknown panes."""
    def run(args, **kw):
        target = args[args.index("-t") + 1]        # "S:<window>"
        win = target.split(":")[-1]
        return FakeProc(returncode=0, stdout=fg_by_window.get(win, "bash") + "\n")
    return run


_STATIC = lambda target, lines=40: "idle pane"     # same both samples → no motion
_STILL = lambda _s: None                            # no real diff sleep


# ── find_dead_agents ─────────────────────────────────────────────


def test_flags_only_exited_to_shell_panes():
    # worker_cc dropped to a shell (CLI exited); the others still run node.
    run = _run({"worker_cc": "bash", "manager": "node", "worker_x": "node"})
    dead = agent_reaper.find_dead_agents(
        ["manager", "worker_cc", "worker_x"], session="S",
        run=run, capture=_STATIC, sleep=_STILL)
    assert dead == ["worker_cc"]            # only the exited-to-shell pane


def test_skips_lazy_agents():
    """A never-woken lazy agent is also a bare shell — must not be respawned."""
    dead = agent_reaper.find_dead_agents(
        ["worker_lazy"], session="S",
        run=_run({"worker_lazy": "bash"}), capture=_STATIC, sleep=_STILL,
        lazy=frozenset({"worker_lazy"}))
    assert dead == []


def test_skips_retired_agents():
    dead = agent_reaper.find_dead_agents(
        ["worker_fired"], session="S",
        run=_run({"worker_fired": "bash"}), capture=_STATIC, sleep=_STILL,
        is_retired=lambda a: a == "worker_fired")
    assert dead == []


def test_alive_cli_on_auth_screen_is_not_reaped():
    """A CLI showing a login/auth screen still has its process up (node, not a
    shell), so the probe reports it alive — never reaped. A respawn can't fix
    expired creds and would just loop, and the probe gives us this for free
    without matching any auth string."""
    dead = agent_reaper.find_dead_agents(
        ["worker_cc"], session="S",
        run=_run({"worker_cc": "node"}), capture=_STATIC, sleep=_STILL)
    assert dead == []


# ── reap (respawn + cooldown) ────────────────────────────────────


def _dead_run():
    return _run({"worker_cc": "bash"})


def test_reap_respawns_dead_and_records_time():
    respawned = []
    last: dict = {}
    out = agent_reaper.reap(
        ["worker_cc"], session="S",
        run=_dead_run(), capture=_STATIC, sleep=_STILL,
        respawn=lambda a: respawned.append(a) or True,
        last_respawn=last, now=lambda: 1000.0, log=lambda _m: None)
    assert out == ["worker_cc"]
    assert respawned == ["worker_cc"]
    assert last["worker_cc"] == 1000.0


def test_reap_skips_within_cooldown():
    respawned = []
    last = {"worker_cc": 1000.0}
    out = agent_reaper.reap(
        ["worker_cc"], session="S",
        run=_dead_run(), capture=_STATIC, sleep=_STILL,
        respawn=lambda a: respawned.append(a) or True,
        cooldown_s=300.0, last_respawn=last,
        now=lambda: 1200.0,            # only 200s later — still cooling down
        log=lambda _m: None)
    assert out == []
    assert respawned == []


def test_reap_respawns_again_after_cooldown_elapses():
    last = {"worker_cc": 1000.0}
    out = agent_reaper.reap(
        ["worker_cc"], session="S",
        run=_dead_run(), capture=_STATIC, sleep=_STILL,
        respawn=lambda a: True,
        cooldown_s=300.0, last_respawn=last,
        now=lambda: 1400.0,            # 400s later — past cooldown
        log=lambda _m: None)
    assert out == ["worker_cc"]
    assert last["worker_cc"] == 1400.0


def test_reap_swallows_respawn_failure():
    def boom(_a):
        raise RuntimeError("spawn blew up")
    out = agent_reaper.reap(
        ["worker_cc"], session="S",
        run=_dead_run(), capture=_STATIC, sleep=_STILL,
        respawn=boom, last_respawn={}, now=lambda: 1.0, log=lambda _m: None)
    assert out == []                   # error swallowed, no crash
