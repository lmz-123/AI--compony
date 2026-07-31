"""Archive + restore an agent's workspace on fire / rehire.

Faithful restore of the pre-`0e1fbe5` `fire` behavior, adapted to the
current `state_dir` layout. When an agent is fired, its business-state
dir

    <state_dir>/agents/<name>/        (identity.md + memory.jsonl + …)

is moved to a dated archive

    <state_dir>/agents/_archived/<name>_<YYYYMMDD>/

alongside two records written into the archive dir:

  _termination.md   human-readable tombstone (who / when / executor +
                    the roster config at termination)
  _roster.json      the agent's team-config entry, stashed so a later
                    `claudeteam hire <name>` can re-add it to the roster
                    and resurrect the same agent.

Leaves the agent's `home/` subdir (the CLI's HOME — creds / cache) in
place: keeping it makes a rehire fast and needs no fresh login.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from claudeteam.runtime import paths

ROSTER_STASH = "_roster.json"       # parsed cfg dict (json) — source-agnostic fallback
ROSTER_BLOCK = "_roster.toml"       # verbatim [team.agents.<name>] toml block — primary
TERMINATION_RECORD = "_termination.md"

# An archive dir is named "<agent>_<YYYYMMDD>" or, on a same-day re-fire,
# "<agent>_<YYYYMMDD_HHMMSS>". This matches ONLY the date-stamp tail so
# find_archived doesn't mistake a longer-named agent's archive for this
# one (e.g. "alice_ai_20260613" must not match agent "alice").
_STAMP_RE = re.compile(r"\d{8}(_\d{6})?$")


def archived_root() -> Path:
    """Directory holding every fired agent's dated archive."""
    return paths.state_dir() / "agents" / "_archived"


def _termination_md(agent: str, cfg: dict, executor: str, stamp: datetime) -> str:
    exec_line = executor.strip() or "unknown (no CLAUDETEAM_ACTOR set)"
    cli = cfg.get("cli", "?") if cfg else "?"
    model = cfg.get("model", "?") if cfg else "?"
    role = cfg.get("role", "") if cfg else ""
    return (
        f"# Termination record — {agent}\n\n"
        f"- agent: `{agent}`\n"
        f"- fired_at: {stamp.isoformat(timespec='seconds')}\n"
        f"- executor: {exec_line}\n"
        f"- cli: `{cli}`\n"
        f"- model: `{model}`\n"
        f"- role: {role}\n\n"
        f"Restore with `claudeteam hire {agent}` (re-adds the roster entry "
        f"from `{ROSTER_STASH}` and moves this workspace back).\n"
    )


def archive_agent(agent: str, cfg: dict | None, *, block_text: str = "",
                  executor: str = "",
                  now: Callable[[], datetime] | None = None) -> Path:
    """Move `agent`'s workspace dir into a dated archive + write records.

    Returns the archive dir path. Idempotent-ish: a same-day re-fire gets
    a time-suffixed dir so a prior archive is never clobbered. If the
    agent has no workspace dir (already archived, never ran), the archive
    dir is still created to hold the termination record.

    `block_text` (the verbatim `[team.agents.<name>]` toml block) is stashed
    as `_roster.toml` for a byte-faithful rehire; `cfg` is also stashed as
    `_roster.json` as a source-agnostic fallback (legacy team.json, or a
    toml block we couldn't capture).
    """
    now = now or datetime.now
    stamp = now()
    root = archived_root()
    dst = root / f"{agent}_{stamp:%Y%m%d}"
    if dst.exists():  # fired twice in one day — keep both
        dst = root / f"{agent}_{stamp:%Y%m%d_%H%M%S}"
    src = paths.agent_dir(agent)
    dst.mkdir(parents=True, exist_ok=True)
    if src.exists() and src.is_dir():
        # Move business state into the tombstone but leave the `home/`
        # subdir (the CLI's dotfiles + cached credentials) in place so a
        # later `hire` is fast and needs no fresh login. Drop the now-empty
        # shell otherwise so it matches a never-provisioned agent.
        for item in src.iterdir():
            if item.name == "home":
                continue
            shutil.move(str(item), str(dst / item.name))
        try:
            src.rmdir()
        except OSError:
            pass  # `home/` (or other survivors) still inside — keep the dir
    if block_text:
        (dst / ROSTER_BLOCK).write_text(block_text, encoding="utf-8")
    if cfg:
        (dst / ROSTER_STASH).write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (dst / TERMINATION_RECORD).write_text(
        _termination_md(agent, cfg or {}, executor, stamp), encoding="utf-8")
    return dst


def find_archived(agent: str) -> Path | None:
    """Latest archive dir for `agent`, or None. Dirs are date-stamped so
    a lexical sort yields chronological order (the time-suffixed same-day
    variant sorts after the date-only one, which is what we want)."""
    root = archived_root()
    if not root.exists():
        return None
    prefix = f"{agent}_"
    matches = sorted(
        p for p in root.glob(f"{agent}_*")
        if p.is_dir() and _STAMP_RE.fullmatch(p.name[len(prefix):]))
    return matches[-1] if matches else None


def read_roster_stash(archive_dir: Path) -> dict:
    """Return the stashed roster cfg dict from an archive dir ({} if absent or
    unreadable — caller falls back to a default)."""
    f = archive_dir / ROSTER_STASH
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_roster_block(archive_dir: Path) -> str:
    """Return the verbatim `[team.agents.<name>]` toml block stashed at fire
    ("" if absent — caller falls back to the dict stash)."""
    try:
        return (archive_dir / ROSTER_BLOCK).read_text(encoding="utf-8")
    except OSError:
        return ""


def restore_workspace(archive_dir: Path, agent: str) -> None:
    """Move the archived business files back to `<state_dir>/agents/<name>/`.

    Skips the archive-only records (`_roster.json` / `_termination.md`) so
    the live workspace stays clean, and never clobbers an existing file in
    the destination. The archive dir is left in place as an audit tombstone.
    """
    dst = paths.agent_dir(agent)
    dst.mkdir(parents=True, exist_ok=True)
    for item in archive_dir.iterdir():
        if item.name in (ROSTER_STASH, ROSTER_BLOCK, TERMINATION_RECORD):
            continue
        target = dst / item.name
        if target.exists():
            continue
        shutil.move(str(item), str(target))
