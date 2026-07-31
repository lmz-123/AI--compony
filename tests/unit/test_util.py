"""Tests for src/claudeteam/util.py — small shared helpers."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
from pathlib import Path

from helpers import env_patch, tmux_patch
from claudeteam.runtime import tmux
from claudeteam.util import (
    ago_ms, atomic_write_text, env_path, env_str, error_exit, flock,
    fmt_bytes, fmt_time_ms, help_requested, maybe_print_help, now_ms,
    pop_bool_flag, pop_flag, print_json, read_json, read_jsonl,
    reject_extra_args, reject_flag_as_agent, usage_error, warn,
)


# ── env_str ─────────────────────────────────────────────────────


def test_env_str_returns_stripped_value():
    with env_patch(X_TEST_ENV_STR="  hello  "):
        assert env_str("X_TEST_ENV_STR") == "hello"


def test_env_str_returns_empty_when_unset():
    with env_patch(X_TEST_ENV_STR_UNSET=None):
        assert env_str("X_TEST_ENV_STR_UNSET") == ""


# ── env_path ────────────────────────────────────────────────────


def test_env_path_returns_path_when_env_set():
    with env_patch(X_TEST_ENV_PATH="/tmp/foo"):
        assert env_path("X_TEST_ENV_PATH") == Path("/tmp/foo")


def test_env_path_returns_none_when_unset():
    with env_patch(X_TEST_ENV_PATH_UNSET=None):
        assert env_path("X_TEST_ENV_PATH_UNSET") is None


# ── now_ms ──────────────────────────────────────────────────────


# ── fmt_time_ms ─────────────────────────────────────────────────


def test_fmt_time_ms_default_format_is_minute_precision():
    # 2026-01-15 14:30:00 local time → ms epoch
    epoch = int(time.mktime((2026, 1, 15, 14, 30, 0, 0, 0, -1))) * 1000
    out = fmt_time_ms(epoch)
    assert "01-15" in out and "14:30" in out
    assert ":00" not in out  # no seconds in default fmt


def test_fmt_time_ms_custom_format_includes_seconds():
    epoch = int(time.mktime((2026, 1, 15, 14, 30, 45, 0, 0, -1))) * 1000
    out = fmt_time_ms(epoch, fmt="%m-%d %H:%M:%S")
    assert "14:30:45" in out


# ── fmt_bytes ───────────────────────────────────────────────────


def test_fmt_bytes_picks_unit_by_size():
    assert fmt_bytes(0) == "0 B"
    assert fmt_bytes(512) == "512 B"
    assert fmt_bytes(1024) == "1 KB"
    assert fmt_bytes(1500) == "1 KB"  # rounds down (.0f)
    assert fmt_bytes(1024 ** 2) == "1 MB"
    assert fmt_bytes(int(2.5 * 1024 ** 3)) == "2.50 GB"


# ── ago_ms ──────────────────────────────────────────────────────


def test_ago_ms_returns_question_for_zero_or_falsy():
    assert ago_ms(0) == "?"
    assert ago_ms(None) == "?"  # type: ignore[arg-type]


def test_ago_ms_seconds_under_60():
    # ms = 30000 means 30 seconds ago when now=60s
    assert ago_ms(30 * 1000, now=60.0) == "30s ago"
    assert ago_ms(0 * 1000 + 1, now=1.0) == "0s ago"


def test_ago_ms_clamps_to_zero_when_now_is_earlier_than_ms():
    # negative durations clamp to 0s
    assert ago_ms(10000, now=5.0) == "0s ago"


# ── atomic_write_text ───────────────────────────────────────────


def test_atomic_write_creates_file_and_writes_content():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_clobbers_stale_tmp_from_previous_crash():
    """Simulate a crash that left a .tmp behind; next call must succeed."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "out.txt"
        # leftover from "previous crash"
        (target.with_suffix(".txt.tmp")).write_text("stale", encoding="utf-8")
        atomic_write_text(target, "fresh")
        assert target.read_text(encoding="utf-8") == "fresh"


# ── warn ────────────────────────────────────────────────────────


def test_warn_writes_to_stderr_returns_none():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rv = warn("just a warning")
    assert rv is None
    assert "just a warning" in err.getvalue()


# ── error_exit ──────────────────────────────────────────────────


def test_error_exit_default_rc_is_one():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = error_exit("❌ something broke")
    assert rc == 1
    assert "something broke" in err.getvalue()


def test_error_exit_respects_custom_rc():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = error_exit("oops", rc=2)
    assert rc == 2


# ── usage_error ─────────────────────────────────────────────────


def test_usage_error_prints_to_stderr_and_returns_one():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = usage_error("usage: foo bar")
    assert rc == 1
    assert err.getvalue().strip() == "usage: foo bar"


# ── help_requested ──────────────────────────────────────────────


def test_help_requested_true_for_short_and_long():
    assert help_requested(["-h"]) is True
    assert help_requested(["--help"]) is True
    assert help_requested(["foo", "-h", "bar"]) is True


def test_help_requested_false_for_unrelated_args():
    assert help_requested([]) is False
    assert help_requested(["foo", "bar"]) is False
    assert help_requested(["-help"]) is False  # not a recognised form


# ── maybe_print_help ────────────────────────────────────────────


def test_maybe_print_help_prints_and_returns_true_on_help_flag():
    """Helper used by every subcommand main(): when the user passes
    -h/--help, the usage gets printed to stdout and the helper returns
    True so the caller can `return 0` immediately."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert maybe_print_help(["--help"], "usage: claudeteam X") is True
    assert "usage: claudeteam X" in out.getvalue()


def test_maybe_print_help_returns_false_without_help_flag():
    """No -h/--help → no print, returns False so caller continues normal flow."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert maybe_print_help(["foo", "bar"], "usage: claudeteam X") is False
    assert out.getvalue() == ""


# ── reject_extra_args ───────────────────────────────────────────


def test_reject_extra_args_returns_none_when_rest_empty():
    """No leftover args → None so caller continues normally."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert reject_extra_args([], "usage: foo") is None
    assert err.getvalue() == ""


def test_reject_extra_args_returns_one_and_prints_when_leftover():
    """Leftover args → stderr error containing the offending tokens AND
    the usage line, return rc=1 (from error_exit)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = reject_extra_args(["bogus", "extra"], "usage: foo bar")
    assert rc == 1
    msg = err.getvalue()
    assert "unexpected args" in msg
    assert "bogus" in msg
    assert "usage: foo bar" in msg


# ── reject_flag_as_agent (flag-as-agent guard) ──────────────


def test_reject_flag_as_agent_passes_real_name():
    """A normal agent name is not flag-shaped → None so caller continues."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert reject_flag_as_agent("worker_cc", "usage: foo") is None
    assert err.getvalue() == ""


def test_reject_flag_as_agent_rejects_dash_prefixed():
    """A '-'-prefixed token (misparsed option like --help) → rc=1 + usage,
    never accepted as an agent (would spawn a phantom agent in facts)."""
    for bad in ("--help", "-h", "--json"):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = reject_flag_as_agent(bad, "usage: foo bar")
        assert rc == 1, bad
        msg = err.getvalue()
        assert bad in msg
        assert "usage: foo bar" in msg


# ── print_json ──────────────────────────────────────────────────


# ── read_jsonl ──────────────────────────────────────────────────


def test_read_jsonl_returns_empty_when_missing(tmp_dir_path=None):
    """Missing file is the common 'no records yet' case; callers
    shouldn't have to special-case existence."""
    import tempfile, os
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        assert read_jsonl(Path(td) / "missing.jsonl") == []


def test_read_jsonl_parses_records_in_file_order():
    """Append-only convention: file order = chronological order, oldest
    first. read_jsonl preserves that."""
    import tempfile, os, json as _json
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.jsonl"
        p.write_text(
            _json.dumps({"i": 1}) + "\n"
            + _json.dumps({"i": 2}) + "\n"
            + _json.dumps({"i": 3}) + "\n",
            encoding="utf-8",
        )
        assert [r["i"] for r in read_jsonl(p)] == [1, 2, 3]


def test_read_jsonl_skips_blank_lines():
    """Blank lines (from manual edits or crash artefacts) must NOT
    raise json.JSONDecodeError on empty input."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.jsonl"
        p.write_text('\n{"i": 1}\n\n{"i": 2}\n\n', encoding="utf-8")
        assert [r["i"] for r in read_jsonl(p)] == [1, 2]


def test_read_jsonl_skips_corrupt_lines_silently():
    """Half-written lines from a crashed writer must NOT brick the file
    forever — drop them, keep the valid ones, let the next write append
    cleanly."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.jsonl"
        p.write_text(
            '{"i": 1}\n'
            '{"i": 2, "broken\n'  # truncated mid-line, no closing brace
            '{"i": 3}\n',
            encoding="utf-8",
        )
        assert [r["i"] for r in read_jsonl(p)] == [1, 3]


def test_print_json_uses_canonical_formatting():
    """ensure_ascii=False so Chinese stays readable; indent=2 so jq /
    scripts get diff-friendly multi-line output. The trailing
    newline is print()'s default, not part of json.dumps."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        print_json({"agent": "manager", "task": "回执"})
    text = out.getvalue()
    # multi-line indent
    assert '\n  "agent"' in text
    # Chinese kept literal, not escaped
    assert "回执" in text
    assert "\\u" not in text
    # newline at end of print()
    assert text.endswith("\n")


# ── pop_flag ────────────────────────────────────────────────────


def test_pop_flag_returns_value_and_removes_pair():
    rest = ["foo", "--by", "manager", "bar"]
    assert pop_flag(rest, "--by") == "manager"
    assert rest == ["foo", "bar"]


def test_pop_flag_returns_none_when_absent():
    rest = ["a", "b"]
    assert pop_flag(rest, "--by") is None
    assert rest == ["a", "b"]


def test_pop_flag_returns_none_when_value_missing_at_end():
    rest = ["a", "--by"]
    assert pop_flag(rest, "--by") is None
    # rest is unchanged so caller can flag the user error
    assert rest == ["a", "--by"]


# ── pop_bool_flag ───────────────────────────────────────────────


def test_pop_bool_flag_present_returns_true_and_removes():
    rest = ["foo", "--force", "bar"]
    assert pop_bool_flag(rest, "--force") is True
    assert rest == ["foo", "bar"]


def test_pop_bool_flag_absent_returns_false_and_no_change():
    rest = ["foo", "bar"]
    assert pop_bool_flag(rest, "--force") is False
    assert rest == ["foo", "bar"]


# ── read_json ───────────────────────────────────────────────────


def test_read_json_returns_default_when_file_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "missing.json"
        assert read_json(path, {}) == {}
        assert read_json(path, {"a": 1}) == {"a": 1}
        assert read_json(path, []) == []


def test_read_json_parses_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.json"
        atomic_write_text(path, '{"k": "v"}')
        assert read_json(path, {}) == {"k": "v"}


def test_read_json_propagates_decode_error():
    """Caller should get the JSONDecodeError on corrupt files; read_json
    doesn't try to be clever."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        try:
            read_json(path, {})
        except json.JSONDecodeError:
            return
        raise AssertionError("expected JSONDecodeError")


# ── flock ───────────────────────────────────────────────────────


def test_flock_creates_parent_and_yields():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "deep" / "lock"
        with flock(target):
            assert target.exists()  # lock file got created


def test_flock_releases_on_normal_exit():
    """After the contextmanager exits, the lock file is unlocked but
    still present on disk (lock files persist; only the kernel lock
    state goes away)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "lock"
        with flock(target):
            pass
        assert target.exists()
        # we can re-acquire immediately
        with flock(target):
            pass


def test_flock_releases_on_exception():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "lock"
        try:
            with flock(target):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # next acquire must succeed without hanging
        with flock(target):
            pass


# ── tmux_patch (helpers) ────────────────────────────────────────


