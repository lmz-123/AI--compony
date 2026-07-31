"""Tests for runtime/pane_probe.py — marker-free state (process + pane-diff)."""
from __future__ import annotations

from helpers import FakeProc
from claudeteam.runtime import pane_probe, tmux


_T = tmux.Target("S", "w")


def _fg(cmd):
    """Fake `run` whose `pane_command` (display-message) returns `cmd`."""
    return lambda args, **kw: FakeProc(returncode=0, stdout=cmd + "\n")


def _cap(values):
    """Fake capture_pane returning successive `values` (last repeats)."""
    box = {"i": 0}

    def cap(target, lines=40):
        v = values[min(box["i"], len(values) - 1)]
        box["i"] += 1
        return v
    return cap


_STILL = lambda _s: None


# ── pane_command (tmux helper) ───────────────────────────────────


def test_pane_command_strips_and_handles_failure():
    assert tmux.pane_command(_T, run=_fg("node")) == "node"
    assert tmux.pane_command(_T, run=lambda a, **k: FakeProc(returncode=1)) == ""


# ── probe: liveness via foreground process, busy via diff ────────


def test_dead_when_shell_foreground_and_static():
    """A shell sitting still = the CLI exited → DEAD."""
    assert pane_probe.probe(_T, run=_fg("bash"),
                            capture=_cap(["$ ", "$ "]), sleep=_STILL) == pane_probe.DEAD


def test_busy_not_dead_when_shell_foreground_but_changing():
    """Foreground momentarily a shell (CLI shelled out for a tool) but output
    is moving → BUSY, must NOT be reaped as dead."""
    assert pane_probe.probe(_T, run=_fg("bash"),
                            capture=_cap(["a", "b"]), sleep=_STILL) == pane_probe.BUSY


def test_idle_when_cli_foreground_and_static():
    """node/python foreground (CLI up) + static pane = IDLE."""
    assert pane_probe.probe(_T, run=_fg("node"),
                            capture=_cap(["x", "x"]), sleep=_STILL) == pane_probe.IDLE


def test_busy_when_cli_foreground_and_changing():
    assert pane_probe.probe(_T, run=_fg("node"),
                            capture=_cap(["x", "y"]), sleep=_STILL) == pane_probe.BUSY


def test_no_window_when_pane_command_empty():
    """display-message fails (no such pane) → NO_WINDOW, not a false DEAD."""
    run = lambda a, **k: FakeProc(returncode=1)
    assert pane_probe.probe(_T, run=run, capture=_cap([""]), sleep=_STILL) \
        == pane_probe.NO_WINDOW


def test_login_shell_prefix_is_recognised_as_shell():
    assert pane_probe.probe(_T, run=_fg("-zsh"),
                            capture=_cap(["% ", "% "]), sleep=_STILL) == pane_probe.DEAD


# ── probe_many: N panes, ONE shared sleep (the /team latency fix) ──


def test_probe_many_shares_one_sleep_and_classifies_all():
    a, b, c = tmux.Target("S", "a"), tmux.Target("S", "b"), tmux.Target("S", "c")
    fgs = {"a": "node", "b": "bash", "c": "node"}

    def run(args, **kw):
        win = args[args.index("-t") + 1].split(":")[-1]
        return FakeProc(returncode=0, stdout=fgs.get(win, "") + "\n")

    seqs = {"a": iter(["x", "x"]), "b": iter(["$", "$"]), "c": iter(["p", "q"])}

    def cap(target, lines=40):
        return next(seqs[target.window])

    sleeps = []
    out = pane_probe.probe_many([a, b, c], run=run, capture=cap,
                                sleep=lambda s: sleeps.append(s))
    assert out[a] == pane_probe.IDLE      # node + static
    assert out[b] == pane_probe.DEAD      # shell + static
    assert out[c] == pane_probe.BUSY      # node + moving
    assert len(sleeps) == 1               # ONE shared interval for all 3 panes


# ── changed_since (inject confirmation) ──────────────────────────


def test_changed_since_detects_movement():
    assert pane_probe.changed_since(_T, "old", capture=lambda t, lines=40: "new") is True
    assert pane_probe.changed_since(_T, "same", capture=lambda t, lines=40: "same") is False
