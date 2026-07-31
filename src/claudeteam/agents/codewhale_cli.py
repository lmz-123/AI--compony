"""CodeWhale (Hmbown/CodeWhale) adapter.

CodeWhale is a Rust terminal coding TUI (codex heritage). Its `provider` is a
fixed enum (deepseek / openai / openrouter / vllm / …); pick the one matching
your endpoint via `CLAUDETEAM_CODEWHALE_PROVIDER` (default `openai`). base_url +
key come from `$OPENAI_BASE_URL` (the `--base-url` flag) / `$OPENAI_API_KEY` (via
agent_auth — see `auth_slots`). Plain Enter submits, process `codewhale`.

Its first run shows a 4-step onboarding wizard (welcome → API key → trust
directory → chat). We skip it by PROVISIONING `~/.codewhale/config.toml` at spawn
(provider + key + model + `[projects."<cwd>"].trust_level = "trusted"`) and
launching with `--skip-onboarding`.

System deps: the prebuilt binary needs `libdbus-1-3` present in the image.
"""
from __future__ import annotations

import shlex

from claudeteam.util import env_str

from .base import CliAdapter, OPENAI_COMPAT_AUTH
from claudeteam.runtime.paths import agent_home


# Single-line printf rendering config.toml. %s slots: provider, model, provider,
# api_key, cwd. Raw string keeps the `\n`s literal for printf to expand.
# - allow_shell = true: the agent shells out to `claudeteam say/read/...` to
#   respond (it's locked by default). Fully-unattended YOLO is set at launch via
#   `--yolo` (see spawn_cmd).
# - [shell_environment_policy] inherit = "all": CodeWhale's shell tool runs in a
#   SANITIZED env by default (codex heritage) that strips CLAUDETEAM_STATE_DIR —
#   so `claudeteam inbox/read/say` fall back to the default state dir, not the deployment's
#   /data/state, the inbox always reads empty, and every read is "no such
#   message". inherit="all" passes the pane env through. Verified: with it, a
#   codewhale shell-out sees CLAUDETEAM_STATE_DIR=/data/state.
_CFG_PRINTF = (
    r"""printf 'provider = "%s"\ndefault_text_model = "%s"\nallow_shell = true\n"""
    r"""[shell_environment_policy]\ninherit = "all"\n"""
    r"""[auth]\nmode = "api_key"\n[providers.%s]\napi_key = "%s"\n"""
    r"""[projects."%s"]\ntrust_level = "trusted"\n' """
)


class CodewhaleAdapter(CliAdapter):
    def spawn_cmd(self, agent: str, model: str) -> str:
        m = (model or "").strip()
        # provider is one of CodeWhale's fixed enum, chosen per endpoint via env.
        provider = env_str("CLAUDETEAM_CODEWHALE_PROVIDER") or "openai"
        home = agent_home(agent)
        qhome = shlex.quote(home)
        qp = shlex.quote(provider)
        write_cfg = (
            f"mkdir -p {qhome}/.codewhale && "
            + _CFG_PRINTF
            + f"{qp} {shlex.quote(m)} {qp}"
            + ' "$OPENAI_API_KEY" "$PWD"'
            + f" > {qhome}/.codewhale/config.toml"
        )
        # --yolo auto-approves every tool — the agent shells out to
        # `claudeteam say/read/...` on every turn. (The codex-style
        # `--approval-policy never` instead DENIES tools here: "code_execution
        # denied by user".) --skip-onboarding bypasses the welcome wizard.
        # --base-url points the chosen provider at $OPENAI_BASE_URL.
        return (
            f"{write_cfg} && HOME={qhome} CODEWHALE_AGENT={shlex.quote(agent)} "
            f'codewhale --yolo --skip-onboarding --base-url "$OPENAI_BASE_URL"'
        )

    def ready_markers(self) -> list[str]:
        # The chat composer placeholder + box label (post-onboarding).
        return ["Write a task or use", "Composer"]

    def process_name(self) -> str:
        return "codewhale"

    def auth_slots(self):
        return OPENAI_COMPAT_AUTH  # api_key via OPENAI_API_KEY (agent_auth tier 3)

    def submit_keys(self) -> list[str]:
        # Verified live: plain Enter submits.
        return ["Enter", "C-m"]

    def display_model(self, model: str) -> str:
        return (model or "").strip() or "OpenAI 兼容端点"

    def clear_command(self) -> str | None:
        # CodeWhale uses session threads (/restore to roll back); it has no
        # plain /clear or /compact, so don't send a command that would error.
        return None

    def compact_command(self) -> str | None:
        return None
