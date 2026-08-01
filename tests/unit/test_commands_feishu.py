"""Tests for `claudeteam feishu connect`.

Three modes:
  • default = browser-automated self-built app (the Playwright bot-creator); on
    any automation failure it falls back to the guided flow.
  • --manual = guided self-built app (prompts for App ID/Secret, hands a permission
    deep-link, verifies im:message.group_msg landed, creates the group).
  • --quick = one-scan PersonalAgent device flow (warns if group_msg can't be had).

The node sidecar + the Playwright bot-creator (network + interactive browser) are
the things we can't run in a unit test, so we stub `feishu._run_sidecar` /
`feishu._run_bot_creator` with recorders that return canned results, and inject
`prompt=` for the guided flow's console inputs.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.commands import feishu
from claudeteam.feishu import lark
from claudeteam.runtime import config, tunables


class _SidecarStub:
    """Stand-in for `feishu._run_sidecar`. Records modes called + returns the
    JSON each sidecar mode emits. `granted` drives the scopes check."""

    def __init__(self, *, granted, register=...):
        self.calls: list[str] = []
        self.granted = list(granted)
        self._register = (
            {"event": "registered", "client_id": "cli_new", "client_secret": "sek",
             "owner_open_id": "ou_me", "tenant": "feishu"}
            if register is ... else register)

    def __call__(self, mode, *, extra_env=None):
        self.calls.append(mode)
        return {
            "register": self._register,
            "scopes": {"event": "scopes", "granted": self.granted},
            "create-group": {"event": "group_created", "chat_id": "oc_new",
                             "invited": "ou_me"},
        }.get(mode)


def _seed_toml() -> None:
    Path(os.environ["CLAUDETEAM_CONFIG_FILE"]).write_text(
        'chat_id = ""\n[team]\nsession = "S"\n', encoding="utf-8")
    tunables.reset_cache()


def _stub_sidecar(stub, tmp):
    sidecar = tmp / "sidecar.js"
    sidecar.write_text("// stub", encoding="utf-8")
    return attr_patch(feishu, _run_sidecar=stub, _sidecar_path=lambda: sidecar,
                      _ensure_node_deps=lambda d: True)


def _run_guided(stub, tmp, inputs):
    """Drive the guided flow with scripted console inputs; suppress its prints."""
    it = iter(inputs)
    with _stub_sidecar(stub, tmp), contextlib.redirect_stdout(io.StringIO()):
        return feishu._connect_guided([], prompt=lambda *a: next(it))


# ── guided (default): self-built app + group_msg verify ───────────────────────


def test_guided_persists_creds_and_chat_id():
    stub = _SidecarStub(granted={"im:message:send_as_bot", "im:message.group_msg"})
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        rc = _run_guided(stub, tmp, ["cli_self", "selfsecret", ""])
        assert rc == 0
        creds = lark.load_app_creds()
        assert creds["app_id"] == "cli_self" and creds["app_secret"] == "selfsecret"
        assert config.chat_id() == "oc_new"
        # guided does NOT register (no device flow); it verifies scopes + groups
        assert stub.calls == ["scopes", "create-group"]
        # creds file is 0600
        import stat
        assert stat.S_IMODE(os.stat(lark.app_creds_file()).st_mode) == 0o600


def test_guided_aborts_when_group_msg_missing():
    """The sensitive scope didn't land (deep-link not confirmed / not published) →
    abort before creating the group, with a clear message."""
    stub = _SidecarStub(granted={"im:message:send_as_bot"})  # no group_msg
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        rc = _run_guided(stub, tmp, ["cli_self", "sek", ""])
        assert rc != 0
        assert "scopes" in stub.calls and "create-group" not in stub.calls
        assert config.chat_id() == ""


def test_guided_aborts_on_empty_app_id():
    stub = _SidecarStub(granted={"im:message.group_msg"})
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        rc = _run_guided(stub, tmp, ["", ""])  # empty App ID
        assert rc != 0
        assert lark.load_app_creds() == {}
        assert stub.calls == []  # never touched the sidecar


def test_guided_warns_but_proceeds_when_scopes_unreadable():
    """A brand-new app can't self-read its scopes yet — that needs
    `application:application:self_manage` + publish/approval, so the read comes
    back empty. Treat empty as 'unknown', NOT 'missing': warn and still create the
    group, instead of dead-ending a correctly-set-up app. (Regression guard for the
    scope-verify dead-end where an empty read was misread as 'scopes missing'.)"""
    stub = _SidecarStub(granted=[])  # empty granted → _granted_scopes returns None
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        rc = _run_guided(stub, tmp, ["cli_self", "sek", ""])
        assert rc == 0
        assert config.chat_id() == "oc_new"   # group created despite unreadable scopes
        assert stub.calls == ["scopes", "create-group"]


def test_connect_rejects_unknown_flag():
    """Both guided + quick gate on reject_extra_args (canonical command shape)."""
    stub = _SidecarStub(granted={"im:message.group_msg"})
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        with _stub_sidecar(stub, tmp):
            rc, _, err = run_cli(["feishu", "connect", "--quick", "--bogus"])
        assert rc != 0
        assert stub.calls == []  # rejected before any sidecar call


def test_connect_aborts_cleanly_on_eof_non_tty():
    # REGRESSION (dogfood #4): a piped / non-TTY invocation makes the guided
    # flow's input() raise EOFError. connect must abort rc=1 with a friendly
    # message, not dump an EOFError traceback. (Default now routes through
    # _connect_auto, which falls back to guided when the browser creator is
    # unavailable — so force that fallback to reach the input() path.)
    def boom(*_a, **_k):
        raise EOFError
    with attr_patch(feishu, _run_bot_creator=lambda *a, **k: None,
                    _connect_guided=boom):
        rc, _, err = run_cli(["feishu", "connect"])
    assert rc == 1
    assert "已取消" in err or "非交互" in err


# ── --quick: PersonalAgent device flow ────────────────────────────────────────


def test_quick_persists_creds_and_chat_id():
    stub = _SidecarStub(granted={"im:message:send_as_bot", "im:message.group_msg"})
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        with _stub_sidecar(stub, tmp):
            rc, out, err = run_cli(["feishu", "connect", "--quick"])
        assert rc == 0, err
        assert lark.load_app_creds()["app_id"] == "cli_new"
        assert config.chat_id() == "oc_new"
        assert stub.calls == ["register", "scopes", "create-group"]


def test_quick_warns_when_group_msg_missing_but_still_groups():
    """--quick is lenient: a PersonalAgent can't get group_msg, so it warns the
    boss they'll need to @bot in groups, but still finishes the setup."""
    stub = _SidecarStub(granted={"im:message:send_as_bot"})  # no group_msg
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        with _stub_sidecar(stub, tmp):
            rc, out, _ = run_cli(["feishu", "connect", "--quick"])
        assert rc == 0
        assert "@bot" in out
        assert config.chat_id() == "oc_new"
        assert stub.calls == ["register", "scopes", "create-group"]


def test_quick_aborts_when_register_fails():
    stub = _SidecarStub(granted=set(), register=None)
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        with _stub_sidecar(stub, tmp):
            rc, _, _ = run_cli(["feishu", "connect", "--quick"])
        assert rc != 0
        assert lark.load_app_creds() == {}
        assert config.chat_id() == ""
        assert stub.calls == ["register"]


# ── dispatch ─────────────────────────────────────────────────────────────────


def test_feishu_no_subcommand_prints_usage():
    rc, out, _ = run_cli(["feishu"])
    assert rc == 0
    assert "feishu connect" in out


def test_feishu_unknown_subcommand_errors():
    rc, _, err = run_cli(["feishu", "bogus"])
    assert rc != 0
    assert "unknown feishu subcommand" in err


def test_connect_permission_link_includes_file_resource_scope():
    assert "im:resource" in feishu._DEEPLINK_SCOPES


# ── default: browser-automated self-built app + graceful fallback ─────────────


def test_default_auto_creates_app_then_group():
    """Default (no flag) drives the browser bot-creator → published self-built
    app, then creates the group. No device-flow register, no scope re-verify —
    the creator already published with group_msg, so it's just create-group."""
    stub = _SidecarStub(granted={"im:message.group_msg"})
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}) as tmp:
        _seed_toml()
        with _stub_sidecar(stub, tmp), attr_patch(
                feishu, _run_bot_creator=lambda name, tenant: ("cli_auto", "autosecret")):
            rc, out, err = run_cli(["feishu", "connect"])
        assert rc == 0, err
        assert lark.load_app_creds()["app_id"] == "cli_auto"
        assert config.chat_id() == "oc_new"
        assert stub.calls == ["create-group"]   # auto: no register / scopes


def test_default_auto_falls_back_to_manual_when_creator_fails():
    """The whole point of the fallback: browser automation failing (Feishu UI
    drift → creator returns None) DEGRADES to the manual guided flow, never a
    hard stop."""
    calls = []
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}):
        _seed_toml()
        with attr_patch(feishu,
                        _run_bot_creator=lambda *a, **k: None,
                        _connect_guided=lambda argv, **k: calls.append("guided") or 0):
            rc, out, _ = run_cli(["feishu", "connect"])
        assert rc == 0
        assert calls == ["guided"]   # fell back to manual
        assert "回退" in out          # and told the operator so


def test_manual_flag_forces_guided_skips_creator():
    """--manual skips browser automation entirely → straight to the guided
    console steps; the creator is never invoked."""
    calls = []
    with isolated_env(team={"agents": {"manager": {"cli": "claude-code"}}}):
        _seed_toml()
        with attr_patch(feishu,
                        _run_bot_creator=lambda *a, **k: calls.append("creator") or ("x", "y"),
                        _connect_guided=lambda argv, **k: calls.append("guided") or 0):
            rc, _, _ = run_cli(["feishu", "connect", "--manual"])
        assert rc == 0
        assert calls == ["guided"]   # creator never ran


def test_run_bot_creator_returns_none_when_creator_absent():
    """Missing creator (or its node deps) → None, which is what makes
    _connect_auto fall back instead of crashing."""
    with isolated_env(), env_patch(CLAUDETEAM_FEISHU_CREATOR_DIR="/no/such/dir"):
        with contextlib.redirect_stdout(io.StringIO()):
            assert feishu._run_bot_creator("ClaudeTeam", "feishu") is None


# ── artifact send ────────────────────────────────────────────────────────────


def test_send_file_accepts_only_configured_artifact_directory():
    calls = []
    with isolated_env(runtime_config={"chat_id": "oc_x"}) as tmp:
        artifact_dir = tmp / "artifacts"
        artifact_dir.mkdir()
        package = artifact_dir / "TripCanvas-debug.apk"
        package.write_bytes(b"apk")

        def sidecar(mode, *, extra_env=None):
            calls.append((mode, extra_env))
            return {"event": "file_sent", "message_id": "om_x"}

        with env_patch(CLAUDETEAM_ARTIFACT_DIR=str(artifact_dir)), \
                attr_patch(lark, subprocess_env=lambda: {
                    "FEISHU_APP_ID": "cli_x", "FEISHU_APP_SECRET": "secret"}):
            rc = feishu._send_file([str(package)], run_sidecar=sidecar)
        assert rc == 0
        assert calls[0][0] == "send-file"
        assert calls[0][1]["FEISHU_FILE_PATH"] == str(package.resolve())


def test_send_file_rejects_path_outside_artifact_directory():
    with isolated_env(runtime_config={"chat_id": "oc_x"}) as tmp:
        artifact_dir = tmp / "artifacts"
        artifact_dir.mkdir()
        outside = tmp / "secret.txt"
        outside.write_text("do not send", encoding="utf-8")
        with env_patch(CLAUDETEAM_ARTIFACT_DIR=str(artifact_dir)):
            rc = feishu._send_file([str(outside)], run_sidecar=lambda *a, **k: {})
        assert rc != 0
