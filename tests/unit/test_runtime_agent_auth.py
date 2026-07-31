"""Tests for runtime/agent_auth.py — per-agent credential resolution
(priority: long-term token > login > api_key)."""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from claudeteam.runtime import agent_auth
from claudeteam.agents.base import AuthSlots


class _Adapter:
    """Adapter stand-in: agent_auth reads only auth_slots()."""
    def __init__(self, slots): self._s = slots
    def auth_slots(self): return self._s


_CLAUDE = _Adapter(AuthSlots("CLAUDE_CODE_OAUTH_TOKEN", ("ANTHROPIC_API_KEY",),
                             ".claude/.credentials.json", "CLAUDE_CODE_OAUTH_TOKEN"))
_CODEX = _Adapter(AuthSlots("CODEX_ACCESS_TOKEN", ("OPENAI_API_KEY",),
                            ".codex/auth.json", None))
_GEMINI = _Adapter(AuthSlots(None, ("GEMINI_API_KEY",),
                             ".gemini/oauth_creds.json", None))
_KIMI = _Adapter(None)   # no slots → mode "none"


@contextmanager
def _home(creds_rel=None, creds_body="{}"):
    """A throwaway agent HOME; optionally seed a login creds file at `creds_rel`."""
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        if creds_rel:
            p = home / creds_rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(creds_body)
        yield home


_EMPTY = Path("/nonexistent-agent-home")   # no login creds file anywhere


# ── priority: token > login > api_key ────────────────────────────


def test_token_beats_login_and_api_key():
    with _home(".claude/.credentials.json", '{"claudeAiOauth":{"accessToken":"AT"}}') as home:
        res = agent_auth.resolve(
            "w", _CLAUDE,
            secrets={"CLAUDE_CODE_OAUTH_TOKEN": "tok", "ANTHROPIC_API_KEY": "key"},
            environ={}, home=home)
    assert res.mode == "token"
    assert res.set_env == {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}
    assert "ANTHROPIC_API_KEY" in res.blank_env       # lower priority → blanked


def test_login_beats_api_key_and_materialises_access_token():
    with _home(".claude/.credentials.json", '{"claudeAiOauth":{"accessToken":"AT"}}') as home:
        res = agent_auth.resolve(
            "w", _CLAUDE, secrets={"ANTHROPIC_API_KEY": "key"}, environ={}, home=home)
    assert res.mode == "login"
    # claude login: feed the file's accessToken back in as the OAuth token env
    assert res.set_env == {"CLAUDE_CODE_OAUTH_TOKEN": "AT"}
    assert "ANTHROPIC_API_KEY" in res.blank_env


def test_api_key_when_no_token_or_login():
    res = agent_auth.resolve(
        "w", _CLAUDE, secrets={"ANTHROPIC_API_KEY": "key"}, environ={}, home=_EMPTY)
    assert res.mode == "api_key"
    assert res.set_env == {"ANTHROPIC_API_KEY": "key"}
    assert "CLAUDE_CODE_OAUTH_TOKEN" in res.blank_env  # token slot blanked


def test_none_when_nothing_present():
    res = agent_auth.resolve("w", _CLAUDE, secrets={}, environ={}, home=_EMPTY)
    assert res.mode == "none"
    assert res.set_env == {} and res.blank_env == ()


# ── per-agent override ───────────────────────────────────────────


def test_per_agent_override_beats_global():
    res = agent_auth.resolve(
        "worker_cc", _CLAUDE,
        secrets={"ANTHROPIC_API_KEY": "global", "WORKER_CC_ANTHROPIC_API_KEY": "mine"},
        environ={}, home=_EMPTY)
    assert res.set_env == {"ANTHROPIC_API_KEY": "mine"}


def test_secrets_file_beats_environ_but_environ_is_a_fallback():
    # environ-only value is used when the secrets file lacks it
    res = agent_auth.resolve(
        "w", _CLAUDE, secrets={}, environ={"ANTHROPIC_API_KEY": "from-env"}, home=_EMPTY)
    assert res.set_env == {"ANTHROPIC_API_KEY": "from-env"}


# ── per-CLI specifics ────────────────────────────────────────────


def test_codex_login_blanks_token_and_key_sets_nothing():
    # codex reads its own auth.json (CODEX_HOME); login materialises no env,
    # but blanks the token + key so neither overrides the file.
    with _home(".codex/auth.json") as home:
        res = agent_auth.resolve("w", _CODEX, secrets={}, environ={}, home=home)
    assert res.mode == "login"
    assert res.set_env == {}
    assert set(res.blank_env) == {"CODEX_ACCESS_TOKEN", "OPENAI_API_KEY"}


def test_gemini_has_no_token_mode_falls_to_api_key():
    res = agent_auth.resolve(
        "w", _GEMINI, secrets={"GEMINI_API_KEY": "g"}, environ={}, home=_EMPTY)
    assert res.mode == "api_key"
    assert res.set_env == {"GEMINI_API_KEY": "g"}
    assert res.blank_env == ()                # gemini has no token slot to blank


def test_kimi_resolves_to_none_no_isolation():
    res = agent_auth.resolve(
        "w", _KIMI, secrets={"ANTHROPIC_API_KEY": "x"}, environ={}, home=_EMPTY)
    assert res.mode == "none"


# ── spawn_env_prefix (the shell prefix prepended at every spawn) ──


def test_spawn_env_prefix_blanks_then_sets_quoted():
    prefix = agent_auth.spawn_env_prefix(
        "w", _CLAUDE,
        secrets={"CLAUDE_CODE_OAUTH_TOKEN": "t o k"}, environ={}, home=_EMPTY)
    assert "ANTHROPIC_API_KEY=" in prefix                 # conflict blanked
    assert "CLAUDE_CODE_OAUTH_TOKEN='t o k'" in prefix     # chosen, shell-quoted


def test_spawn_env_prefix_empty_when_none():
    assert agent_auth.spawn_env_prefix(
        "w", _KIMI, secrets={}, environ={}, home=_EMPTY) == ""


# ── secrets file parsing ─────────────────────────────────────────


def test_load_secrets_parses_env_file():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "secrets.env"
        f.write_text(
            "# a comment\n"
            "ANTHROPIC_API_KEY=sk-xyz\n"
            'export OPENAI_API_KEY="quoted val"\n'
            "\n"
            "BAD LINE NO EQUALS\n")
        old = os.environ.get("CLAUDETEAM_SECRETS_FILE")
        os.environ["CLAUDETEAM_SECRETS_FILE"] = str(f)
        try:
            s = agent_auth.load_secrets()
        finally:
            if old is None:
                os.environ.pop("CLAUDETEAM_SECRETS_FILE", None)
            else:
                os.environ["CLAUDETEAM_SECRETS_FILE"] = old
    assert s["ANTHROPIC_API_KEY"] == "sk-xyz"
    assert s["OPENAI_API_KEY"] == "quoted val"     # export + quotes stripped
    assert "BAD" not in " ".join(s.keys())          # malformed line skipped


# ── real adapters declare their own slots (not a central table) ──


def test_real_adapters_declare_expected_slots():
    from claudeteam.agents.claude_code import ClaudeCodeAdapter
    from claudeteam.agents.codex_cli import CodexCliAdapter
    from claudeteam.agents.kimi_code import KimiCodeAdapter

    cc = ClaudeCodeAdapter().auth_slots()
    assert cc.token_env == "CLAUDE_CODE_OAUTH_TOKEN"
    assert cc.login_credfile == ".claude/.credentials.json"
    assert cc.login_token_env == "CLAUDE_CODE_OAUTH_TOKEN"  # keychain-avoidance

    cx = CodexCliAdapter().auth_slots()
    assert cx.token_env == "CODEX_ACCESS_TOKEN"
    assert cx.login_token_env is None                       # codex reads auth.json itself

    # kimi has no per-agent isolation → no slots → "unmanaged" (base default).
    assert KimiCodeAdapter().auth_slots() is None
