"""Unified, marker-free pane-state probe.

Determines an agent CLI's state from signals that DON'T scrape the TUI's
banner / prompt text — which drifts per CLI and per version, and which we
never want to pattern-match for "is it busy":

  - liveness  : the pane's foreground process (`tmux #{pane_current_command}`,
                via `tmux.pane_command`). A shell (bash / zsh / …) in the
                foreground means the CLI has exited; anything else (node /
                python / … = the running CLI) means it's up.
  - busy/idle : whether the pane is *changing*. Capture twice a short interval
                apart; if the tail changed, the CLI is producing output
                (busy); if static, it's idle.

These compose so a shell foreground that is still churning (e.g. the CLI
shelled out for a tool) reads BUSY, not DEAD — only a shell sitting STATIC is
a genuinely dead pane.

This replaces the per-CLI `busy_markers` / `pane_state` regexes for the
alive / busy / idle / dead question. Deep "is it in the right state /
online" checks stay with the verify-status LLM skill (no fixed strings
there either).
"""
from __future__ import annotations

import time
from typing import Callable

from claudeteam.runtime import tmux


NO_WINDOW = "no_window"   # pane / window / session gone
DEAD = "dead"             # CLI exited — pane sitting static at a shell prompt
BUSY = "busy"             # pane is changing (producing output)
IDLE = "idle"             # CLI up, pane static (waiting for input)


# Foreground commands that mean "no CLI running" (back at a shell). tmux may
# prefix a login shell with '-'.
_SHELLS = frozenset({
    "bash", "zsh", "sh", "fish", "dash", "ash", "csh", "tcsh", "ksh",
})


def _is_shell(cmd: str) -> bool:
    return cmd.strip().lstrip("-").lower() in _SHELLS


def changed(target: tmux.Target, *, interval_s: float = 0.4,
            capture: Callable | None = None,
            sleep: Callable | None = None) -> bool:
    """True if the pane tail changed over `interval_s` — the CLI is emitting
    output (busy). Two captures, one short sleep apart."""
    capture = capture or tmux.capture_pane
    sleep = sleep or time.sleep
    before = capture(target, lines=40)
    sleep(interval_s)
    after = capture(target, lines=40)
    return before != after


def changed_since(target: tmux.Target, baseline: str, *,
                  capture: Callable | None = None) -> bool:
    """True if the current pane tail differs from `baseline`.

    Used to confirm an inject actually landed: capture a baseline, send the
    submit key, then check `changed_since` — the composer cleared / a spinner
    or output appeared. No content matching, just "did anything move"."""
    capture = capture or tmux.capture_pane
    return capture(target, lines=40) != baseline


def _classify(fg: str, busy: bool) -> str:
    """Foreground process + busy(motion) → state. Empty fg = no pane. A shell
    that's churning is BUSY (a tool is running), not DEAD — only a static
    shell prompt is DEAD."""
    if not fg:
        return NO_WINDOW
    if _is_shell(fg):
        return BUSY if busy else DEAD
    return BUSY if busy else IDLE


def probe(target: tmux.Target, *, interval_s: float = 0.4,
          run: Callable | None = None,
          capture: Callable | None = None,
          sleep: Callable | None = None) -> str:
    """Classify a pane as NO_WINDOW / DEAD / BUSY / IDLE without matching any
    TUI content string."""
    run = run or tmux._default_run
    fg = tmux.pane_command(target, run=run)
    if not fg:
        return NO_WINDOW
    busy = changed(target, interval_s=interval_s, capture=capture, sleep=sleep)
    return _classify(fg, busy)


def probe_many(targets, *, interval_s: float = 0.4,
               run: Callable | None = None,
               capture: Callable | None = None,
               sleep: Callable | None = None) -> dict:
    """Probe several panes sharing ONE `interval_s` sleep instead of N.

    Reads every pane's foreground + a first capture, sleeps ONCE, reads the
    second capture, classifies all. So N panes cost ~one interval, not N —
    this is what keeps /team from blocking the router event loop for N×0.4s.
    Returns {target: state}."""
    run = run or tmux._default_run
    capture = capture or tmux.capture_pane
    sleep = sleep or time.sleep
    targets = list(targets)
    fg: dict = {}
    before: dict = {}
    for t in targets:
        fg[t] = tmux.pane_command(t, run=run)
        before[t] = capture(t, lines=40) if fg[t] else ""
    sleep(interval_s)
    out: dict = {}
    for t in targets:
        after = capture(t, lines=40) if fg[t] else ""
        out[t] = _classify(fg[t], before[t] != after)
    return out
