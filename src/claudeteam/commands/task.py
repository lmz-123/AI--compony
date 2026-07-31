"""`claudeteam task <subcommand>`

  task create <assignee> <title> [--by <agent>] [--desc <text>] [--intent I-n]
  task update <id>       [--status S] [--assignee A] [--title T] [--desc D]
  task list              [--status S] [--assignee A]
  task get <id>
  task done <id>          (alias for `update <id> --status 已完成`)
  task pause <id>         [--note <why>] [--to <who>] [--by <agent>]
  task approve <id>       [--done]
  task reject <id> <feedback> [--cancel]
  task void <id>          [--note <why>] [--by <agent>]
  task intent create <raw...> [--src <msg_id>] [--key <points>] [--by <agent>]
  task intent get <I-n>
"""
from __future__ import annotations

from claudeteam.store import local_facts, tasks
from claudeteam.util import (
    error_exit, fmt_time_ms, maybe_print_help, pop_bool_flag, pop_flag,
    usage_error,
)


USAGE = (
    "usage:\n"
    "  claudeteam task create <assignee> <title> [--by <agent>] [--desc <text>] [--intent I-n]\n"
    "  claudeteam task update <id>  [--status S] [--assignee A] [--title T] [--desc D]\n"
    "  claudeteam task list  [--status S] [--assignee A]\n"
    "  claudeteam task get <id>\n"
    "  claudeteam task done <id>\n"
    "  claudeteam task pause <id> [--note <why>] [--to <who>] [--by <agent>]\n"
    "  claudeteam task approve <id> [--done] [--note <裁决内容>]\n"
    "  claudeteam task reject <id> <feedback> [--cancel]\n"
    "  claudeteam task void <id> [--note <why>] [--by <agent>]\n"
    "  claudeteam task intent create <raw...> [--src <msg_id>] [--key <points>] [--by <agent>]\n"
    "  claudeteam task intent get <I-n>"
)


def _refresh_anchor(*agents: str) -> None:
    """Re-project the on-disk native-memory anchor for the affected
    assignee(s) after a task transition, so an already-online worker
    doesn't keep a stale (or completed-task) anchor across /compact.
    Best-effort + only rewrites when the projection changed (see
    identity.refresh_native_memory).

    The disk rewrite only reaches the *running* agent for CLIs that
    re-read their native file mid-session (claude-code, gemini). CLIs that
    load it once at startup (codex/qwen/kimi) won't see the new anchor, so
    for them we additionally push a best-effort reidentify into an idle
    pane — see `_reidentify_stale_anchor`.

    Lazy import mirrors the registry's cycle-avoidance discipline."""
    from claudeteam.agents import identity
    seen: set[str] = set()
    for a in agents:
        if a and a not in seen:
            seen.add(a)
            identity.refresh_native_memory(a)
            _reidentify_stale_anchor(a)


def _reidentify_stale_anchor(agent: str) -> None:
    """For a CLI whose native memory file is NOT re-read mid-session
    (codex/qwen/kimi), the disk anchor rewrite never reaches the running
    agent — so re-inject the identity init prompt, which carries the live
    intent anchor inline. Only into an *idle* pane (ready and not busy) so
    we never derail an agent mid-turn.

    Entirely best-effort: any failure (no tmux session, no pane, busy
    pane, inject error, unknown agent/adapter) is swallowed so an anchor
    refresh can never make the triggering `task` command fail.

    Note: an agent running `claudeteam task done` on *itself* leaves its
    own pane busy (it's mid-command), so the idle gate skips it — no
    self-injection, no special case needed. Duplicate wakes (a task change
    that also delivered a message) are tolerated on purpose: re-waking is
    safer than a missed anchor, and dedup would need cross-process state."""
    try:
        from claudeteam.agents import adapter_for_agent, identity
        from claudeteam.runtime import config, tmux, wake
        adapter = adapter_for_agent(agent)
        if adapter.native_memory_reloads():
            return  # claude/gemini re-read the disk anchor themselves
        session = config.session_name()
        target = tmux.Target(session, agent)
        if not tmux.has_session(session) or not tmux.has_window(target):
            return
        from claudeteam.runtime import pane_probe
        if pane_probe.probe(target) != pane_probe.IDLE:
            return  # only inject at a quiet, alive, idle pane (probe: no markers)
        # Verified inject (escalates the submit key + confirms via pane motion)
        # instead of a bare fire-and-forget: a dropped submit on these
        # non-reload CLIs (codex/qwen/kimi) is what leaves the big identity
        # prompt wedged + piling up in the composer.
        wake.inject_and_confirm(target, adapter, identity.init_prompt(agent))
    except Exception:
        pass


def _fmt_task(t: dict) -> list[str]:
    ts = fmt_time_ms(t["created_at"])
    head = f"{t['id']}  [{t['status']}]  {t['title']}"
    body = [f"  assignee: {t.get('assignee') or '-'}"]
    if t.get("creator"):
        body.append(f"  by: {t['creator']}")
    if t.get("intent_id"):
        body.append(f"  intent: {t['intent_id']}")
    if t.get("description"):
        body.append(f"  desc: {t['description']}")
    if t.get("status") == tasks.SUSPEND_STATUS:
        body.append(f"  awaiting: {t.get('awaiting') or '-'}")
        if t.get("approval_note"):
            body.append(f"  note: {t['approval_note']}")
    body.append(f"  created: {ts}")
    return [head] + body


def _auto_memory(agent: str, kind: str, content: str, *, ref: str = "") -> None:
    """Auto-record a task-lifecycle event into the assignee's durable memory.

    The highest-value memories (assigned / done / blocked) were the ones most
    often MISSING, because memory.append's only writer is the manual
    `claudeteam remember` — nothing wrote on task transitions, so capture was
    left to the agent remembering to run the command (memory's core
    unreliability). This records them by CODE instead.

    Memory stays a best-effort *notepad* — the authoritative record is the
    task/intent store, not this. To avoid flooding (memory caps at ~200), we
    write ONE brief line per REAL state change only; content is title-capped.
    Best-effort: a memory write must never fail the task command."""
    if not agent:
        return
    try:
        from claudeteam.store import memory
        memory.append(agent, kind, content, ref=ref)
    except Exception:
        pass


def _mem_title(t: dict | None) -> str:
    """Short task label for a memory line (id + capped title)."""
    if not t:
        return ""
    title = (t.get("title") or "")[:50]
    return f"{t.get('id', '')} {title}".strip()


def _cmd_create(rest: list[str]) -> int:
    by = pop_flag(rest, "--by") or ""
    desc = pop_flag(rest, "--desc") or ""
    intent_id = pop_flag(rest, "--intent") or ""
    if len(rest) < 2:
        return usage_error(USAGE)
    assignee = rest[0]
    title = " ".join(rest[1:])
    try:
        tid = tasks.create(assignee, title, description=desc, creator=by,
                           intent_id=intent_id)
    except ValueError as e:
        return error_exit(f"❌ {e}")
    _refresh_anchor(assignee)
    intent_note = f" (intent {intent_id})" if intent_id else ""
    _auto_memory(assignee, "task_assigned", f"{tid} {title[:50]}{intent_note}", ref=tid)
    print(f"✅ created {tid}: {title} → {assignee}")
    return 0


def _cmd_update(rest: list[str]) -> int:
    status = pop_flag(rest, "--status")
    assignee = pop_flag(rest, "--assignee")
    title = pop_flag(rest, "--title")
    desc = pop_flag(rest, "--desc")
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    before = tasks.get(tid)
    try:
        ok = tasks.update(tid, status=status, assignee=assignee,
                          title=title, description=desc)
    except ValueError as e:
        return error_exit(f"❌ {e}")
    if not ok:
        return error_exit(f"❌ no such task: {tid}")
    after = tasks.get(tid)
    # status flips and reassignment both reshape the anchor; a reassign
    # moves it between two agents, so refresh both old and new owner.
    _refresh_anchor(before["assignee"] if before else "",
                    after["assignee"] if after else "")
    # Auto-memory only on a REAL transition INTO 已完成 (covers `task done`);
    # idempotent re-asserts (already 已完成) don't re-record.
    if (after and after.get("status") == "已完成"
            and (not before or before.get("status") != "已完成")):
        _auto_memory(after.get("assignee", ""), "task_completed",
                     f"{_mem_title(after)} 已完成", ref=tid)
    print(f"✅ updated {tid}")
    return 0


def _cmd_done(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    return _cmd_update([rest[0], "--status", "已完成"])


def _cmd_list(rest: list[str]) -> int:
    status = pop_flag(rest, "--status")
    assignee = pop_flag(rest, "--assignee")
    rows = tasks.list_tasks(status=status, assignee=assignee)
    if not rows:
        print("📋 no matching tasks")
        return 0
    print(f"📋 {len(rows)} tasks")
    for t in rows:
        for line in _fmt_task(t):
            print(line)
        print()
    return 0


def _cmd_get(rest: list[str]) -> int:
    if len(rest) < 1:
        return usage_error(USAGE)
    t = tasks.get(rest[0])
    if t is None:
        return error_exit(f"❌ no such task: {rest[0]}")
    for line in _fmt_task(t):
        print(line)
    return 0


def _cmd_pause(rest: list[str]) -> int:
    note = pop_flag(rest, "--note") or ""
    awaiting = pop_flag(rest, "--to") or "user"
    by = pop_flag(rest, "--by") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    if not tasks.pause(tid, awaiting=awaiting, approval_note=note, paused_by=by):
        return error_exit(f"❌ cannot pause {tid} (missing or not 进行中)")
    t = tasks.get(tid)
    local_facts.append_log(t["assignee"], "task_transition",
                           f"{tid} 进行中→需审批 (await {awaiting}): {note}", ref=tid)
    local_facts.append_message(awaiting, by or t["assignee"],
                               note or f"{tid} 需审批", priority="高", task_id=tid)
    _refresh_anchor(t["assignee"])
    _auto_memory(t.get("assignee", ""), "blocker",
                 f"{_mem_title(t)} 需审批(await {awaiting})"
                 + (f": {note}" if note else ""), ref=tid)
    print(f"⏸️  {tid} 需审批 — awaiting {awaiting}")
    return 0


def _cmd_approve(rest: list[str]) -> int:
    done = pop_bool_flag(rest, "--done")
    note = pop_flag(rest, "--note") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    if not tasks.approve(tid, done=done, note=note):
        return error_exit(f"❌ cannot approve {tid} (not 需审批)")
    t = tasks.get(tid)
    # Thread the verdict into the audit row and the assignee receipt so
    # "what was decided" travels the same gated channel as "decided" —
    # not a free-text relay that can lose the race against a worker
    # resuming from a question-only anchor.
    suffix = f": {note}" if note else ""
    local_facts.append_log(t["assignee"], "task_transition",
                           f"{tid} 需审批→{t['status']} (approved){suffix}", ref=tid)
    local_facts.append_message(t["assignee"], "user",
                               f"{tid} 已批准{'并完成' if done else '·继续'}{suffix}",
                               task_id=tid)
    _refresh_anchor(t["assignee"])
    # approve --done is a completion path → record it like `task done`.
    if done and t.get("status") == "已完成":
        _auto_memory(t.get("assignee", ""), "task_completed",
                     f"{_mem_title(t)} 已批准完成", ref=tid)
    print(f"✅ approved {tid} → {t['status']}")
    return 0


def _cmd_reject(rest: list[str]) -> int:
    cancel = pop_bool_flag(rest, "--cancel")
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    feedback = " ".join(rest[1:])
    if not tasks.reject(tid, feedback=feedback, cancel=cancel):
        return error_exit(f"❌ cannot reject {tid} (not 需审批)")
    t = tasks.get(tid)
    verb = "已取消" if cancel else "打回"
    local_facts.append_log(t["assignee"], "task_transition",
                           f"{tid} 需审批→{t['status']} ({verb}): {feedback}", ref=tid)
    local_facts.append_message(t["assignee"], "user",
                               f"{tid} {verb}: {feedback}", task_id=tid)
    _refresh_anchor(t["assignee"])
    print(f"↩️  rejected {tid} → {t['status']}")
    return 0


def _cmd_void(rest: list[str]) -> int:
    note = pop_flag(rest, "--note") or ""
    by = pop_flag(rest, "--by") or ""
    if len(rest) < 1:
        return usage_error(USAGE)
    tid = rest[0]
    before = tasks.get(tid)
    if not tasks.void(tid, reason=note, voided_by=by):
        return error_exit(f"❌ cannot void {tid} (missing or already 已取消)")
    t = tasks.get(tid)
    suffix = f": {note}" if note else ""
    prev = before["status"] if before else "?"
    local_facts.append_log(t["assignee"], "task_transition",
                           f"{tid} {prev}→已取消 (void){suffix}", ref=tid)
    _refresh_anchor(t["assignee"])
    print(f"🗑️  voided {tid} → 已取消")
    return 0


def _cmd_intent(rest: list[str]) -> int:
    if not rest:
        return usage_error(USAGE)
    action = rest[0]
    if action == "create":
        src = pop_flag(rest, "--src") or ""
        key = pop_flag(rest, "--key") or ""
        by = pop_flag(rest, "--by") or ""
        raw = " ".join(rest[1:])
        try:
            iid = tasks.create_intent(raw, source_msg=src, key_points=key,
                                      creator=by or "user")
        except ValueError as e:
            return error_exit(f"❌ {e}")
        print(f"✅ intent {iid}")
        return 0
    if action == "get":
        if len(rest) < 2:
            return usage_error(USAGE)
        intent = tasks.get_intent(rest[1])
        if intent is None:
            return error_exit(f"❌ no such intent: {rest[1]}")
        print(f"{intent['id']}  by {intent['creator']}")
        print(f"  raw: {intent['raw_text']}")
        if intent.get("key_points"):
            print(f"  key: {intent['key_points']}")
        return 0
    return usage_error(USAGE)


SUBCOMMANDS = {
    "create":  _cmd_create,
    "update":  _cmd_update,
    "done":    _cmd_done,
    "list":    _cmd_list,
    "get":     _cmd_get,
    "pause":   _cmd_pause,
    "approve": _cmd_approve,
    "reject":  _cmd_reject,
    "void":    _cmd_void,
    "intent":  _cmd_intent,
}


def main(argv: list[str]) -> int:
    if maybe_print_help(argv, USAGE):
        return 0
    if not argv:
        # No subcommand: print usage to stdout (it IS the requested output)
        # but return 1 so scripts know the call was incomplete.
        print(USAGE)
        return 1
    sub = argv[0]
    if sub not in SUBCOMMANDS:
        return error_exit(f"unknown task subcommand: {sub}\n{USAGE}")
    return SUBCOMMANDS[sub](list(argv[1:]))
