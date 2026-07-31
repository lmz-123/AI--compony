"""Tests for runtime/config.py — team.json + runtime_config.json loading."""
from __future__ import annotations

import sys

from helpers import env_patch, isolated_env

from claudeteam.agents import adapter_for_agent
from claudeteam.agents.codex_cli import CodexCliAdapter
from claudeteam.agents.kimi_code import KimiCodeAdapter
from claudeteam.runtime import config

# 3.10 has no stdlib tomllib (added in 3.11); mirror the product code's fallback
# so the round-trip-via-real-parse assertions below work on the 3.10 CI leg too.
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _team_env(team_data, runtime_data=None):
    """Sugar over isolated_env(team=..., runtime_config=...) — keeps the
    older positional API the tests in this file have always used."""
    return isolated_env(team=team_data, runtime_config=runtime_data)


# ── team.json basics ────────────────────────────────────────────


def test_load_team_returns_default_when_missing():
    # isolated_env points CLAUDETEAM_TEAM_FILE at a tempdir that has no
    # team.json (since team= isn't passed), so config.load_team() takes
    # the missing-file → default-dict path.
    with isolated_env():
        t = config.load_team()
        assert t["agents"] == {}
        assert "session" in t


def test_session_name_derives_per_team_when_unset():
    """No `session` in the team → derive a per-team name from the config's
    location, so two teams on one host don't collide on a shared "ClaudeTeam"
    session (which `down`/`fire` would cross-kill)."""
    with _team_env({"agents": {}}):
        sn = config.session_name()
        assert sn == config.default_session_name()
        assert sn.startswith("ClaudeTeam-") and sn != "ClaudeTeam"


def test_agent_names_sorted():
    team = {"agents": {"z": {}, "a": {}, "m": {}}}
    with _team_env(team):
        assert config.agent_names() == ["a", "m", "z"]


# ── per-agent config ────────────────────────────────────────────


def test_agent_config_returns_copy():
    team = {"agents": {"a": {"cli": "claude-code", "model": "opus"}}}
    with _team_env(team):
        cfg = config.agent_config("a")
        cfg["model"] = "modified"
        # original team.json untouched
        assert config.agent_config("a")["model"] == "opus"


def test_agent_config_unknown_raises_keyerror():
    with _team_env({"agents": {}}):
        try:
            config.agent_config("ghost")
        except KeyError as exc:
            assert "ghost" in str(exc)
        else:
            raise AssertionError("expected KeyError")


def test_agent_cli_defaults_to_claude_code():
    team = {"agents": {"a": {}}}
    with _team_env(team):
        assert config.agent_cli("a") == "claude-code"


# ── model resolution chain ──────────────────────────────────────


def test_agent_model_uses_agent_specific_first():
    team = {"agents": {"a": {"model": "haiku"}}, "default_model": "opus"}
    with _team_env(team):
        assert config.agent_model("a") == "haiku"


def test_agent_model_uses_env_default_when_no_agent_model():
    team = {"agents": {"a": {}}, "default_model": "opus"}
    with _team_env(team), env_patch(CLAUDETEAM_DEFAULT_MODEL="sonnet"):
        assert config.agent_model("a") == "sonnet"


def test_agent_model_uses_team_default_when_no_env():
    team = {"agents": {"a": {}}, "default_model": "opus"}
    with _team_env(team), env_patch(CLAUDETEAM_DEFAULT_MODEL=None):
        assert config.agent_model("a") == "opus"


def test_agent_model_falls_back_to_opus_constant():
    team = {"agents": {"a": {}}}  # no default_model
    with _team_env(team), env_patch(CLAUDETEAM_DEFAULT_MODEL=None):
        assert config.agent_model("a") == "opus"


# ── runtime_config.json ─────────────────────────────────────────


def test_load_runtime_config_returns_empty_dict_when_missing():
    with _team_env({"agents": {}}):  # no runtime_data → file doesn't exist
        assert config.load_runtime_config() == {}


def test_chat_id_reads_runtime_config():
    with _team_env({"agents": {}}, runtime_data={"chat_id": "oc_xxx"}):
        assert config.chat_id() == "oc_xxx"


def test_chat_id_empty_when_unset():
    with _team_env({"agents": {}}, runtime_data={}):
        assert config.chat_id() == ""


def test_lark_profile_env_beats_file():
    with _team_env({"agents": {}}, runtime_data={"lark_profile": "from-file"}), \
            env_patch(LARK_CLI_PROFILE="from-env"):
        assert config.lark_profile() == "from-env"


def test_lark_profile_falls_back_to_file_when_env_unset():
    with _team_env({"agents": {}}, runtime_data={"lark_profile": "from-file"}), \
            env_patch(LARK_CLI_PROFILE=None):
        assert config.lark_profile() == "from-file"


# ── claudeteam.toml unified config (preferred over legacy json) ──


def _write_toml(tmp_dir, content: str):
    """Drop a claudeteam.toml in tmp + reset tunables cache."""
    from claudeteam.runtime import tunables
    (tmp_dir / "claudeteam.toml").write_text(content, encoding="utf-8")
    tunables.reset_cache()


def test_load_team_prefers_toml_over_legacy_json():
    """Both files exist → toml wins. Lets ops migrate without deleting
    old json; old json sticks around as a backup."""
    legacy = {"session": "from-legacy", "agents": {"old": {"cli": "claude-code"}}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """
[team]
session = "from-toml"
default_model = "opus"

[team.agents.new]
cli = "claude-code"
role = "新员工"
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            loaded = config.load_team()
        assert loaded["session"] == "from-toml"
        assert "new" in loaded["agents"]
        assert "old" not in loaded["agents"]


def test_load_team_falls_back_to_json_when_toml_missing():
    legacy = {"session": "S", "agents": {"a": {"cli": "claude-code"}}}
    with _team_env(legacy) as tmp:
        # No toml written. CONFIG_FILE points at non-existent path.
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "missing.toml")):
            loaded = config.load_team()
        assert loaded["session"] == "S"
        assert "a" in loaded["agents"]


def test_chat_id_prefers_toml():
    with _team_env({"agents": {}}, runtime_data={"chat_id": "oc_legacy"}) as tmp:
        _write_toml(tmp, 'chat_id = "oc_from_toml"\n')
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            assert config.chat_id() == "oc_from_toml"


def test_chat_id_falls_back_to_legacy_runtime_config():
    with _team_env({"agents": {}}, runtime_data={"chat_id": "oc_legacy"}) as tmp:
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "missing.toml")):
            assert config.chat_id() == "oc_legacy"


def test_lark_profile_priority_env_then_toml_then_legacy():
    """Three-way priority. env beats both; toml beats legacy json."""
    with _team_env({"agents": {}}, runtime_data={"lark_profile": "legacy"}) as tmp:
        _write_toml(tmp, 'lark_profile = "from-toml"\n')
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml"),
                       LARK_CLI_PROFILE="from-env"):
            assert config.lark_profile() == "from-env"
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml"),
                       LARK_CLI_PROFILE=None):
            assert config.lark_profile() == "from-toml"
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "missing.toml"),
                       LARK_CLI_PROFILE=None):
            assert config.lark_profile() == "legacy"


def test_save_runtime_config_roundtrip():
    with _team_env({"agents": {}}):
        config.save_runtime_config({"chat_id": "oc_new", "lark_profile": "p"})
        loaded = config.load_runtime_config()
        assert loaded == {"chat_id": "oc_new", "lark_profile": "p"}


# ── adapter_for_agent integration ───────────────────────────────


def test_adapter_for_agent_uses_team_json_cli_field():
    team = {"agents": {"w_codex": {"cli": "codex-cli"}, "w_kimi": {"cli": "kimi-code"}}}
    with _team_env(team):
        assert isinstance(adapter_for_agent("w_codex"), CodexCliAdapter)
        assert isinstance(adapter_for_agent("w_kimi"), KimiCodeAdapter)


def test_adapter_for_agent_unknown_agent_raises():
    with _team_env({"agents": {}}):
        try:
            adapter_for_agent("ghost")
        except KeyError:
            pass
        else:
            raise AssertionError("expected KeyError")


# ── lenient JSONDecodeError handling ─────────────────────────────


def test_load_team_returns_default_on_corrupt_json_with_warning():
    """REGRESSION: a malformed team.json used to raise JSONDecodeError
    straight through every claudeteam command. Now it falls back to
    the default + emits a stderr warning so the operator can fix the
    file without losing access to the CLI."""
    import io
    import contextlib
    with isolated_env() as tmp:
        team_path = tmp / "team.json"
        team_path.write_text("{ this is not valid json", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            loaded = config.load_team()
        # Default — empty agents dict
        assert loaded.get("agents") == {}
        assert loaded.get("session") == "ClaudeTeam"
        assert "team.json" in err.getvalue()
        assert "not valid JSON" in err.getvalue()


def test_load_runtime_config_returns_default_on_corrupt_json():
    """Sister case for runtime_config.json — same fallback semantics."""
    import io
    import contextlib
    with isolated_env() as tmp:
        rt_path = tmp / "runtime_config.json"
        rt_path.write_text("not-json-at-all", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            loaded = config.load_runtime_config()
        assert loaded == {}
        assert "runtime_config.json" in err.getvalue()
        assert "not valid JSON" in err.getvalue()


def test_session_name_falls_back_to_default_when_team_corrupt():
    """Downstream accessor `session_name()` should also degrade
    gracefully — `claudeteam start` shouldn't blow up just because
    team.json got truncated."""
    with isolated_env() as tmp:
        (tmp / "team.json").write_text("{partial", encoding="utf-8")
        import io, contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            # degrades to a usable default (now per-team-derived) — the point is
            # it doesn't blow up, not the exact string.
            assert config.session_name().startswith("ClaudeTeam")
            assert config.agent_names() == []


def test_load_team_falls_back_on_oserror_with_warning():
    """If the file is present but unreadable (e.g. permission denied,
    encoding error), the lenient loader should still return the default
    + warn. Easier to stage via attr_patch on read_json since we can't
    portably create an unreadable file in CI."""
    import io
    import contextlib
    from helpers import attr_patch
    from claudeteam.runtime import config as cfg_module

    def boom(*a, **kw):
        raise OSError("[Errno 13] Permission denied")

    with isolated_env(team={"agents": {"a": {}}}):
        # The file IS valid; we're simulating an OS-level read failure
        err = io.StringIO()
        with attr_patch(cfg_module, read_json=boom), \
                contextlib.redirect_stderr(err):
            loaded = config.load_team()
        # default: empty agents, default session
        assert loaded.get("agents") == {}
        assert "team.json" in err.getvalue()
        assert "unreadable" in err.getvalue()


# ── roster mutation: remove_agent / add_agent (fire / hire backing) ──


def test_remove_agent_deletes_from_team_json():
    import json
    team = {"session": "S", "agents": {"manager": {}, "x": {"cli": "claude-code"}}}
    with _team_env(team) as tmp:
        ok, msg = config.remove_agent("x")
        assert ok is True
        assert "removed x" in msg
        roster = json.loads((tmp / "team.json").read_text())
        assert "x" not in roster["agents"]
        assert "manager" in roster["agents"]


def test_remove_agent_not_in_roster_returns_false():
    with _team_env({"session": "S", "agents": {"manager": {}}}):
        ok, msg = config.remove_agent("ghost")
        assert ok is False
        assert "not in the team roster" in msg


def test_remove_agent_surgically_excises_toml_block_preserving_rest():
    """Toml is the real deployment source: remove_agent must excise the
    [team.agents.<x>] block while leaving the operator's comments + other
    sections byte-for-byte intact (no TOML writer; line surgery)."""
    legacy = {"session": "S", "agents": {}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """# hand-written header comment
[team]
session = "from-toml"
default_model = "opus"

[team.agents.manager]
cli = "claude-code"
role = "主管"

[team.agents.tx]
cli = "claude-code"
model = "sonnet"

[limits]
max = 5
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            ok, msg = config.remove_agent("tx")
            assert ok is True, msg
            assert "removed [team.agents.tx]" in msg
            text = (tmp / "claudeteam.toml").read_text()
            # tx block gone, everything else preserved
            assert "[team.agents.tx]" not in text
            assert "# hand-written header comment" in text
            assert "[team.agents.manager]" in text
            assert 'role = "主管"' in text
            assert "[limits]" in text and "max = 5" in text
            # and load_team reflects the removal
            loaded = config.load_team()
            assert "tx" not in loaded["agents"]
            assert "manager" in loaded["agents"]


def test_remove_agent_toml_agent_not_present_returns_false():
    legacy = {"session": "S", "agents": {}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """
[team]
session = "t"

[team.agents.manager]
cli = "claude-code"
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            ok, msg = config.remove_agent("ghost")
        assert ok is False
        assert "not in claudeteam.toml" in msg


def test_add_agent_inserts_toml_block_after_last_agent():
    legacy = {"session": "S", "agents": {}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """[team]
session = "t"

[team.agents.manager]
cli = "claude-code"

[limits]
max = 5
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            ok, msg = config.add_agent("revived", {"cli": "claude-code", "model": "opus"})
            assert ok is True, msg
            text = (tmp / "claudeteam.toml").read_text()
            assert "[team.agents.revived]" in text
            # inserted within the team.agents group, before [limits]
            assert text.index("[team.agents.revived]") < text.index("[limits]")
            loaded = config.load_team()
            assert loaded["agents"]["revived"]["model"] == "opus"


def test_add_agent_existing_does_not_duplicate_block():
    """Re-adding a present agent must overwrite, not emit a second
    [team.agents.<name>] table (duplicate tables make tomllib reject the
    whole file → load_team would fall back to defaults)."""
    legacy = {"session": "S", "agents": {}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """[team]
session = "t"

[team.agents.x]
cli = "claude-code"
model = "opus"
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            ok, _ = config.add_agent("x", {"cli": "claude-code", "model": "sonnet"})
            assert ok is True
            text = (tmp / "claudeteam.toml").read_text()
            # exactly one block, and it carries the new value
            assert text.count("[team.agents.x]") == 1
            loaded = config.load_team()   # would be {} if toml were unparseable
            assert loaded["agents"]["x"]["model"] == "sonnet"


def test_remove_then_add_agent_roundtrip_toml():
    """fire→hire round-trip at the config layer: remove an agent, re-add it
    from the stashed cfg, and load_team sees it again."""
    legacy = {"session": "S", "agents": {}}
    with _team_env(legacy) as tmp:
        _write_toml(tmp, """[team]
session = "t"

[team.agents.manager]
cli = "claude-code"

[team.agents.worker]
cli = "codex-cli"
model = "gpt-5.5"
role = "dev"
""")
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            cfg = config.agent_config("worker")
            assert config.remove_agent("worker")[0] is True
            assert "worker" not in config.load_team()["agents"]
            assert config.add_agent("worker", cfg)[0] is True
            back = config.load_team()["agents"]["worker"]
            assert back["cli"] == "codex-cli"
            assert back["model"] == "gpt-5.5"
            assert back["role"] == "dev"


# ── pure toml block helpers ──────────────────────────────────────


def test_toml_remove_block_preserves_next_sections_comment():
    """Firing codex must NOT eat kimi's comment that sits under codex's
    last key (between the block body and kimi's header)."""
    text = (
        "[team.agents.codex]\n"
        "cli = \"codex-cli\"\n"
        "model = \"gpt-5.5\"\n"
        "\n"
        "# kimi: the moonshot worker\n"
        "[team.agents.kimi]\n"
        "cli = \"kimi-code\"\n"
    )
    out, found = config._toml_remove_agent_block(text, "codex")
    assert found is True
    assert "[team.agents.codex]" not in out
    assert 'cli = "codex-cli"' not in out
    # kimi's comment + block survive intact
    assert "# kimi: the moonshot worker" in out
    assert "[team.agents.kimi]" in out
    # still valid + parses to just kimi
    assert list(tomllib.loads(out)["team"]["agents"]) == ["kimi"]


def test_toml_format_value_types():
    assert config._toml_format_value("opus") == '"opus"'
    assert config._toml_format_value(True) == "true"
    assert config._toml_format_value(5) == "5"
    assert config._toml_format_value(["a", "b"]) == '["a", "b"]'
    # quotes inside strings are escaped
    assert config._toml_format_value('a"b') == '"a\\"b"'


def test_toml_format_value_escapes_control_chars_to_valid_toml():
    """REGRESSION: a multi-line value must serialize to a VALID
    single-line basic string (escaped newline), not a raw newline that makes
    the whole file unparseable."""
    out = config._toml_format_value("line1\nline2\twith tab\n")
    assert "\n" not in out  # no raw newline in the emitted literal
    assert out == '"line1\\nline2\\twith tab\\n"'
    # and it round-trips exactly through a real TOML parse
    parsed = tomllib.loads(f"v = {out}")
    assert parsed["v"] == "line1\nline2\twith tab\n"


# ── verbatim block stash/restore (fire→hire fidelity bug) ───────────


_MULTILINE_TOML = '''[team]
session = "t"

[team.agents.manager]
cli = "claude-code"

# kimi 研究员
[team.agents.worker_kimi]
cli        = "kimi-code"
role       = "Kimi 研究员"
card_color = "blue"
specialty  = ["长上下文", "中文研究"]
notes = """
多行 notes 第一行
第二行
"""

[limits]
max = 5
'''


def test_extract_agent_block_returns_verbatim_text():
    with _team_env({"session": "S", "agents": {}}) as tmp:
        _write_toml(tmp, _MULTILINE_TOML)
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            block = config.extract_agent_block("worker_kimi")
            # alignment + multi-line value captured verbatim
            assert "cli        = \"kimi-code\"" in block
            assert 'notes = """' in block
            assert "多行 notes 第一行\n第二行" in block
            # nothing from the neighbouring sections
            assert "[team.agents.manager]" not in block
            assert "[limits]" not in block


def test_fire_hire_roster_block_roundtrip_is_field_faithful():
    """Manager's requested assertion: after a fire(remove)→hire(restore) the
    agent's roster block is field-for-field identical to the archived one —
    including a multi-line notes value that broke the dict-reserialize path."""
    with _team_env({"session": "S", "agents": {}}) as tmp:
        _write_toml(tmp, _MULTILINE_TOML)
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
            orig = config.agent_config("worker_kimi")
            stash = config.extract_agent_block("worker_kimi")          # fire stashes this
            assert config.remove_agent("worker_kimi")[0] is True       # fire removes
            assert "worker_kimi" not in config.load_team()["agents"]
            ok, _ = config.restore_agent_block("worker_kimi", stash)   # hire restores
            assert ok is True
            # toml still parses (the bug made it unparseable) …
            text = (tmp / "claudeteam.toml").read_text()
            tomllib.loads(text)
            # … and every field is identical to the archived roster
            assert config.agent_config("worker_kimi") == orig
            # verbatim fidelity: alignment + multi-line block survived
            assert "cli        = \"kimi-code\"" in text
            assert 'notes = """' in text


# ── set_chat_id (written by `feishu connect` after auto-creating the group) ────


def test_set_chat_id_writes_toml_in_place_preserving_comment():
    import os
    from pathlib import Path
    from claudeteam.runtime import tunables
    with isolated_env():
        cf = Path(os.environ["CLAUDETEAM_CONFIG_FILE"])
        cf.write_text('chat_id      = ""                  # the group\n',
                      encoding="utf-8")
        tunables.reset_cache()
        ok, _ = config.set_chat_id("oc_new")
        assert ok
        text = cf.read_text(encoding="utf-8")
        assert 'chat_id      = "oc_new"' in text
        assert "# the group" in text            # trailing comment preserved
        assert config.chat_id() == "oc_new"     # same-process read sees it


def test_set_chat_id_prepends_when_no_chat_id_line():
    import os
    from pathlib import Path
    from claudeteam.runtime import tunables
    with isolated_env():
        cf = Path(os.environ["CLAUDETEAM_CONFIG_FILE"])
        cf.write_text('[team]\nsession = "S"\n', encoding="utf-8")
        tunables.reset_cache()
        ok, _ = config.set_chat_id("oc_z")
        assert ok
        assert config.chat_id() == "oc_z"


def test_set_chat_id_falls_back_to_runtime_json_without_toml():
    # isolated_env pins CONFIG_FILE at a non-existent toml → json fallback path.
    with isolated_env():
        ok, msg = config.set_chat_id("oc_json")
        assert ok
        assert "runtime_config" in msg
        assert config.chat_id() == "oc_json"
