"""openclaw (openclaw/openclaw) adapter.

openclaw is a Node multi-channel agent gateway that also ships a real terminal
TUI. `openclaw chat` (= `tui --local`) runs the embedded agent runtime in-pane
with no separate gateway daemon — verified live against DeepSeek `/v1`: Enter
submits, model comes from config, process `openclaw-tui`.

Config is `$HOME/.openclaw/openclaw.json`. We provision it per-agent: an
OpenAI-compatible provider (`api: openai-completions`) whose `baseUrl` + `apiKey`
are `${OPENAI_BASE_URL}` / `${OPENAI_API_KEY}` (openclaw substitutes both from the
pane env at runtime — the key resolved through agent_auth, see `auth_slots`), and
the agent's model allowlisted under `agents.defaults.models` (openclaw rejects a
model that isn't allowlisted). Runs on any OpenAI-compatible endpoint (DeepSeek /
OpenAI / a local server / …).
"""
from __future__ import annotations

import json
import shlex

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home


class OpenclawAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        m = (model or "").strip()
        fq = f"compat/{m}"
        home = agent_home(agent)
        cfg = {
            "agents": {"defaults": {
                "model": {"primary": fq},
                "models": {fq: {"alias": "OpenAI-compatible"}},
            }},
            "models": {"providers": {"compat": {
                "baseUrl": "${OPENAI_BASE_URL}",
                "apiKey": "${OPENAI_API_KEY}",
                "api": "openai-completions",
                "models": [{
                    "id": m, "name": m, "reasoning": False, "input": ["text"],
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "contextWindow": 131072, "maxTokens": 8192,
                }],
            }}},
        }
        cfg_json = json.dumps(cfg, separators=(",", ":"))
        qhome = shlex.quote(home)
        return (
            f"mkdir -p {qhome}/.openclaw && "
            f"printf '%s' {shlex.quote(cfg_json)} > {qhome}/.openclaw/openclaw.json && "
            f"HOME={qhome} OPENCLAW_AGENT={shlex.quote(agent)} openclaw chat"
        )

    def ready_markers(self) -> list[str]:
        return ["openclaw tui - local embedded", "local ready"]

    def process_name(self) -> str:
        return "openclaw-tui"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Verified live: plain Enter submits.
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        return (model or "").strip() or "OpenAI 兼容端点"

    def clear_command(self) -> str | None:
        # openclaw resets via a new session, not a /clear slash; don't send one.
        return None

    def compact_command(self) -> str | None:
        return None
