"""opencode (sst/opencode) adapter.

opencode is a purpose-built coding TUI (binary `opencode`). It's provider-
agnostic via `@ai-sdk/openai-compatible`, so it runs on ANY OpenAI-compatible
endpoint (DeepSeek / OpenAI / a local server / …).

Config lives at `$HOME/.config/opencode/opencode.json`. We provision it per-agent
(isolated HOME) with `{env:OPENAI_BASE_URL}` / `{env:OPENAI_API_KEY}` substitution
so base_url + key come from the operator's env at runtime (the key resolved
through agent_auth — see `auth_slots`); only the model id is baked in per agent.
NOTE: the provider id must NOT be `openai` — that makes opencode use the
`/responses` API (which some OpenAI-compatible backends lack → "Z.responses"
error); we use the neutral id `compat`. Verified: plain Enter submits, prompt `Ask anything…`,
`/new` resets the session (= clear), `/compact` compacts, process `opencode`.
"""
from __future__ import annotations

import json
import shlex

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home


class OpencodeAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        m = (model or "").strip()
        home = agent_home(agent)
        cfg = {
            "$schema": "https://opencode.ai/config.json",
            # Auto-approve every tool action + out-of-workspace file access.
            # Without this, a team agent stalls on opencode's "Permission
            # required — Access external directory /data/state/agents/<a>"
            # gate the moment it reads its own identity.md / runs `claudeteam`
            # (its state dir is outside the /app worktree). Verified headless:
            # `permission: allow` lets it cat an external path with no prompt.
            "permission": "allow",
            "provider": {
                "compat": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "OpenAI-compatible",
                    # opencode substitutes {env:...} at runtime from the pane env.
                    "options": {"baseURL": "{env:OPENAI_BASE_URL}",
                                "apiKey": "{env:OPENAI_API_KEY}"},
                    "models": {m: {"name": m}},
                }
            },
            "model": f"compat/{m}",
        }
        cfg_json = json.dumps(cfg, separators=(",", ":"))
        qhome = shlex.quote(home)
        return (
            f"mkdir -p {qhome}/.config/opencode && "
            f"printf '%s' {shlex.quote(cfg_json)} > {qhome}/.config/opencode/opencode.json && "
            f"HOME={qhome} OPENCODE_AGENT={shlex.quote(agent)} opencode"
        )

    def ready_markers(self) -> list[str]:
        # Composer placeholder + the always-present footer hint.
        return ["ctrl+p commands", "Ask anything"]

    def process_name(self) -> str:
        return "opencode"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Verified live: plain Enter submits.
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        return (model or "").strip() or "OpenAI 兼容端点"

    def clear_command(self) -> str:
        # opencode resets context by starting a new session: /new.
        return "/new"
