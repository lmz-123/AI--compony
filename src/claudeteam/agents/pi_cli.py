"""Pi (earendil-works/pi, npm `@mariozechner/pi-coding-agent`) adapter.

Pi is a BYOK terminal coding agent (binary `pi`). Unlike the other workers it
needs NO config file: provider, model, and key are all flags. Pi has built-in
providers (`openai` / `anthropic` / `deepseek` / …); pick the one matching your
endpoint via `CLAUDETEAM_PI_PROVIDER` (default `openai`, which honours
`$OPENAI_BASE_URL`). Key from `$OPENAI_API_KEY` (resolved through agent_auth —
see `auth_slots`); model from team.json:

  pi --provider <provider> --model <model> --api-key "$OPENAI_API_KEY"

lands straight in the ready TUI (no onboarding / login screen) and answers;
plain Enter submits (the "modified Enter" tmux warning is only about multi-line
input, not submit).

First launch in a fresh HOME downloads `fd` + `ripgrep` into ~/.pi/agent/bin
(a one-time few-second step) — the ready footer appears once that's done, well
within the 60s ready window.
"""
from __future__ import annotations

import shlex

from claudeteam.util import env_str

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home


class PiCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        m = (model or "").strip()
        qhome = shlex.quote(agent_home(agent))
        # No config file — Pi's built-in providers + flags are enough. Pick the
        # provider per endpoint via env; key + base_url come from the operator's
        # OPENAI_* env. Per-agent HOME isolates ~/.pi sessions + downloaded fd/rg.
        provider = env_str("CLAUDETEAM_PI_PROVIDER") or "openai"
        return (
            f"HOME={qhome} PI_AGENT={shlex.quote(agent)} "
            f"pi --provider {shlex.quote(provider)} --model {shlex.quote(m)} "
            f'--api-key "$OPENAI_API_KEY"'
        )

    def ready_markers(self) -> list[str]:
        # The persistent footer help line, printed once the TUI is up.
        return ["escape interrupt", "ctrl+c/ctrl+d clear"]

    def process_name(self) -> str:
        return "pi"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Verified: plain Enter submits.
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        return (model or "").strip() or "OpenAI 兼容端点"

    def clear_command(self) -> str | None:
        # Pi clears the input/session with ctrl+c, not a slash command, so
        # there's no text command to inject.
        return None

    def compact_command(self) -> str | None:
        return None
