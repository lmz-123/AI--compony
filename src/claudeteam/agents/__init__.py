"""CLI adapter registry — maps a `cli` identifier to its CliAdapter."""
from __future__ import annotations

from claudeteam.runtime.config import agent_cli

from .base import CliAdapter
from .claude_code import ClaudeCodeAdapter
from .codewhale_cli import CodewhaleAdapter
from .codex_cli import CodexCliAdapter
from .gemini_cli import GeminiCliAdapter
from .hermes_cli import HermesCliAdapter
from .kimi_code import KimiCodeAdapter
from .minimax_agent import MiniMaxAgentAdapter
from .openclaw_cli import OpenclawAdapter
from .opencode_cli import OpencodeAdapter
from .pi_cli import PiCliAdapter
from .qwen_code import QwenCodeAdapter
from .trae_cli import TraeCliAdapter


_kimi = KimiCodeAdapter()
_qwen = QwenCodeAdapter()
_minimax = MiniMaxAgentAdapter()
_codewhale = CodewhaleAdapter()
_trae = TraeCliAdapter()
_pi = PiCliAdapter()
_REGISTRY: dict[str, CliAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex-cli": CodexCliAdapter(),
    "gemini-cli": GeminiCliAdapter(),
    "kimi-code": _kimi,
    "kimi-cli": _kimi,  # alias: upstream package name
    "qwen-code": _qwen,
    "qwen-cli": _qwen,  # alias for symmetry with kimi
    "minimax": _minimax,
    "mini-agent": _minimax,  # alias: upstream binary name
    "opencode": OpencodeAdapter(),
    "codewhale": _codewhale,
    "code-whale": _codewhale,  # alias (the spoken name)
    "openclaw": OpenclawAdapter(),
    "trae": _trae,
    "trae-cli": _trae,  # alias: upstream binary name
    "hermes": HermesCliAdapter(),
    "pi": _pi,
    "pi-cli": _pi,  # alias for symmetry
}


def known_clis() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def get_adapter(cli_name: str) -> CliAdapter:
    """Return the adapter for `cli_name`. Raises KeyError if not registered."""
    if cli_name not in _REGISTRY:
        raise KeyError(
            f"unknown cli: {cli_name!r} (known: {', '.join(_REGISTRY)})")
    return _REGISTRY[cli_name]


def adapter_for_agent(agent: str) -> CliAdapter:
    """Look up the agent's `cli` from team.json and return its adapter.

    Convenience over `get_adapter(config.agent_cli(agent))`; the routing
    layer reaches for this whenever it needs to spawn or inspect a pane.
    """
    return get_adapter(agent_cli(agent))
