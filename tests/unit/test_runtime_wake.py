"""Tests for runtime/wake.py — lazy wake of dormant CLI panes."""
from __future__ import annotations

from claudeteam.runtime import wake, tmux


class _ClaudeFake:
    """Minimal CliAdapter stand-in for tests."""
    def ready_markers(self):
        return ["bypass permissions on", "? for shortcuts"]

    def submit_keys(self):
        return ["Enter", "C-m", "C-j"]

    def resubmit_on_idle(self):
        return True


class _NoRenudgeFake(_ClaudeFake):
    """A CLI (like kimi) whose TUI reads a re-sent submit key as an interrupt
    → opts out of the autosubmit re-nudge."""
    def resubmit_on_idle(self):
        return False


def _capturer(text_per_call: list[str]):
    """Return a capture_pane fake that yields one text per call."""
    iterator = iter(text_per_call)

    def fake(target, lines=80):
        try:
            return next(iterator)
        except StopIteration:
            return ""
    return fake


# ── is_ready ─────────────────────────────────────────────────────


def test_is_ready_true_when_pane_shows_marker():
    target = tmux.Target("S", "manager")
    capture = _capturer(["welcome\nbypass permissions on\n>"])
    assert wake.is_ready(target, _ClaudeFake(), capture=capture) is True


# ── inject_and_confirm ────────────────


def test_inject_and_confirm_returns_when_pane_is_moving():
    """If the pane is MOVING after the inject (streaming a reply = submitted),
    confirm without re-nudging. Motion, not a busy-marker string."""
    injects = []
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hello",
        inject=lambda t, text, *, submit_keys=None: injects.append(text) or True,
        send_keys=lambda t, *k: sends.append(k),
        # settle sees a static pane first, THEN the confirm sees motion
        capture=_capturer(["calm", "calm", "frame-a", "frame-b"]),
        sleep=lambda s: None,
    )
    assert ok is True
    assert injects == ["hello"]   # injected once
    assert sends == []            # confirmed by motion; never re-nudged


def test_inject_and_confirm_renudges_then_confirms_motion():
    """Static after the inject (submit dropped) → re-send the primary key,
    then the pane starts moving = submitted."""
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hi",
        inject=lambda t, text, *, submit_keys=None: True,
        send_keys=lambda t, *k: sends.append(k),
        # settle (calm, calm); 1st check: static (unsubmitted); after re-nudge: moving
        capture=_capturer(["calm", "calm", "idle", "idle", "resp-1", "resp-2"]),
        sleep=lambda s: None,
    )
    assert ok is True
    assert sends == [("Enter",)]   # re-nudged once with submit_keys[0]


def test_inject_and_confirm_not_fooled_by_startup_banner_motion():
    """Regression (codex first-wake): a pane still animating its startup banner
    must NOT be read as 'submitted'. The pre-inject settle absorbs the banner
    motion; the post-inject static then correctly triggers a re-nudge — instead
    of the banner redraw faking a successful submit and leaving the prompt
    unsent (the '♥ never / initializing' bug)."""
    sends = []
    captures = [
        "banner-1", "banner-2",   # settle: banner still animating...
        "stable",   "stable",     # settle: quiesced → safe to inject
        "stable",   "stable",     # post-inject confirm: static → submit was eaten
        "resp-1",   "resp-2",     # after the re-nudge: moving = submitted
    ]
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hi",
        inject=lambda t, text, *, submit_keys=None: True,
        send_keys=lambda t, *k: sends.append(k),
        capture=_capturer(captures),
        sleep=lambda s: None,
    )
    assert ok is True
    assert sends == [("Enter",)]   # re-nudged once — not fooled by banner motion


def test_inject_and_confirm_optout_adapter_injects_once_no_renudge():
    """A CLI that opts out (resubmit_on_idle False, e.g. kimi) gets a plain
    single inject — never a re-nudge keypress (the re-sent key would be
    misread as an interrupt)."""
    injects = []
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _NoRenudgeFake(), "hi",
        inject=lambda t, text, *, submit_keys=None: injects.append(text) or True,
        send_keys=lambda t, *k: sends.append(k),
        capture=_capturer(["x", "x"]),
        sleep=lambda s: None,
    )
    assert ok is True
    assert injects == ["hi"]   # injected exactly once
    assert sends == []         # NEVER re-nudged (no interrupt risk)


def test_inject_and_confirm_gives_up_after_attempts():
    """Pane never moves (nothing submitted) → escalate `attempts` times then
    return False. The text was still injected, so no worse than a plain
    inject."""
    sends = []
    ok = wake.inject_and_confirm(
        tmux.Target("S", "w"), _ClaudeFake(), "hi",
        attempts=2,
        inject=lambda t, text, *, submit_keys=None: True,
        send_keys=lambda t, *k: sends.append(k),
        capture=_capturer(["static"] * 8),   # never moves → never confirms
        sleep=lambda s: None,
    )
    assert ok is False
    assert len(sends) == 2   # escalated exactly `attempts` times


# ── wake_if_dormant ──────────────────────────────────────────────


def test_wake_returns_true_when_already_ready_no_spawn():
    target = tmux.Target("S", "manager")
    capture = _capturer(["bypass permissions on\n>"])
    spawn_calls = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude --foo",
        capture=capture,
        spawn=lambda t, c: spawn_calls.append((str(t), c)) or True,
        is_retired=lambda t: False,
        sleep=lambda s: None,
    )
    assert ok is True
    assert spawn_calls == []


def test_wake_spawns_and_polls_until_ready():
    target = tmux.Target("S", "worker")
    # First check: dormant. Second check (post-spawn): still loading.
    # Third check: ready.
    captures = ["$ ", "$ loading...", "bypass permissions on\n>"]
    capture = _capturer(captures)
    spawn_calls = []
    sleeps = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: spawn_calls.append(c) or True,
        is_retired=lambda t: False,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert spawn_calls == ["claude"]
    assert len(sleeps) == 2  # slept twice while polling


def test_wake_returns_false_when_spawn_fails():
    target = tmux.Target("S", "worker")
    capture = _capturer(["$ "])
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: False,
        is_retired=lambda t: False,
        sleep=lambda s: None,
    )
    assert ok is False


# ── wait_until_ready (no spawn — pure polling) ────────────────────


def test_wait_until_ready_returns_true_immediately_when_already_ready():
    """No-spawn poll variant: if the marker is already there on first
    capture, no sleep happens — the loop checks then exits."""
    target = tmux.Target("S", "manager")
    capture = _capturer(["bypass permissions on\n>"])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert sleeps == []  # ready on first check, no sleep needed


def test_wait_until_ready_polls_with_sleep_then_returns_true():
    """When the marker appears on the second capture, exactly one sleep
    fires between the two checks."""
    target = tmux.Target("S", "manager")
    capture = _capturer(["$ ", "bypass permissions on\n>"])
    sleeps = []
    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: sleeps.append(s),
        timeout_s=5.0, poll_interval_s=0.1,
    )
    assert ok is True
    assert len(sleeps) == 1


def test_wait_until_ready_returns_false_on_timeout():
    """Marker never appears — function returns False after the deadline.
    Uses a fake clock so the test doesn't actually sleep through 20s."""
    target = tmux.Target("S", "manager")
    capture = lambda t, lines=80: "$ "  # always dormant
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    ok = wake.wait_until_ready(
        target, _ClaudeFake(), capture=capture,
        sleep=lambda s: None, now=now,
        timeout_s=1.0, poll_interval_s=0.1,
    )
    assert ok is False


def test_wake_returns_false_on_timeout():
    target = tmux.Target("S", "worker")
    # always dormant
    capture = lambda t, lines=80: "$ "
    # fake clock: each call advances by 0.5s; deadline is 1.0s.
    clock = {"t": 0.0}

    def now():
        clock["t"] += 0.5
        return clock["t"]

    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=capture,
        spawn=lambda t, c: True,
        is_retired=lambda t: False,
        sleep=lambda s: None,
        now=now,
        timeout_s=1.0, poll_interval_s=0.1,
    )
    assert ok is False


def test_wake_refuses_to_revive_retired_agent():
    """A fired agent (status 已停止) returns False without spawning OR
    capturing — firing is an authoritative 'stay down' signal."""
    target = tmux.Target("S", "worker_fired")
    capture_calls = []
    spawn_calls = []
    ok = wake.wake_if_dormant(
        target, _ClaudeFake(), spawn_cmd="claude",
        capture=lambda t, lines=80: capture_calls.append(t) or "",
        spawn=lambda t, c: spawn_calls.append(c) or True,
        is_retired=lambda t: True,
        sleep=lambda s: None,
    )
    assert ok is False
    assert spawn_calls == []   # never tried to revive
    assert capture_calls == []  # gated before the capture call too


def test_default_is_retired_reads_status_row():
    """The production default consults local_facts.is_retired keyed on the
    pane's window name. Uses the project's isolated_env helper (stdlib
    runner has no pytest tmp_path/monkeypatch fixtures)."""
    from helpers import isolated_env
    from claudeteam.store import local_facts
    with isolated_env():
        local_facts.upsert_status("worker_fired", "已停止", "fired")
        assert wake._default_is_retired(tmux.Target("S", "worker_fired")) is True
        assert wake._default_is_retired(tmux.Target("S", "worker_live")) is False
