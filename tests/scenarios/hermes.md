# Scenario: Hermes (Nous Research hermes-agent) as a team agent

Operator regression playbook for the `hermes` CLI adapter.

## Prerequisites

- `hermes` on PATH, installed via the **venv** path (NOT `--no-venv`):
  ```bash
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup
  ```
  (`--no-venv` produces a self-`exec`ing `/usr/local/bin/hermes` wrapper that
  hangs forever; `--skip-setup` avoids the interactive setup prompt.)
- The launching shell exports the key as `OPENAI_API_KEY` (a DeepSeek key).
- A `hermes` agent in `claudeteam.toml`:
  ```toml
  [team.agents.worker_hm]
  cli   = "hermes"
  model = "deepseek-v4-flash"
  role  = "Hermes 员工"
  ```

## Given / When / Then

**Given** the prerequisites and `claudeteam up`,
**When** `claudeteam health`, **Then** `worker_hm` shows `pane ready (hermes)` —
the adapter wrote a per-agent `~/.hermes/config.yaml` (`provider: custom`,
DeepSeek `/v1` base_url, the model) + `~/.hermes/.env` (`OPENAI_API_KEY`), and
launched `hermes`.

**Given** the pane is ready (banner `Welcome to Hermes Agent`, prompt `❯`),
**When** a boss message is injected,
**Then** Hermes submits on **Enter** and answers via DeepSeek (footer shows
`deepseek-v4-flash │ <ctx> │ …`). Verified live (it answered `4`).

**Given** `/clear worker_hm` / `/compact worker_hm`,
**Then** the adapter sends Hermes's own commands: **`/new`** (reset session) and
**`/compress`** (compact) — Hermes has no `/clear` or `/compact`.

## Notes

- The `custom` provider uses chat/completions, so DeepSeek works (unlike Trae's
  `openai` provider which hits the `/responses` 404).
- Per-agent isolated HOME, so each pane has its own `~/.hermes` (also avoids the
  one-time "legacy ~/.openclaw detected" migration tip).
- A harmless `tirith security scanner … not available` warning may print.
