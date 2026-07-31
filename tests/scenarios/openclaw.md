# Scenario: openclaw as a team agent

Operator regression playbook for the `openclaw` CLI adapter. openclaw's
`chat` (= `tui --local`) runs an embedded agent in-pane (no gateway daemon),
on any OpenAI-compatible endpoint (pinned to DeepSeek `/v1` here).

## Prerequisites

- `openclaw` on PATH (`npm i -g openclaw@latest`) — **needs Node ≥ 22**.
- The launching shell exports the key as `OPENAI_API_KEY` (DeepSeek key).
- An `openclaw` agent in `claudeteam.toml`:
  ```toml
  [team.agents.worker_ow]
  cli   = "openclaw"
  model = "deepseek-v4-flash"
  role  = "openclaw 员工"
  ```

## Given / When / Then

**Given** the prerequisites and `claudeteam up`,
**When** `claudeteam health`,
**Then** `worker_ow` shows `pane ready (openclaw)` — the adapter wrote a per-agent
`~/.openclaw/openclaw.json` (deepseek provider, model allowlisted) and `openclaw
chat` lands in the local embedded TUI.

**Given** the pane is ready,
**When** `claudeteam peek worker_ow`,
**Then** the pane shows `openclaw tui - local embedded - agent main` and a footer
`local ready | idle … deepseek/deepseek-v4-flash`.

**Given** `@worker_ow <task>`,
**When** the router injects it,
**Then** the composer submits on **Enter** and openclaw answers (token counter
advances).

## Notes

- The model MUST be allowlisted under `agents.defaults.models` or openclaw
  rejects it — the adapter does this automatically.
- `apiKey` is `${OPENAI_API_KEY}` (substituted at runtime); baseUrl is pinned to
  DeepSeek. No `/clear` or `/compact` slash — reset by restarting the pane.
- Process name in the pane is `openclaw-tui`.
