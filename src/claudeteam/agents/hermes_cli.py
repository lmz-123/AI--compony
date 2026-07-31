"""Nous Research Hermes (NousResearch/hermes-agent) adapter.

Hermes's CLI is `hermes` (Python; install via its install.sh — use the venv
install, NOT `--no-venv`, which produces a self-`exec`ing wrapper that hangs).
Its interactive REPL is prompt_toolkit (not Textual), so tmux drives it fine —
plain Enter submits, prompt `❯`. Runs on any OpenAI-compatible endpoint
(DeepSeek / OpenAI / a local server / …) via `$OPENAI_BASE_URL`.

Config: `$HOME/.hermes/config.yaml` (non-secret) + `$HOME/.hermes/.env` (secret).
The `custom` provider uses chat/completions (no `/responses` 404). We provision
both per-agent from the operator's `$OPENAI_BASE_URL` + `$OPENAI_API_KEY` (the
key resolved through agent_auth — see `auth_slots`) + the agent's model.
Context: `/new` (reset) and `/compress` (compact) — NOT `/clear`/`/compact`.
"""
from __future__ import annotations

import shlex

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home

# Raw printf rendering config.yaml; %s slots = model, base_url.
_CFG_PRINTF = (
    r"""printf 'model:\n  provider: "custom"\n  default: "%s"\n"""
    r"""  base_url: "%s"\n' """
)


class HermesCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        m = (model or "").strip()
        home = agent_home(agent)
        qhome = shlex.quote(home)
        write_cfg = (
            f"mkdir -p {qhome}/.hermes && "
            + _CFG_PRINTF + shlex.quote(m) + ' "$OPENAI_BASE_URL"'
            + f" > {qhome}/.hermes/config.yaml && "
            + f'printf \'OPENAI_API_KEY=%s\\n\' "$OPENAI_API_KEY" > {qhome}/.hermes/.env'
        )
        return f"{write_cfg} && HOME={qhome} HERMES_AGENT={shlex.quote(agent)} hermes"

    def ready_markers(self) -> list[str]:
        return ["Welcome to Hermes Agent", "/help for commands"]

    def process_name(self) -> str:
        return "hermes"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Verified live: plain Enter submits (prompt_toolkit REPL).
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        return (model or "").strip() or "OpenAI 兼容端点"

    def clear_command(self) -> str | None:
        # Hermes resets the session with /new (NOT /clear).
        return "/new"

    def compact_command(self) -> str | None:
        # Hermes compacts context with /compress (NOT /compact).
        return "/compress"
