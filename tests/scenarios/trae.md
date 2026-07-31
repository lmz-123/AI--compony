# Scenario: Trae (ByteDance trae-agent) as a team agent

Operator regression playbook for the `trae` (alias `trae-cli`) adapter.

## Prerequisites

- `trae-cli` on PATH, installed WITH its deps:
  ```bash
  uv tool install --with docker --with pexpect "git+https://github.com/bytedance/trae-agent.git"
  ```
  (`docker` + `pexpect` are imported unconditionally; a plain install crashes.)
- The launching shell exports the key as `OPENAI_API_KEY` (a DeepSeek key).
- A `trae` agent in `claudeteam.toml`:
  ```toml
  [team.agents.worker_tr]
  cli   = "trae"
  model = "deepseek-v4-flash"
  role  = "Trae 员工"
  ```

## The two hard-won facts (why this adapter is shaped the way it is)

1. **Provider must be `openrouter`, not `openai`.** Trae's `openai` client calls
   the OpenAI *Responses* API (`/responses`), which DeepSeek does NOT serve →
   `404`. `openrouter` (and `doubao`) use `chat.completions` + a custom base_url
   → works. `ollama` hardcodes localhost. The adapter writes `provider: openrouter`.
2. **Console must be `simple`, not the default Textual `rich`.** The Textual TUI
   does not accept `tmux send-keys` (Enter never submits). `--console-type simple`
   is a plain prompt that submits on Enter.

## Given / When / Then

**Given** the prerequisites and `claudeteam up`,
**When** `claudeteam health`, **Then** `worker_tr` shows `pane ready (trae)` —
the adapter wrote a per-agent `~/.trae_config.json` (provider openrouter, DeepSeek
base_url, required ModelConfig fields, `enable_lakeview: false`) and launched
`trae-cli interactive --console-type simple --api-key "$OPENAI_API_KEY"`.

**Given** the pane is ready (prompt `Task:`),
**When** a boss message is injected,
**Then** Trae submits on Enter and runs the task on DeepSeek (`Step N: Completed`,
real token usage), proven live (it answered `4` and created files).

## KNOWN NUANCE (needs operator awareness / a follow-up)

The simple console prompts **`Working Directory:`** after the first task (accepts
empty = cwd). ClaudeTeam's injector sends one Enter (the task); the working-dir
prompt then needs one more Enter. Until that's auto-handled in the wake path, the
first task may need a nudge (`/send worker_tr ""` or a manual Enter). Subsequent
tasks loop on `Task:`. (Trae is the one CLI in this set with a multi-prompt
interactive flow — unlike claude/codex/the others.)
