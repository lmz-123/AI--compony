# Scenario: MiniMax Mini-Agent as a team agent

Operator regression playbook for the `minimax` (alias `mini-agent`) CLI adapter.
Mini-Agent is an OpenAI-compatible coding CLI, so it runs against **any**
OpenAI-compatible endpoint — verified against DeepSeek `/v1`.

## Prerequisites

- `mini-agent` on PATH (`uv tool install "git+https://github.com/MiniMax-AI/Mini-Agent.git"`).
- The launching shell exports an OpenAI-compatible endpoint + key, e.g. DeepSeek:
  ```bash
  export OPENAI_BASE_URL="https://api.deepseek.com/v1"
  export OPENAI_API_KEY="sk-..."        # a DeepSeek key
  ```
  (The adapter provisions `~/.mini-agent/config/config.yaml` from these at spawn;
  Mini-Agent has no endpoint env var of its own.)
- A `minimax` agent in `claudeteam.toml` with a real OpenAI-compatible model:
  ```toml
  [team.agents.worker_mm]
  cli   = "minimax"
  model = "deepseek-v4-flash"   # or deepseek-v4-pro
  role  = "MiniMax 员工"
  ```

## Given / When / Then

**Given** the prerequisites above and `claudeteam up`,
**When** you run `claudeteam health`,
**Then** `worker_mm` shows `pane ready (minimax)` and its heartbeat ticks.

**Given** the pane is ready,
**When** you `claudeteam peek worker_mm`,
**Then** the pane shows the banner `🤖 Mini Agent - Multi-turn Interactive Session`
and the `You ›` prompt (the adapter's ready markers).

**Given** a boss message `@worker_mm <task>` (or `claudeteam send`),
**When** the router injects it,
**Then** the pane prints `Agent › Thinking… (Esc to cancel)` then `🤖 Assistant:`
with the reply — i.e. plain **Enter** submitted the injected text (Ctrl+J = newline).

**Given** a `/clear worker_mm`,
**Then** Mini-Agent runs `/clear` (clears session history, keeps the system
prompt). NOTE: Mini-Agent has **no `/compact`** — `/compact worker_mm` is a no-op
for this CLI (the adapter returns `compact_command() == None`); use `/clear` to
reset context instead.

## Known limitations

- One shared `~/.mini-agent/config/config.yaml` per HOME — multiple `minimax`
  panes under the same HOME share it (fine when they use the same endpoint/model).
- Mini-Agent runs whatever model the endpoint serves; the team's `opus`/`sonnet`
  aliases do not apply — set `model` to a real endpoint model id.
