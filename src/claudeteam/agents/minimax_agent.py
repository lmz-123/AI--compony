"""MiniMax Mini-Agent adapter.

Mini-Agent (github.com/MiniMax-AI/Mini-Agent) is MiniMax's native interactive
coding CLI (Python; binary `mini-agent`). Unlike the Anthropic-protocol CLIs it
is a plain OpenAI-compatible client, so it points at ANY OpenAI-compatible
endpoint (DeepSeek / OpenAI / a local server / …) set via `$OPENAI_BASE_URL`.

It has NO env override for its endpoint: it only reads
`~/.mini-agent/config/config.yaml`. So `spawn_cmd` provisions that file at launch
from the operator-supplied `OPENAI_BASE_URL` + `OPENAI_API_KEY` (the latter
resolved through agent_auth — see `auth_slots`) and the agent's `model`
(`provider: openai` = the arbitrary-base_url path). Interactive REPL,
observed live: plain Enter submits (Ctrl+J = newline), `/clear` resets the
session, there is NO `/compact`, prompt is `You ›`, process name `mini-agent`.
"""
from __future__ import annotations

import shlex

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home


# printf format that renders config.yaml; %s slots get key / base_url / model.
# Raw string so the `\n`s stay literal for printf to expand into newlines.
_CFG_PRINTF = r"""printf 'api_key: "%s"\napi_base: "%s"\nmodel: "%s"\nprovider: "openai"\n' """


class MiniMaxAgentAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        # Provision <agent_home>/.mini-agent/config/config.yaml from the
        # operator's OPENAI_BASE_URL/OPENAI_API_KEY + this agent's model, then
        # launch with HOME pinned there. (Mini-Agent reads only that file — no
        # endpoint env var exists.) HOME is per-agent so two minimax agents — or
        # two teams — don't clobber one shared ~/.mini-agent; every other
        # OpenAI-compat adapter already isolates HOME this way.
        m = (model or "").strip()
        qhome = shlex.quote(agent_home(agent))
        write_cfg = (
            f"mkdir -p {qhome}/.mini-agent/config && "
            + _CFG_PRINTF
            + '"$OPENAI_API_KEY" "$OPENAI_BASE_URL" '
            + shlex.quote(m)
            + f" > {qhome}/.mini-agent/config/config.yaml"
        )
        return (f"{write_cfg} && HOME={qhome} "
                f"MINI_AGENT_AGENT={shlex.quote(agent)} mini-agent")

    def ready_markers(self) -> list[str]:
        # Interactive REPL banner, printed after tools + config load. ASCII-only
        # picks (avoid the unicode `You ›` prompt glyph).
        return ["Mini Agent - Multi-turn Interactive Session", "Type /help for help"]

    def process_name(self) -> str:
        return "mini-agent"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Verified live: plain Enter submits; Ctrl+J inserts a newline. So lead
        # with Enter (C-m == Enter as the only safe escalation); never C-j.
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        # Runs whatever OpenAI-compatible model the operator points it at — the
        # team's claude-style alias (opus/sonnet) doesn't apply here.
        return (model or "").strip() or "OpenAI 兼容端点"

    def compact_command(self) -> str | None:
        # Mini-Agent has `/clear` (→ base default) but NO `/compact`.
        return None
