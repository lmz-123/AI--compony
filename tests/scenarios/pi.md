# Scenario: Pi (earendil-works/pi) as a team agent

Operator regression playbook for the `pi` CLI adapter.

## Prerequisites

- `pi` on PATH (`npm i -g @mariozechner/pi-coding-agent`; the binary is `pi`).
- The launching shell exports the DeepSeek key as `DEEPSEEK_API_KEY` — Pi's
  built-in `deepseek` provider already knows `deepseek-v4-flash` / `-pro` and
  points at api.deepseek.com (no base-url wiring needed).
- A `pi` agent in `claudeteam.toml`:
  ```toml
  [team.agents.worker_pi]
  cli   = "pi"
  model = "deepseek-v4-flash"   # or deepseek-v4-pro
  role  = "Pi 员工"
  ```

## Given / When / Then

**Given** the prerequisites and `claudeteam up`,
**When** `claudeteam health`, **Then** `worker_pi` shows `pane ready (pi)` — the
adapter launches `pi --provider deepseek --model <model> --api-key
"$DEEPSEEK_API_KEY"` with a per-agent HOME. **No config file is written**
(provider / model / key are all flags) and there is no onboarding / login
screen — it lands straight in the ready TUI.

**Given** the pane is ready (footer `escape interrupt · ctrl+c/ctrl+d clear/exit
· / commands · ! bash`, model footer `(deepseek) deepseek-v4-flash`),
**When** a boss message is injected,
**Then** Pi submits on **Enter** and answers via DeepSeek. Verified live (it
answered `42` to a test question, then processed the identity init).

## Notes

- First launch in a fresh HOME downloads `fd` + `ripgrep` into `~/.pi/agent/bin`
  (one-time, a few seconds) — the ready footer appears after, well within the
  60s ready window. Install `ripgrep`/`fd` on PATH to skip the download.
- Pi has no `/clear` or `/compact` slash command (ctrl+c clears the input), so
  the adapter reports neither — `/clear` / `/compact` just re-init instead.
- Interrupt is **Esc** (uniform with the other CLIs; Pi's footer literally says
  `escape interrupt`).
- A harmless "Update Available" notice may print at the top of the pane; it does
  not block launch.
