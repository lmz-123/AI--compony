"""Per-agent CLI authentication: resolve which credential each agent's CLI
uses, by a fixed priority — long-term **token** > interactive **login** >
**api_key**. "If a long-term token exists, the other two are ignored."

Why a priority resolver rather than a per-agent mode flag in the config: the
operator just drops whatever credential they have into the secrets file; the
highest-priority one present wins. Nothing about *which* mode is in
`claudeteam.toml`.

Secrets never live in `claudeteam.toml`. They're read from a gitignored env
file (`$CLAUDETEAM_SECRETS_FILE`, default `<state_dir>/.env`) and are
deliberately NOT loaded into the daemon's own environment — doing that would
leak every agent's key into every pane and defeat the priority (a CLI like
claude prefers `ANTHROPIC_API_KEY` over its OAuth token, so a stray exported
key would silently win). Instead the resolver reads the file directly, and
`spawn_env_prefix` sets ONLY the chosen credential's var while BLANKING the
lower-priority ones (`VAR=` in the shell prefix), so the chosen mode wins
regardless of the CLI's own env precedence or anything already in the
environment.

Per-agent override: a `<AGENT>_<VAR>` secret (e.g. `WORKER_CC_ANTHROPIC_API_KEY`)
beats the bare `<VAR>` — that's how different agents carry different accounts.

Trigger: `spawn_env_prefix` is prepended at every spawn site — provision_pane
(start / hire / restart / reaper-respawn), deliver lazy-wake, and send
lazy-wake — so it runs on every CLI launch, not just the first.

Per-CLI data (which env vars / creds file a CLI uses) lives on each adapter
via `CliAdapter.auth_slots()`; this module owns only the CLI-agnostic
resolution. An adapter with no slots (base default None — e.g. kimi, which
shares the operator's ~/.kimi with no per-agent isolation) resolves to mode
"none": no prefix, behavior unchanged.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from claudeteam.runtime import paths
from claudeteam.util import env_str


@dataclass(frozen=True)
class AuthResolution:
    mode: str                      # "token" | "login" | "api_key" | "none"
    set_env: dict[str, str]        # vars to set (the chosen credential)
    blank_env: tuple[str, ...]     # lower-priority vars to blank out


def _secrets_path() -> Path:
    override = env_str("CLAUDETEAM_SECRETS_FILE")
    return Path(override) if override else paths.state_dir() / ".env"


def load_secrets() -> dict[str, str]:
    """Parse the gitignored secrets env file into a dict. Missing file → {}.
    Format: `KEY=value` lines, `#` comments, optional `export `, optional
    surrounding quotes. NOT merged into os.environ (see module docstring)."""
    out: dict[str, str] = {}
    try:
        text = _secrets_path().read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def _agent_prefix(agent: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", agent.upper())


def _lookup(secrets: Mapping[str, str], environ: Mapping[str, str],
            agent: str, var: str) -> str:
    """Per-agent override (`<AGENT>_<VAR>`) then bare `<VAR>`, checking the
    secrets file first, then the live environment (operator-exported)."""
    pa = f"{_agent_prefix(agent)}_{var}"
    for source in (secrets, environ):
        for key in (pa, var):
            if source.get(key):
                return source[key]
    return ""


def _read_login_token(cred: Path) -> str | None:
    """Access token out of a claude `.credentials.json` (claudeAiOauth.accessToken).
    Best-effort: missing/malformed file → None."""
    try:
        data = json.loads(cred.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    tok = data.get("claudeAiOauth", {}).get("accessToken")
    return tok if isinstance(tok, str) and tok else None


def _agent_home(agent: str) -> Path:
    from claudeteam.runtime.paths import agent_home
    return Path(agent_home(agent))


def resolve(agent: str, adapter, *,
            secrets: Mapping[str, str] | None = None,
            environ: Mapping[str, str] | None = None,
            home: Path | None = None) -> AuthResolution:
    """Resolve `agent`'s credential by priority token > login > api_key."""
    slot = adapter.auth_slots()
    if slot is None:
        return AuthResolution("none", {}, ())
    if secrets is None:
        secrets = load_secrets()
    if environ is None:
        import os
        environ = os.environ
    home = home if home is not None else _agent_home(agent)

    # 1. long-term token — wins outright if present.
    if slot.token_env:
        tok = _lookup(secrets, environ, agent, slot.token_env)
        if tok:
            return AuthResolution("token", {slot.token_env: tok}, slot.api_key_envs)

    # 2. login — the CLI's own creds file is present in the isolated HOME.
    if slot.login_credfile and (home / slot.login_credfile).exists():
        set_env: dict[str, str] = {}
        if slot.login_token_env:
            tok = _read_login_token(home / slot.login_credfile)
            if tok:
                set_env[slot.login_token_env] = tok
        blank = tuple(e for e in (slot.token_env, *slot.api_key_envs)
                      if e and e not in set_env)
        return AuthResolution("login", set_env, blank)

    # 3. api key.
    for env_name in slot.api_key_envs:
        key = _lookup(secrets, environ, agent, env_name)
        if key:
            blank = (slot.token_env,) if slot.token_env else ()
            return AuthResolution("api_key", {env_name: key}, blank)

    return AuthResolution("none", {}, ())


def spawn_env_prefix(agent: str, adapter, *,
                     secrets: Mapping[str, str] | None = None,
                     environ: Mapping[str, str] | None = None,
                     home: Path | None = None) -> str:
    """Shell env prefix carrying `agent`'s resolved credential. Blanks the
    lower-priority vars first (so they can't override), then sets the chosen
    one. Empty string when nothing resolves (mode "none")."""
    res = resolve(agent, adapter, secrets=secrets, environ=environ, home=home)
    parts = [f"{k}=" for k in res.blank_env]
    parts += [f"{k}={shlex.quote(v)}" for k, v in res.set_env.items()]
    return " ".join(parts)
