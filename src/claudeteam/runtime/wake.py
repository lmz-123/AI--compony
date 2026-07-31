"""Lazy wake: spawn an agent's CLI on demand.

Two ways an agent's pane can be in a state that's not yet ready to
receive a message:

1. Boss configured the agent as `lazy` in team.json — `claudeteam start`
   created the window but didn't spawn the CLI.  The pane is just a
   shell.
2. The CLI exited — Ctrl-C, /clear, OOM, network blip — and the watchdog
   either hasn't noticed yet or doesn't supervise this pane.

Either way, the next time deliver.apply() wants to inject, it should
detect "no CLI ready" and bring it up.  This module is the detection +
spawn step, kept pure-ish (collaborators injectable for tests).
"""
from __future__ import annotations

import time
from typing import Callable

from claudeteam.agents.base import CliAdapter
from claudeteam.runtime import pane_probe, tmux


def _has_marker(target: tmux.Target, markers: list[str],
                capture: Callable | None) -> bool:
    """Capture the pane (default tmux.capture_pane) and return True iff any
    string in `markers` appears. Empty marker list → always False (saves a
    capture call when the adapter declines to publish that marker class)."""
    if not markers:
        return False
    capture = capture or tmux.capture_pane
    text = capture(target, lines=80)
    return any(m in text for m in markers)


def is_ready(target: tmux.Target, adapter: CliAdapter, *,
             capture: Callable | None = None) -> bool:
    """True if the pane already shows one of the adapter's ready markers.

    This is a *readiness* check (has the CLI booted to its prompt), used by
    the spawn boot-wait and the deliver/send lazy-wake gate — not a busy
    check. Busy/idle/dead now come from the marker-free `pane_probe`.
    """
    return _has_marker(target, adapter.ready_markers(), capture)


# Onboarding dialogs claude pops on fresh ~/.claude.json (ephemeral
# per-container since the host bind-mount was dropped). Each dialog
# blocks the `bypass permissions on` ready marker, so we auto-press
# Enter to accept the default highlighted choice. Settings.json
# silent-launch flags suppress most onboarding paths, but a few
# dialogs ALWAYS show on a fresh state file regardless of settings.
_FIRST_LAUNCH_DIALOG_MARKERS = (
    "Choose the text style",                  # syntax theme picker
    "Claude account with subscription",       # auth method picker
    "Yes, I accept",                          # bypass-perms confirmation
    "Bypass Permissions mode",                # bypass-perms banner
    "Choose an option:",                      # generic onboarding prompt
    "Press Enter to continue",                # claude v2.1+ security notes; codewhale welcome (step 1/3)
    "trust this folder",                      # claude per-folder trust dialog (default = "Yes, I trust")
    "Press 1-7 to choose",                    # codewhale wizard language picker (step 2/3)
    "Press Enter to open",                    # codewhale wizard "open the workspace" (step 3/3)
)


def _poll_until_ready(target: tmux.Target, adapter: CliAdapter, *,
                      timeout_s: float, poll_interval_s: float,
                      capture: Callable, sleep: Callable, now: Callable) -> bool:
    """Loop `is_ready` checks until a ready marker shows up or `timeout_s`
    elapses. claude pops a chain of first-launch dialogs (theme
    picker, auth-method picker, bypass-perms confirm). Each dialog
    blocks the next, so we auto-press Enter every time we see ANY
    known dialog marker, throttled to once per second so we don't
    spam-press during a single dialog. Default-highlighted choice
    gets accepted; the next dialog appears; we Enter again until the
    bypass-permissions ready marker shows."""
    ready_markers = adapter.ready_markers()
    deadline = now() + timeout_s
    last_dismiss_at = 0.0
    while now() < deadline:
        text = capture(target, lines=80)
        if any(m in text for m in ready_markers):
            return True
        if any(m in text for m in _FIRST_LAUNCH_DIALOG_MARKERS):
            t = now()
            if t - last_dismiss_at >= 1.0:
                tmux.send_keys(target, "Enter")
                last_dismiss_at = t
        sleep(poll_interval_s)
    return False


def wait_until_ready(target: tmux.Target, adapter: CliAdapter, *,
                     timeout_s: float = 20.0,
                     poll_interval_s: float = 0.5,
                     capture: Callable | None = None,
                     sleep: Callable | None = None,
                     now: Callable | None = None) -> bool:
    """Poll the pane until a ready marker shows up. Does NOT spawn — use
    after a fresh `tmux.spawn_agent` to wait for the CLI banner before
    the next inject. Returns True if a marker appeared in time.
    """
    return _poll_until_ready(
        target, adapter,
        timeout_s=timeout_s, poll_interval_s=poll_interval_s,
        capture=capture or tmux.capture_pane,
        sleep=sleep or time.sleep,
        now=now or time.monotonic,
    )


def _wait_settled(target: tmux.Target, *, capture: Callable, sleep: Callable,
                  max_checks: int = 6, interval_s: float = 0.4) -> bool:
    """Block until the pane reads identical across one `interval_s` gap (the
    CLI finished painting / animating its startup banner), or `max_checks`
    elapse. Returns True if it settled, False if it timed out still moving.

    Why this exists: codex paints its startup banner TWICE before the input
    box is interactive. If we inject during that animation, two things break —
    the paste lands in a not-yet-ready composer, AND the post-inject motion
    check in `inject_and_confirm` reads the still-running banner redraw as
    "the CLI is streaming a reply = submitted", so the re-nudge never fires
    and the prompt sits unsubmitted (the "♥ never / initializing" bug).
    Bounded so a CLI that never goes fully quiet still proceeds (no worse than
    the pre-fix single inject)."""
    prev = capture(target, lines=40)
    for _ in range(max(1, max_checks)):
        sleep(interval_s)
        cur = capture(target, lines=40)
        if cur == prev:
            return True
        prev = cur
    return False


def inject_and_confirm(target: tmux.Target, adapter: CliAdapter, text: str, *,
                       attempts: int = 3, settle_s: float = 1.0,
                       inject: Callable | None = None,
                       capture: Callable | None = None,
                       send_keys: Callable | None = None,
                       sleep: Callable | None = None) -> bool:
    """Inject `text` into the pane and CONFIRM it actually submitted,
    re-nudging the submit key if it didn't.

    The respawn race: `tmux.inject` sends the submit key on a fixed ~200ms
    settle after the paste, but a *freshly* spawned/ready pane may not have
    ingested a large multi-line prompt yet, so the Enter / M-Enter is
    dropped and the text sits in the input box unsubmitted until a human
    presses Enter. This automates that nudge.

    Confirmation uses pane MOTION, never a busy-marker string: a submit that
    LANDED makes the pane keep changing (the CLI streams its reply), while a
    key that only inserted a newline into the composer leaves it static.
    `pane_probe.changed` measures motion AFTER the key (the key's one-shot
    effect is absorbed into its baseline), so it reads "is it processing".
    While static, re-send the primary submit key then escalate through the
    candidate keys, up to `attempts` times. Returns True once motion is
    observed, else False — the text was still injected, so the worst case is
    no worse than a plain inject.

    Used by the post-spawn identity-init inject in provision_pane and
    wake_if_dormant; collaborators are injectable for tests.
    """
    inject = inject or tmux.inject
    capture = capture or tmux.capture_pane
    send_keys = send_keys or tmux.send_keys
    sleep = sleep or time.sleep
    submit_keys = adapter.submit_keys() or ["Enter"]

    # Let the pane finish painting before injecting. Otherwise a CLI still
    # animating its startup banner (codex draws it twice) gets the paste into a
    # not-yet-interactive composer AND fools the motion check below into reading
    # the redraw as "submitted" — so the re-nudge never fires and the prompt
    # sits unsubmitted. Bounded; a never-quiet pane still proceeds.
    _wait_settled(target, capture=capture, sleep=sleep)

    inject(target, text, submit_keys=submit_keys)
    # Some CLIs (kimi) read a re-sent submit key as an interrupt — they opt
    # out of the re-nudge and keep the plain single inject (no worse than the
    # pre-fix behavior). Only claude/codex-style CLIs get verify+renudge.
    if not adapter.resubmit_on_idle():
        return True
    # Settle, then check for ongoing motion (= it's processing = submitted).
    # While static, re-nudge the primary key first (a freshly-ready pane often
    # just dropped the submit on a large multi-line paste), then ESCALATE
    # through the remaining candidate keys — one keypress per attempt. The
    # escalation recovers a CLI whose real submit key isn't the assumed
    # primary (e.g. a codex TUI that commits on a different key than M-Enter).
    for i in range(max(1, attempts)):
        sleep(settle_s)
        if pane_probe.changed(target, capture=capture, sleep=sleep):
            return True
        send_keys(target, submit_keys[min(i, len(submit_keys) - 1)])
    return pane_probe.changed(target, capture=capture, sleep=sleep)


def _default_is_retired(target: tmux.Target) -> bool:
    """Module-default retirement check: consult the agent's status row.
    The agent name is the tmux window name. Imported lazily so wake.py
    stays import-light for tests that inject their own check."""
    from claudeteam.store import local_facts
    return local_facts.is_retired(target.window)


def wake_if_dormant(target: tmux.Target, adapter: CliAdapter, *,
                    spawn_cmd: str,
                    init_msg: str | None = None,
                    on_woken: Callable[[], None] | None = None,
                    timeout_s: float = 30.0,
                    poll_interval_s: float = 0.5,
                    capture: Callable | None = None,
                    spawn: Callable | None = None,
                    inject: Callable | None = None,
                    is_retired: Callable[[tmux.Target], bool] | None = None,
                    sleep: Callable | None = None,
                    now: Callable | None = None) -> bool:
    """Ensure the agent's CLI is ready to receive input.

    Returns True iff the pane shows a ready marker (already awake, or
    woken in time).  Returns False on timeout — caller decides whether
    to inject anyway, queue, or surface to boss.

    A *retired* agent (status 已停止 — fired) returns False WITHOUT
    spawning: firing is an authoritative "stay down" signal, so neither
    a router delivery nor a peer `send` may silently revive the pane.
    The deliberate bring-back path is `claudeteam hire`, which goes
    through `provision_pane` (not this function) and clears the row.

    When the function had to actually spawn (pane was dormant on entry)
    AND `init_msg` is provided, it injects the identity/init prompt
    after the CLI shows ready, then calls `on_woken` (typically used
    to flip the agent's status row from "待命" to "进行中").
    """
    capture = capture or tmux.capture_pane
    spawn = spawn or tmux.spawn_agent
    inject = inject or tmux.inject
    is_retired = is_retired or _default_is_retired
    sleep = sleep or time.sleep
    now = now or time.monotonic

    # Retirement gate first — before the capture call — so a fired agent
    # is never revived regardless of what its (dead) pane currently shows.
    if is_retired(target):
        return False

    if is_ready(target, adapter, capture=capture):
        return True  # already awake — caller already handled identity at start

    if not spawn(target, spawn_cmd):
        return False

    # Give the CLI a beat to boot before checking — the pane was just
    # spawned; an immediate is_ready will always be False and burns a
    # capture-pane call.
    sleep(poll_interval_s)
    if not _poll_until_ready(target, adapter,
                             timeout_s=timeout_s, poll_interval_s=poll_interval_s,
                             capture=capture, sleep=sleep, now=now):
        return False

    # CLI just came up. Feed it the identity init prompt before whatever
    # real message follows, so the agent starts knowing who it is. Use
    # inject_and_confirm so a freshly-ready pane that drops the submit key
    # gets re-nudged instead of sitting unsubmitted.
    if init_msg:
        inject_and_confirm(target, adapter, init_msg,
                           inject=inject, capture=capture, sleep=sleep)
        sleep(poll_interval_s)
    if on_woken is not None:
        on_woken()
    return True
