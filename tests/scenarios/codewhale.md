# Scenario: CodeWhale (DeepSeek-TUI) as a team agent

Operator regression playbook for the `codewhale` (alias `code-whale`) adapter.
CodeWhale is a DeepSeek-native Rust coding TUI.

## Prerequisites

- `codewhale` on PATH (`npm i -g codewhale`) and the system lib **`libdbus-1-3`**
  installed (the prebuilt binary links it).
- The launching shell exports the DeepSeek key as `OPENAI_API_KEY`:
  ```bash
  export OPENAI_API_KEY="sk-..."   # a DeepSeek key (CodeWhale's base_url is api.deepseek.com)
  ```
- A `codewhale` agent in `claudeteam.toml`:
  ```toml
  [team.agents.worker_cw]
  cli   = "codewhale"
  model = "deepseek-v4-pro"   # or deepseek-v4-flash
  role  = "CodeWhale 员工"
  ```

## Given / When / Then

**Given** the prerequisites and `claudeteam up`,
**When** you run `claudeteam health`,
**Then** `worker_cw` shows `pane ready (codewhale)` — the adapter pre-provisions
`~/.codewhale/config.toml` (deepseek key + `[projects."<cwd>"].trust_level =
"trusted"`), so the 4-step onboarding wizard is skipped and the pane lands
straight in the chat composer.

**Given** the pane is ready,
**When** you `claudeteam peek worker_cw`,
**Then** the pane shows the `Composer` box with `Write a task or use /.` and the
footer `agent · deepseek-v4-pro · idle`.

**Given** `@worker_cw <task>`,
**When** the router injects it,
**Then** the composer submits on **Enter**, CodeWhale shows `… reasoning done`
then the answer and `✓ turn completed`.

## Notes

- CodeWhale is DeepSeek-only (its `base_url` defaults to `api.deepseek.com`); the
  team's `model` maps to `default_text_model`.
- It has no plain `/clear` or `/compact`; the adapter returns `None` for both
  (it uses session threads + `/restore`). Reset context by restarting the pane.
- Per-agent isolated HOME so each pane has its own `~/.codewhale` config + sessions.
