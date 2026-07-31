# Scenario: opencode as a team agent

Operator regression playbook for the `opencode` CLI adapter. opencode is an
OpenAI-compatible coding TUI — runs on any OpenAI-compatible endpoint (verified
against DeepSeek `/v1`).

## Prerequisites

- `opencode` on PATH (`npm i -g opencode-ai` or `curl -fsSL https://opencode.ai/install | bash`).
- The launching shell exports an OpenAI-compatible endpoint + key:
  ```bash
  export OPENAI_BASE_URL="https://api.deepseek.com/v1"
  export OPENAI_API_KEY="sk-..."        # a DeepSeek key
  ```
  (The adapter writes a per-agent `opencode.json` whose `baseURL`/`apiKey` are
  `{env:OPENAI_BASE_URL}` / `{env:OPENAI_API_KEY}`; opencode substitutes them at
  runtime, so nothing secret is written to disk.)
- An `opencode` agent in `claudeteam.toml`:
  ```toml
  [team.agents.worker_oc]
  cli   = "opencode"
  model = "deepseek-v4-flash"
  role  = "opencode 员工"
  ```

## Given / When / Then

**Given** the prerequisites and `claudeteam up`,
**When** you run `claudeteam health`,
**Then** `worker_oc` shows `pane ready (opencode)` and its heartbeat ticks.

**Given** the pane is ready,
**When** you `claudeteam peek worker_oc`,
**Then** the pane shows the opencode banner with `Ask anything…` and the footer
`ctrl+p commands`, and the model line reads `Build · DeepSeek V4 Flash`.

**Given** a boss message `@worker_oc <task>`,
**When** the router injects it,
**Then** the composer submits on **Enter** and opencode streams the reply
(`+ Thought:` then the answer · DeepSeek V4 Flash).

**Given** `/clear worker_oc`,
**Then** the adapter sends `/new` (opencode starts a fresh session = context reset).
`/compact worker_oc` sends `/compact` (opencode's `session_compact`).

## Notes

- The provider in `opencode.json` is named `deepseek`, NOT `openai`: an `openai`
  provider makes opencode use the `/responses` API which DeepSeek does not serve.
- Per-agent isolated HOME (`agent_home`) so each `opencode` pane has its own
  `~/.config/opencode/opencode.json` (own model) and session state.
