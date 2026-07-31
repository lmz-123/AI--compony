"""Google Gemini CLI adapter.

Install: `npm install -g @google/gemini-cli`
Auth: OAuth (60/min + 1000/day free tier) / GEMINI_API_KEY / Vertex AI

Currently a thin shape match to the other adapters; spawn_cmd uses
`--approval-mode=yolo` (matches claude-code's `--dangerously-skip-permissions`
operationally — auto-approve every tool call so the agent runs unattended).

`model` is dropped because gemini-cli's model selection is via env or
config, not argv. Pass `GEMINI_MODEL` in the deployment environment if
the default doesn't fit.
"""
from __future__ import annotations

import shlex

from .base import AuthSlots, CliAdapter, MULTILINE_SUBMIT_KEYS
from claudeteam.runtime.paths import agent_home


class GeminiCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # gemini-cli has no --name flag; tag agent identity via env so
        # /proc and log-parsers can correlate output with which pane.
        # DISABLE_UPDATE_CHECK avoids a blocking prompt at startup.
        # HOME=<agent_home> isolates each pane's ~/.gemini (auth cache +
        # GEMINI.md memory) so siblings don't clobber one shared HOME.
        return (
            f"HOME={shlex.quote(agent_home(agent))} "
            f"DISABLE_UPDATE_CHECK=1 GEMINI_AGENT={shlex.quote(agent)} "
            f"gemini --approval-mode=yolo"
        )

    def display_model(self, model: str) -> str:
        # gemini-cli selects its model via env/config (GEMINI_MODEL), not
        # the team/argv model — so render the source, not a stale alias.
        return "gemini 自身配置 (env/config)"

    def native_memory_reloads(self) -> bool:
        # gemini feeds GEMINI.md into every prompt and reloads it on /memory
        # refresh, so an on-disk anchor rewrite reaches the running agent —
        # the only non-claude CLI of the four that doesn't need a re-inject.
        return True

    def native_memory_path(self, agent: str) -> str:
        # gemini loads ~/.gemini/GEMINI.md into every prompt and re-reads it
        # on /memory refresh, so the anchor survives the CLI's compaction —
        # the only one of the four non-claude CLIs that does.
        return f"{agent_home(agent)}/.gemini/GEMINI.md"

    def ready_markers(self) -> list[str]:
        # TUI ready prompt; community gemini-cli wraps Ink so the trailing
        # `>` after the banner is the canonical marker. Add the tagline so
        # the spawn-cmd echo doesn't accidentally match.
        return ["Gemini>", "Gemini CLI"]

    def process_name(self) -> str:
        return "gemini"

    def auth_slots(self) -> AuthSlots:
        # No long-term-token mode — api key, or its own oauth_creds (login).
        return AuthSlots(
            token_env=None,
            api_key_envs=("GEMINI_API_KEY",),
            login_credfile=".gemini/oauth_creds.json",
        )

    def submit_keys(self) -> list[str]:
        # Ink-based UI, same submit pattern as Codex / Kimi
        return list(MULTILINE_SUBMIT_KEYS)

    def compact_command(self) -> str:
        # gemini-cli compacts context with /compress (not /compact).
        return "/compress"
