"""Respawn agents whose CLI has exited.

The watchdog supervises the router daemon but NOT the agent CLIs running in
tmux panes. When an agent's CLI exits (crash, rate-limit, OOM, network blip),
its pane drops to a bare shell and stays dead until the next message lazily
wakes it — to a watching operator the agent "exited on its own". This module
lets the watchdog detect that (via `pane_probe` — process foreground + pane
motion, never TUI-string matching) and bring the agent back proactively.

Conservative: only a genuinely DEAD pane (CLI exited, shell sitting static)
is respawned. A CLI still showing a login / auth screen is *alive* (its
process is up, not a shell), so the probe reports it BUSY/IDLE — not DEAD —
and it's left alone (respawning can't fix expired creds, and would loop).
Lazy / retired agents are skipped, and a per-agent cooldown prevents
thrashing.
"""
from __future__ import annotations

import time
from typing import Callable

from claudeteam.runtime import pane_probe, tmux


def find_dead_agents(agents, *, session: str,
                     run: Callable | None = None,
                     capture: Callable | None = None,
                     sleep: Callable | None = None,
                     is_retired: Callable[[str], bool] | None = None,
                     lazy=frozenset()) -> list[str]:
    """Agents whose CLI has exited (`pane_probe.probe` == DEAD), excluding
    lazy (never-woken = also a shell) and retired (fired) agents."""
    dead = []
    for a in agents:
        if a in lazy:
            continue
        if is_retired is not None and is_retired(a):
            continue
        if pane_probe.probe(tmux.Target(session, a), run=run, capture=capture,
                            sleep=sleep) == pane_probe.DEAD:
            dead.append(a)
    return dead


def reap(agents, *, session: str, respawn: Callable[[str], bool],
         cooldown_s: float = 300.0,
         last_respawn: dict | None = None,
         now: Callable[[], float] | None = None,
         run: Callable | None = None,
         capture: Callable | None = None,
         sleep: Callable | None = None,
         is_retired: Callable[[str], bool] | None = None,
         lazy=frozenset(),
         log: Callable[[str], None] = print) -> list[str]:
    """Respawn DEAD agents that are past their per-agent cooldown.

    `last_respawn` is a dict mutated in place (agent → last respawn time);
    the caller keeps it across cycles so the cooldown survives. Returns the
    names respawned this call. `respawn(agent) -> bool` does the rebuild."""
    now = now or time.monotonic
    last_respawn = last_respawn if last_respawn is not None else {}
    out = []
    for a in find_dead_agents(agents, session=session, run=run, capture=capture,
                              sleep=sleep, is_retired=is_retired, lazy=lazy):
        t = now()
        prev = last_respawn.get(a)
        if prev is not None and (t - prev) < cooldown_s:
            log(f"  ⏳ {a} dead but within respawn cooldown "
                f"({cooldown_s:.0f}s) — leaving for lazy-wake")
            continue
        try:
            if respawn(a):
                last_respawn[a] = t
                out.append(a)
                log(f"  ♻️  respawned dead agent {a}")
            else:
                log(f"  ⚠️ respawn {a} returned failure")
        except Exception as e:
            log(f"  ⚠️ respawn {a} raised: {e}")
    return out
