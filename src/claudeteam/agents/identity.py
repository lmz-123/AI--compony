"""Render per-agent identity markdown.

Each agent gets a small markdown file at
    $CLAUDETEAM_STATE_DIR/agents/<name>/identity.md
that the agent's CLI reads on demand to learn:
  - who it is and what role
  - which command format to use for talking back (claudeteam send / say
    / status / log / remember / recall / peek + the argument-order rules
    that LLMs habitually mis-order)
  - which CLI it's running under (so adapter quirks like Codex's
    M-Enter don't surprise it)
  - cross-agent management discipline (manager body only — role
    boundaries / instant-ack-then-close-the-loop / inspect-and-verify /
    communication format / requirements discipline / external systems /
    collective orders must dispatch)

The text is interpolated from the agent's claudeteam.toml entry —
there's no external template file to edit; the canonical copy lives
in this module as `_MANAGER_BODY` / `_WORKER_BODY`.

`init_prompt(agent)` is the wake message injected into a fresh /
cleared pane. It also appends the agent's recent durable memory (via
`memory.render_for_prompt`) so a /clear-ed pane picks up prior
context. Empty memory → no extra section.

The manager's inspect-and-verify cadence uses `claudeteam peek <agent>`
rather than raw `tmux capture-pane`.
"""
from __future__ import annotations

from pathlib import Path

from claudeteam.runtime import config, paths
from claudeteam.store import memory, team_memory
from claudeteam.util import atomic_write_text


# Shared section: every role's identity needs this guardrail. Keeping it
# in one constant means any tweak (new env vars, more failure modes) only
# happens once and both bodies stay in sync automatically.
_WORKDIR_RULE = """\
## Working directory rule

Run `claudeteam …` commands from the pane's current directory; do NOT `cd` or prefix them
with `cd /elsewhere &&`: runtime_config.json lives beside the spawned pane, and a
different CWD can make chat_id / lark_profile disappear."""


# Standing "maintain your own memory" policy. Appended to the CLI-native
# always-loaded memory file (claude's ~/.claude/CLAUDE.md) so the
# instruction is in context on every turn and survives /compact — unlike
# the one-shot init prompt, which the CLI's own context compaction can
# summarise away. We tell the agent WHEN to call `claudeteam remember`
# rather than auto-extracting from logs: the agent is the best judge of
# what's worth keeping, and the trigger list is deliberately bounded so a
# hot agent doesn't flood its 200-entry window with low-value notes.
_MEMORY_POLICY = """\
## Memory maintenance

Use `claudeteam remember <you> <kind> "<one sentence>"` only for facts worth
re-reading after restart: `decision`, `learning`, `blocker`, `task_completed`.
Managers may record `task_assigned`. Do not remember micro-steps, logs, secrets,
or duplicates.

Shared facts: add `--team --pin` only for a small number of team-wide hard facts;
otherwise pull with `claudeteam recall --team --grep <keyword>` when needed."""


# Team working principles every agent is born with. Shared (like
# _WORKDIR_RULE) so manager + worker stay in sync, and injected into the
# identity body — which means it reaches every CLI via identity.md AND
# lands in claude's always-loaded ~/.claude/CLAUDE.md (survives /compact),
# because native_memory_text() renders from this same body. These encode
# the intent→task→approval discipline the tasks feature exists to enforce:
# verbal "ok" is not a state transition, and the boss's verbatim ask must
# never be paraphrased away.
_TEAM_PRINCIPLES = """\
## Team principles

- Formal work goes through the task queue: `task create ... --desc ...` then
  `task dispatch-next <worker> --by manager`. Use `--intent I-n` for recorded
  boss requirements; keep the boss's raw words in intent only, not worker context.
- If approval or business clarification is needed, pause/ask; never invent a
  decision. `task pause/approve/reject` carries the verdict.
- Worker handles one T-n at a time. Different T-n messages stay unread until the
  manager dispatches them.
- On completion: `task done T-n` and report manager with result + evidence.
- Say/send basics: `send <recipient> <sender> "<msg>"`; `say <agent> "<msg>" --to user|manager`.
- For command details use `claudeteam <command> --help`; for role details read
  the assigned playbook only when the task needs it."""


# Compact runtime identity bodies. Detailed role workflows live in per-agent
# playbooks and are read on demand, so trivial wakes do not pay for them.

_MANAGER_BODY = """\
# {name} — {role}

You are **{name}**, the team manager, running on **{cli}** (model: `{model}`).
Mirror the boss's language.

## Core job

Decide, break down, dispatch, chase blockers, accept results, and summarize to the boss.
The iron rule of management dispatch: assign execution to workers; you manage.
You are the boss's sole interface: boss input is interpreted by you, then routed to workers as task briefs.
Do not personally execute worker work.

## Red lines

1. Work expected to take >1 minute (reading code, grep, testing, editing, push, deploy, research) goes to the right worker through the task queue.
2. First boss reply is always short: `已收到，我先<动作>，完成后向你汇报。`
3. Dispatch clear goals / acceptance / boundaries; do not over-prescribe implementation steps.
4. Use the minimum useful agents, but run independent subtasks in parallel.
5. Stay quiet after dispatch except completion, blocker/risk, or boss status request. No periodic peek loops.
6. Never post a worker's response on its behalf.

## Essential commands

```bash
claudeteam inbox manager
claudeteam task create <worker> "<title>" --by manager --desc "<brief>" [--intent I-n]
claudeteam task dispatch-next <worker> --by manager
claudeteam send <recipient> <sender> "<msg>"
claudeteam send <recipient> manager "<msg>"
claudeteam say manager "<msg>" --to user
claudeteam team
claudeteam peek <worker> [N]
```

Use single quotes around messages containing code/backticks/shell syntax.
For command details use `claudeteam <command> --help`.

{workdir_rule}

{team_principles}

## Dispatch rhythm

Boss message → short acknowledgement → split into independent subtasks → create queued tasks with clear briefs → dispatch free workers → accept worker results → one concise final summary.

If a business rule, environment, data action, release approval, or acceptance criterion is unclear, ask the boss before that affected subtask continues. Clear independent subtasks may continue.
"""


_WORKER_BODY = """\
# {name} — {role}

You are **{name}**, a team worker. Your role is **{role}**, running on **{cli}** (model: `{model}`).
Mirror the boss's language.

## Core job

Pick up exactly one assigned task, do the role-specific work, validate what you changed, then report result/evidence to manager. Do not take over another role's work.
Pick up tasks only from your inbox / current task anchor.

## Essential commands

```bash
claudeteam inbox {name}
claudeteam read <local_id>
claudeteam status {name} 进行中 "<task>"
claudeteam send manager {name} "<update>"
claudeteam say {name} "<msg>" --to manager
claudeteam say {name} "<msg>" --to user
claudeteam task done <T-id>
```

Argument order matters: `send <recipient> <sender>`, and `say <agent> ... --to user|manager`.
Use `claudeteam <command> --help` for details.

## Task isolation

- Treat one T-n as your only active work context.
- Read only the current dispatch, same-T-n supplements, and this task's recent result/evidence.
- Leave different-T-n messages unread while busy; tell manager you are busy.
- After completion, close the task and do not carry old details into the next task unless manager explicitly links them.

{workdir_rule}

{team_principles}
"""


def _render_specialty_section(specialty: list[str]) -> str:
    """Optional specialty block. Empty list → empty string (no section)."""
    if not specialty:
        return ""
    items = "\n".join(f"- {s}" for s in specialty)
    return f"\n\n## Specialty\n\n{items}"


def _render_tone_section(tone: str) -> str:
    if not tone:
        return ""
    return f"\n\n## Style\n\n{tone}"


def _render_notes_section(notes: str) -> str:
    if not notes:
        return ""
    return f"\n\n## Notes\n\n{notes}"


def _read_playbook(playbook: str) -> str:
    """Read an agent's playbook file — a role instruction doc that becomes the
    bulk of its identity. Path is relative to the config file's directory so a
    copied template folder stays self-contained; absolute paths pass through.
    Missing / unreadable degrades to "" (never break a spawn over a typo'd path)."""
    p = Path(playbook)
    if not p.is_absolute():
        p = paths.config_file().parent / p
    try:
        return p.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        # missing / unreadable / non-UTF-8 (binary) → degrade, never crash a spawn
        return ""


def _render_playbook_section(playbook: str) -> str:
    """Render a pointer to the agent playbook, not the whole file.

    The playbook used to be injected verbatim into every identity/native memory
    file. That made even trivial wakes pay for a long role manual. Keep the path
    resident and let the agent read it on demand when a task actually needs the
    role workflow.
    """
    if not playbook:
        return ""
    p = Path(playbook)
    resolved = p if p.is_absolute() else paths.config_file().parent / p
    exists = _read_playbook(playbook) != ""
    availability = "available" if exists else "missing/unreadable"
    return (
        "\n\n## Role playbook (read on demand)\n\n"
        f"- Path: `{resolved}` ({availability}).\n"
        "- Read it before substantive role work such as requirement dispatch, development, ops diagnosis, deploy, or build. "
        "Skip it only for trivial status, inbox cleanup, or one-line coordination."
    )


def _render_workspace_section(agent: str) -> str:
    """Per-agent private scratch area. Absolute path so it resolves from
    any pane CWD (claude / codex / ... spawn from different dirs)."""
    ws = paths.agent_workspace(agent)
    return (
        f"\n\n## Your private workspace\n\n"
        f"`{ws}` is your exclusive directory. Long reports / drafts / temp files / big log blocks go here——"
        f"**do not** pile them in the shared repo root (you'll collide with other workers). In the group, post only a summary + the path here."
    )


def _render_skills_section() -> str:
    """Pointer to the committed, reusable skills index. Static — the index
    teaches *which* skill *when*; agents read the actual SKILL.md on demand."""
    return (
        "\n\n## Reusable skills library (skills/)\n\n"
        "The team maintains reusable workflows in the repo's `skills/`, one directory per skill. When you hit a matching scenario,"
        "first read `skills/README.md` to pick one, then follow the steps in the corresponding `SKILL.md`;"
        "don't grope from scratch through a workflow that's already been distilled."
    )


def _render_team_specialties_block() -> str:
    """For manager prompt: list each non-manager agent's specialty so
    manager can dispatch with awareness. Empty if no agent has specialty."""
    try:
        team = config.load_team()
    except Exception:
        return ""
    rows = []
    for name, cfg in (team.get("agents") or {}).items():
        if name == "manager":
            continue
        spec = cfg.get("specialty") or []
        if spec:
            rows.append(f"- **{name}** specializes in: " + " / ".join(spec))
    if not rows:
        return ""
    return "\n\n## Team members' specialties (dispatch reference)\n\n" + "\n".join(rows)


def _render_intent_anchor(agent: str) -> str:
    """Anchor only active task briefs, not the boss's full original ask.

    The original intent remains durable in tasks.json for audit/live lookup,
    but injecting it into every worker wake makes large boss messages linger
    across unrelated work. Covers the agent's non-terminal (进行中 / 需审批)
    tasks. Empty string when there's no such task — no section, no noise.

    Each task also surfaces its `approval_note` when present: the pending
    question while suspended (需审批), the latest verdict after
    approve --note / reject feedback (进行中). Without the verdict, a worker
    resumed from an anchor holding only its own pending question can invent
    the answer the boss never gave — the anchor must carry what was DECIDED,
    not just what was asked.

    Must never raise: this feeds the spawn / native-memory path, and a
    throw here would break the agent's whole wake. Any store hiccup → "".
    """
    try:
        from claudeteam.store import tasks
        active = [t for t in tasks.list_tasks(assignee=agent)
                  if t.get("status") in ("进行中", "需审批")]
        task_lines: list[str] = []
        notes: list[str] = []
        for t in active:
            intent_id = t.get("intent_id") or "-"
            desc = (t.get("description") or "").strip()
            brief = desc if desc else (t.get("title") or "")
            if len(brief) > 700:
                brief = brief[:700].rstrip() + "..."
            task_lines.append(
                f"- **{t['id']}** [{t.get('status', '')}] intent {intent_id}: "
                f"{t.get('title', '')}" + (f"\n  brief: {brief}" if brief else "")
            )
            note = (t.get("approval_note") or "").strip()
            if note:
                label = ("Pending question" if t.get("status") == "需审批"
                         else "Latest verdict")
                notes.append(f"　↳ {t['id']} {label}：{note}")
    except Exception:
        return ""
    if not task_lines:
        return ""
    lines = [
        "## Active task anchor (brief only)",
        "",
        "Use these active task briefs as the working context. Do not re-read the boss's full original ask unless the manager explicitly asks or the brief is insufficient.",
        "",
    ]
    lines.extend(task_lines)
    lines.extend(notes)
    return "\n".join(lines)


def render(agent: str, *, role: str | None = None,
           cli: str | None = None, model: str | None = None,
           specialty: list[str] | None = None,
           tone: str | None = None,
           notes: str | None = None) -> str:
    """Return the identity markdown text for `agent`.

    Defaults missing fields from team.json so callers can call this with
    just the agent name in production, or override every field for tests.

    `specialty` / `tone` / `notes` are optional team.agents.<X> fields
    (Step 2 schema extension). Empty / absent → no section rendered;
    keeps existing one-role-line agents' identity files unchanged.
    """
    # Always read the agent's config so config-backed optional fields (specialty /
    # tone / notes / playbook) resolve in EVERY path — including the lifecycle
    # provision call that passes role/cli/model explicitly (which used to skip the
    # read and silently drop them). Tolerate a missing agent (tests render ad-hoc
    # names not in any team config) by degrading to an empty dict.
    try:
        cfg = config.agent_config(agent)
    except KeyError:
        cfg = {}
    role = role if role is not None else (cfg.get("role") or agent)
    cli = cli if cli is not None else (cfg.get("cli") or "claude-code")
    model = model if model is not None else (cfg.get("model") or "")
    # Let the adapter map the resolved model to its display label: CLIs
    # that ignore the team/argv model (gemini/qwen/kimi) or only honour it
    # conditionally (codex) must not render a model the agent isn't running.
    try:
        from claudeteam.agents import get_adapter
        model = get_adapter(cli).display_model(model)
    except KeyError:
        model = model or "default"
    specialty = specialty if specialty is not None else (cfg.get("specialty") or [])
    tone = tone if tone is not None else (cfg.get("tone") or "")
    notes = notes if notes is not None else (cfg.get("notes") or "")
    playbook = cfg.get("playbook") or ""
    body = _MANAGER_BODY if agent == "manager" else _WORKER_BODY
    rendered = body.format(name=agent, role=role, cli=cli, model=model,
                           workdir_rule=_WORKDIR_RULE,
                           team_principles=_TEAM_PRINCIPLES)
    # Append optional sections at the end of the identity body. Manager
    # also gets the team specialties block so it can pick the right worker.
    rendered += _render_specialty_section(specialty)
    rendered += _render_tone_section(tone)
    rendered += _render_notes_section(notes)
    rendered += _render_playbook_section(playbook)
    rendered += _render_workspace_section(agent)
    rendered += _render_skills_section()
    if agent == "manager":
        rendered += _render_team_specialties_block()
    return rendered


def init_prompt(agent: str) -> str:
    """On-spawn / on-clear / on-reidentify prompt: inject this into an
    agent's pane so it loads its identity, checks inbox, processes any
    unread messages, and reports for duty. Without this, a
    freshly-spawned claude-code sits at an empty prompt and never knows
    it's "manager" or "worker_cc".

    Appends the agent's recent durable memory (if any) so a pane that's
    been /clear-ed or restarted picks up where it left off instead of
    losing all task continuity. Empty memory → no extra section appears
    (avoid noise on a brand-new agent).

    The prompt explicitly tells the agent to PROCESS unread inbox
    messages that belong to the current task rather than just counting them.
    Workers deliberately avoid sweeping all unread history into one turn.
    """
    say_target_hint = (
        "--to user (to the boss)" if agent == "manager"
        else "--to user (completion/visible to the boss) or --to manager (internal progress)"
    )
    # Identity path threaded as absolute. The relative form `agents/<x>/identity.md`
    # only resolves from the agent pane's CWD — claude on host happens to
    # run from the project root where `state/agents/...` is a sibling, but
    # codex / kimi / docker spawns at `/app` (or wherever the spawn cmd
    # runs from) and the relative path doesn't resolve there — the codex
    # pane logs "agents/worker_codex/identity.md was missing" at boot.
    id_path = identity_path(agent)
    base = (
        f"You are {agent}. Read {id_path}, then run:\n"
        f"  claudeteam inbox {agent}\n"
        f"  claudeteam status {agent} 进行中 \"ready\"\n"
        f"\n"
        f"Process inbox with task isolation:\n"
        f"  1. Manager: handle boss/worker messages by deciding, queueing, dispatching, accepting, or summarizing.\n"
        f"  2. Worker: process only the current dispatch or supplements for the same T-n. If another unread message is for a different task,\n"
        f"     leave it unread and tell manager you are busy on the current T-n.\n"
        f"  3. For status / report-in / completion / progress updates, post with\n"
        f"     `claudeteam say {agent} \"<msg>\" --to user`\n"
        f"     (or --to manager for internal progress reports).\n"
        f"     ⚠️ every `say` MUST include `--to`: {say_target_hint}.\n"
        f"     Skipping --to silently falls back to user but defeats\n"
        f"     chat.publish filtering — don't be lazy.\n"
        f"  4. Mark a message read only after you have handled it: `claudeteam read <local_id>`.\n"
        f"\n"
        f"After processing, ack with one line: name, state, processed count."
    )
    if agent == "manager":
        # Hoist the manager red lines to the wake prompt so they're the
        # last thing the LLM reads before processing inbox. The full
        # rules also live at the top of identity.md but get buried under
        # 200+ lines by the time the LLM is mid-task. Without the hoist,
        # when manager's first inbox item is an exec task the natural
        # impulse is "let me handle it" rather than dispatching to a worker.
        base += (
            "\n\n"
            "⚠️ Manager red lines (strictly observe while processing inbox):\n"
            "  • Any execution >1 min (grep / reading files / running commands / writing scripts / testing / research)\n"
            "    create a task and use `claudeteam task dispatch-next <worker> --by manager`; don't do it yourself.\n"
            "  • Stay quiet after dispatch unless there is a completion, blocker, risk, or the boss asks for status.\n"
            "  • Collective orders (\"all hands/@team\") must send once to each non-manager agent,\n"
            "    never post the summary on the workers' behalf.\n"
            "  • When dispatching, give only the goal + acceptance + boundaries, don't predetermine the How.\n"
        )
    anchor = _render_intent_anchor(agent)
    recall = memory.render_for_prompt(agent)
    team = team_memory.render_for_prompt()
    tail = "\n\n".join(p for p in (anchor, recall, team) if p)
    if not tail:
        return base
    return f"{base}\n\n{tail}\n\nContinue your previously unfinished work; if it's already done, confirm and stand by."


def identity_path(agent: str) -> Path:
    """Where the rendered identity for `agent` lives on disk."""
    return paths.agent_dir(agent) / "identity.md"


def native_memory_text(agent: str, *, role: str | None = None,
                       cli: str | None = None, model: str | None = None,
                       specialty: list[str] | None = None,
                       tone: str | None = None,
                       notes: str | None = None) -> str:
    """Full text for a CLI-native always-loaded memory file (claude's
    ~/.claude/CLAUDE.md): identity body + standing remember policy +
    current durable-memory digest.

    A *projection* — source of truth is the team config (identity body)
    and `memory.jsonl` (digest). `write()` regenerates it on every
    (re)provision so the native file tracks the digest, and because
    claude loads it natively it survives /compact (which can bury the
    one-shot init prompt)."""
    body = render(agent, role=role, cli=cli, model=model,
                  specialty=specialty, tone=tone, notes=notes)
    parts = [body]
    anchor = _render_intent_anchor(agent)
    if anchor:
        parts.append(anchor)
    parts.append(_MEMORY_POLICY)
    recall = memory.render_for_prompt(agent)
    if recall:
        parts.append(recall)
    team = team_memory.render_for_prompt()
    if team:
        parts.append(team)
    return "\n\n".join(parts)


def write(agent: str, *, role: str | None = None,
          cli: str | None = None, model: str | None = None,
          specialty: list[str] | None = None,
          tone: str | None = None,
          notes: str | None = None) -> Path:
    """Render and persist the identity file; return its path.

    Also writes the CLI-native memory file (claude's ~/.claude/CLAUDE.md)
    when the agent's adapter declares one — so identity + remember policy
    + memory digest are loaded natively every session and survive
    /compact. No-op for CLIs without a native memory file."""
    target = identity_path(agent)
    atomic_write_text(target, render(agent, role=role, cli=cli, model=model,
                                      specialty=specialty, tone=tone, notes=notes))
    _write_native_memory(agent, role=role, cli=cli, model=model,
                         specialty=specialty, tone=tone, notes=notes)
    return target


def _write_native_memory(agent: str, *, role: str | None = None,
                         cli: str | None = None, model: str | None = None,
                         specialty: list[str] | None = None,
                         tone: str | None = None,
                         notes: str | None = None) -> None:
    """Best-effort write of the agent's CLI-native memory file. Resolves
    the agent's adapter; does nothing if it has no `native_memory_path`.

    Tolerates OSError — an unwritable agent HOME must not fail the whole
    provision (identity.md is already persisted and the init prompt still
    injects the memory digest) — but WARNS instead of swallowing: a failed
    write here means the anti-drift anchor silently stops tracking, which
    is exactly the failure the boss should hear about (disk full, perms).
    Lazy import of the adapter registry avoids any import cycle with
    `agents/__init__`."""
    resolved_cli = cli if cli is not None else (
        config.agent_config(agent).get("cli") or "claude-code")
    try:
        from claudeteam.agents import get_adapter
        path = get_adapter(resolved_cli).native_memory_path(agent)
    except KeyError:
        return
    if not path:
        return
    try:
        atomic_write_text(Path(path), native_memory_text(
            agent, role=role, cli=cli, model=model,
            specialty=specialty, tone=tone, notes=notes))
    except OSError as e:
        from claudeteam.util import warn
        warn(f"⚠️ {agent}: native memory write failed ({path}): {e} — "
             f"anti-drift anchor not persisted, identity.md still valid")


def refresh_native_memory(agent: str) -> bool:
    """Re-project the agent's CLI-native memory file (claude's
    ~/.claude/CLAUDE.md) so its always-loaded intent anchor tracks the
    agent's *current* active tasks.

    The native file is otherwise only (re)written at provision / reidentify,
    so an already-online, /compact-ed worker keeps a stale anchor that can
    point at an already-finished task — exactly the drift the anchor exists
    to prevent. Call this after any task transition that may change an
    assignee's active-intent set.

    Writes only when the projection actually changed (no needless disk
    churn) and never raises — a refresh failure must not break the task
    command that triggered it. Returns True iff the file was rewritten;
    False when there's no native file or it was already current.

    A swallowed failure here means a /compact-ed worker keeps acting on a
    stale anchor with zero traces — so unexpected exceptions WARN to stderr
    (the task command's caller sees it inline) instead of dying silent.
    The expected no-op cases (no native file for this CLI, unknown agent /
    adapter) stay quiet: they're configuration, not failure.

    Defaults (role / cli / model / …) come from team config, identical to
    how `write()` is invoked at provision, so the refreshed projection
    matches the provisioned one apart from the live anchor + digest.
    """
    try:
        resolved_cli = config.agent_config(agent).get("cli") or "claude-code"
        from claudeteam.agents import get_adapter
        path = get_adapter(resolved_cli).native_memory_path(agent)
    except KeyError:
        return False    # unknown agent / unregistered adapter — config, not failure
    if not path:
        return False
    try:
        new_text = native_memory_text(agent)
        target = Path(path)
        if target.exists() and \
                target.read_text(encoding="utf-8") == new_text:
            return False
        atomic_write_text(target, new_text)
        return True
    except Exception as e:
        from claudeteam.util import warn
        warn(f"⚠️ {agent}: anchor refresh failed ({path}): {e} — "
             f"this agent's anti-drift anchor may now be stale")
        return False
