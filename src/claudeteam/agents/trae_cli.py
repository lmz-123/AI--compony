"""ByteDance Trae (bytedance/trae-agent) adapter.

trae-agent's CLI is `trae-cli` (Python/uv). Two hard-won facts, both verified
live against DeepSeek:

1. The `provider` selects trae's API CLIENT, not a service. For a custom
   OpenAI-compatible endpoint use a provider whose client extends
   `openai_compatible_base` (`chat.completions.create` + custom `base_url`):
   `openrouter`/`doubao` — NOT `openai`, whose client calls the *Responses* API
   (`client.responses.create`) that many backends lack -> 404. (`ollama`
   hardcodes localhost.) Default `openrouter`, override via
   `CLAUDETEAM_TRAE_PROVIDER`. base_url + key come from `$OPENAI_BASE_URL` /
   `$OPENAI_API_KEY` (the key via agent_auth — see `auth_slots`).
2. Run a FRESH `trae-cli run` per message (via a tiny shell loop), NOT
   `trae-cli interactive`. The interactive REPL carries conversation history
   across tasks; after a tool-using task the saved history ends on a dangling
   `task_done` tool_call with no matching tool response, and DeepSeek rejects
   that on the NEXT message with HTTP 400 ("assistant message with 'tool_calls'
   must be followed by tool messages"). One-shot `trae-cli run` starts clean
   each time -> no 400. Use `--console-type simple` (the Textual `rich` TUI
   doesn't accept tmux send-keys). Readiness is a marker the loop echoes
   (`TRAE_AGENT_READY`), since there's no persistent prompt.

Trae needs a config FILE (no full env override). We provision a per-agent
config with the required ModelConfig fields and `enable_lakeview: false`; the
API key is passed via `--api-key` (from `$OPENAI_API_KEY`) so no secret is
written to disk. The config file MUST end in `.yaml`, NOT `.json`:
trae-agent's `Config.create` routes any `*.json` path straight to its legacy
parser (`create_from_legacy_config`), which crashes on our schema with
`KeyError: 'anthropic'`. The contents stay JSON (a valid YAML subset) — only
the extension decides which parser runs.
"""
from __future__ import annotations

import json
import shlex

from claudeteam.util import env_str

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home


class TraeCliAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        m = (model or "").strip()
        home = agent_home(agent)
        # trae's `provider` selects the API CLIENT, not a service. A custom
        # OpenAI-compatible endpoint needs a chat/completions client honouring a
        # base_url — that's trae's `openrouter`/`doubao` (NOT `openai`, which
        # calls the /responses API). Override per deployment via
        # CLAUDETEAM_TRAE_PROVIDER. base_url + api_key come from env via flags.
        provider = env_str("CLAUDETEAM_TRAE_PROVIDER") or "openrouter"
        cfg = {
            "agents": {"trae_agent": {
                "model": "trae_agent_model",
                "max_steps": 30,
                "enable_lakeview": False,
                "tools": ["bash", "str_replace_based_edit_tool",
                          "sequentialthinking", "task_done"],
            }},
            "models": {"trae_agent_model": {
                "model_provider": "ds", "model": m, "max_tokens": 8192,
                "temperature": 0.5, "top_p": 1.0, "top_k": 0,
                "parallel_tool_calls": True, "max_retries": 2,
            }},
            # base_url + api_key are supplied at launch via flags (env-driven),
            # so the config carries only placeholders.
            "model_providers": {"ds": {
                "provider": provider, "base_url": "set-via-flag",
                "api_key": "set-via-flag",
            }},
        }
        cfg_json = json.dumps(cfg, separators=(",", ":"))
        qhome = shlex.quote(home)
        # Per-task loop, NOT `trae-cli interactive`. trae's interactive REPL
        # carries conversation history across tasks, and after a tool-using task
        # the saved history ends on a dangling `task_done` tool_call with no
        # tool response — which DeepSeek rejects on the NEXT message with HTTP
        # 400 ("assistant message with 'tool_calls' must be followed by tool
        # messages"). So each injected message runs a FRESH `trae-cli run`
        # (clean history → no 400; verified). The inner read-with-timeout drains
        # a multi-line paste into one task; the loop re-echoes the ready marker
        # after each task so wake/pane_probe see it idle-ready again.
        # `.yaml` (not .json): trae routes *.json to its legacy parser (crashes
        # on our schema with KeyError 'anthropic').
        loop = (
            "echo TRAE_AGENT_READY\n"
            "while IFS= read -r line; do\n"
            '  task="$line"\n'
            '  while IFS= read -r -t 0.4 more; do task="$task $more"; done\n'
            '  [ -n "$task" ] && trae-cli run "$task" --console-type simple '
            '--config-file "$HOME/.trae_config.yaml" '
            '--model-base-url "$OPENAI_BASE_URL" --api-key "$OPENAI_API_KEY"\n'
            "  echo TRAE_AGENT_READY\n"
            "done"
        )
        return (
            f"mkdir -p {qhome} && printf '%s' {shlex.quote(cfg_json)} > "
            f"{qhome}/.trae_config.yaml && "
            f"HOME={qhome} TRAE_AGENT={shlex.quote(agent)} bash -c {shlex.quote(loop)}"
        )

    def ready_markers(self) -> list[str]:
        # The per-task loop echoes this before/after each `trae-cli run`.
        return ["TRAE_AGENT_READY"]

    def process_name(self) -> str:
        # `which trae-cli` (health). Pane idles at bash between tasks and runs
        # trae-cli during one; readiness is marker-based (TRAE_AGENT_READY).
        return "trae-cli"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Enter submits the line to the loop's `read`.
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        return (model or "").strip() or "OpenAI 兼容端点"

    def clear_command(self) -> str | None:
        return None

    def compact_command(self) -> str | None:
        return None
