"""Tests for `claudeteam router` daemon entry.

The Popen + signal-handler + endless-loop machinery in main() can't be
sanely unit-tested — it's plumbing around process_lines (separately
covered by test_feishu_subscribe + the in-process integration suite).
What CAN and SHOULD be tested:
  - _build_subscribe_cmd: the argv we hand to lark-cli
  - main() early-validation paths: missing chat_id, empty team,
    pidlock already held — all should exit non-zero with a clear
    stderr message before any subprocess is spawned.
"""
from __future__ import annotations

from pathlib import Path

from helpers import attr_patch, env_patch, isolated_env, run_cli
from claudeteam.commands.router import (
    _build_subscribe_cmd,
    _diagnose_sidecar_exit,
    _load_seen_msg_ids,
    _make_on_progress,
    _notify_catchup_skips,
    _stale_event_threshold_s,
    _subscribe_rotate_reason,
    _watch_subscribe_health,
)


# Stub the sidecar path so test argv doesn't depend on the repo layout.
_STUB_SIDECAR = lambda: "/fake/scripts/feishu_channel/sidecar.js"


# ── _build_subscribe_cmd (Channel SDK sidecar ingress) ─────────────


def test_build_cmd_launches_sidecar_run():
    """Ingress is `node <repo>/scripts/feishu_channel/sidecar.js run` — the
    @larksuite/channel sidecar that emits the same --compact NDJSON shape
    process_lines already parses. Replaced the old lark-cli `event +subscribe`
    argv (which silently dropped its WebSocket on macOS)."""
    cmd = _build_subscribe_cmd("ignored-profile", sidecar=_STUB_SIDECAR)
    assert cmd == ["node", "/fake/scripts/feishu_channel/sidecar.js", "run"]


def test_build_cmd_uses_lark_sidecar_path_by_default():
    """Default sidecar path comes from `lark.sidecar_path()` so one resolver
    (honoring CLAUDETEAM_FEISHU_SIDECAR_DIR) feeds both the ingress and
    `feishu connect`."""
    from claudeteam.feishu import lark
    cmd = _build_subscribe_cmd("")
    assert cmd == ["node", str(lark.sidecar_path()), "run"]


def test_build_cmd_no_larkcli_subscribe_argv():
    """REGRESSION: the lark-cli `event +subscribe` path is deleted — none of
    its args may leak back in. The sidecar binds to the resolved app creds via
    env (lark.subprocess_env), not the argv, so there is no --profile / --as /
    --event-types / --force / +subscribe / --compact on the command line."""
    cmd = _build_subscribe_cmd("", sidecar=_STUB_SIDECAR)
    joined = " ".join(cmd)
    for stale in ("+subscribe", "--force", "--event-types",
                  "--as", "--compact", "--profile"):
        assert stale not in joined, f"stale lark-cli arg {stale!r} re-introduced"


def test_sidecar_run_heartbeat_keeps_node_alive():
    """REGRESSION: @larksuite/channel may unref its WebSocket after connect().
    The heartbeat must remain referenced or `sidecar.js run` exits immediately,
    making watchdog respawn the router every 30 seconds."""
    sidecar = Path(__file__).resolve().parents[2] / "scripts/feishu_channel/sidecar.js"
    source = sidecar.read_text(encoding="utf-8")
    heartbeat = source.split("async function doRun()", 1)[1]
    heartbeat = heartbeat.split("await channel.connect()", 1)[0]
    assert "setInterval(" in heartbeat
    assert ".unref" not in heartbeat


# ── main() early validations ─────────────────────────────────────


def test_main_returns_one_when_chat_id_missing():
    """Empty chat_id in runtime_config → main exits before spawning
    lark-cli with a clear error."""
    team = {"agents": {"manager": {"cli": "claude-code"}}}
    rc_cfg = {"chat_id": "", "lark_profile": "test"}  # explicit empty
    with isolated_env(team=team, runtime_config=rc_cfg):
        rc, _, err = run_cli(["router"])
    assert rc == 1
    assert "chat_id" in err
    assert "runtime_config.json" in err


def test_main_returns_one_when_team_has_no_agents():
    """An empty team.json `agents` map means there's nothing to route
    TO — the daemon would just drop everything."""
    team = {"agents": {}}
    rc_cfg = {"chat_id": "oc_x", "lark_profile": "test"}
    with isolated_env(team=team, runtime_config=rc_cfg):
        rc, _, err = run_cli(["router"])
    assert rc == 1
    assert "no agents" in err


# ── help ────────────────────────────────────────────────────────


# ── stale-event self-restart ──────────────────────────────────────


def _patch_platform(name: str):
    """Force platform.system() to return `name` so tests are deterministic
    across the runner's OS. macOS dev laptop and Linux CI box would
    otherwise see different defaults (Darwin → 120, else → 600)."""
    import platform
    return attr_patch(platform, system=lambda: name)


def test_stale_threshold_default_linux_is_600s():
    """Linux WebSocket is stable; default stays 600s. Calibrated value:
    1200 too lax / 180 too tight (see commit history)."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S=None), _patch_platform("Linux"):
        assert _stale_event_threshold_s() == 600.0


def test_stale_threshold_default_darwin_is_120s():
    """macOS lark-cli 1.0.23 WebSocket silently drops without reconnect.
    Tighter default lets self-restart + catchup recover in ~2 min
    instead of ~10."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S=None), _patch_platform("Darwin"):
        assert _stale_event_threshold_s() == 120.0


def test_stale_threshold_picks_up_env_override():
    """Env override beats platform default — operators can tune."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S="60"), _patch_platform("Darwin"):
        assert _stale_event_threshold_s() == 60.0


def test_stale_threshold_falls_back_to_default_on_garbage():
    """Misconfigured env (`CLAUDETEAM_ROUTER_STALE_S=potato`) should fall
    back to platform default rather than raise."""
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_STALE_S="potato"), _patch_platform("Linux"):
        assert _stale_event_threshold_s() == 600.0


def test_make_on_progress_refreshes_timestamp_on_each_event():
    """Every successful (non-DROP) event should bump last_event_at[0]
    so the watchdog's stale check sees fresh activity. DROP events don't
    flow through process_lines' on_progress, so they don't refresh."""
    from types import SimpleNamespace
    with isolated_env():
        last_event_at = [0.0]
        events_seen = [0]
        cb = _make_on_progress(last_event_at, events_seen)
        # Mock decision (only attribute used is by record_decision; we patch
        # catchup.record_decision to a no-op so we don't need full Decision).
        from claudeteam.feishu import catchup
        real_record = catchup.record_decision
        catchup.record_decision = lambda d: None
        try:
            before = last_event_at[0]
            cb(SimpleNamespace(msg_id="om_x"), object())
            after = last_event_at[0]
        finally:
            catchup.record_decision = real_record
    # time.monotonic always > 0 since boot; before was 0.0
    assert after > before
    # genuine handled event is counted (idle-vs-stalled signal for #5)
    assert events_seen[0] == 1


def test_subscribe_rotate_reason_idle_is_calm_not_alarming():
    """events_seen==0 is the NORMAL macOS idle case (no inbound yet this
    session; live WS goes quiet, catchup recovers on restart). The log must
    NOT scream 'silently stalled' — it reads as broken on a fresh deploy."""
    line = _subscribe_rotate_reason(130.0, 120.0, events_seen=0)
    assert "ℹ️" in line
    assert "stalled" not in line.lower()
    assert "catchup" in line.lower()


def test_subscribe_rotate_reason_after_events_is_warning():
    """events_seen>0 means events WERE flowing then stopped — genuinely
    more notable (esp. Linux, where the WS is supposed to be stable)."""
    line = _subscribe_rotate_reason(130.0, 120.0, events_seen=5)
    assert "⚠️" in line
    assert "stopped" in line.lower()


def test_watch_subscribe_health_self_terminates_on_stale_events():
    """Subscribe child alive but no events for > threshold → SIGTERM
    self. REGRESSION: lark WebSocket silently stalling left the router
    process looking healthy in `ps` while user messages went unprocessed
    for 7+ min."""
    import threading, signal, os
    from claudeteam.commands import router as _r

    class FakeProc:
        def __init__(self): self.returncode = None
        def poll(self): return None  # never exits

    sigterms = []
    real_kill = os.kill
    os.kill = lambda pid, sig: sigterms.append((pid, sig))
    try:
        # env override beats toml + module default in tunable() — speeds
        # up the loop without depending on whatever sits in the user's
        # claudeteam.toml.
        with env_patch(CLAUDETEAM_ROUTER_STALE_S="0.1",
                       CLAUDETEAM_ROUTER_SUBSCRIBE_WATCHDOG_PERIOD_S="0.05"):
            stop_event = threading.Event()
            # last_event_at far in the past → stale
            last_event_at = [0.0]
            t = threading.Thread(
                target=_watch_subscribe_health,
                args=(FakeProc(), stop_event, last_event_at, [0]),
                daemon=True,
            )
            t.start()
            t.join(timeout=2.0)
        assert sigterms, "watchdog thread didn't SIGTERM on stale events"
        assert sigterms[0][1] == signal.SIGTERM
    finally:
        os.kill = real_kill


def test_watch_subscribe_health_self_terminates_on_child_exit():
    """Subscribe child exits (non-stale-events path). Coverage for the
    pre-existing fail mode: npm-exec parent stays alive holding stdout
    open, lark-cli grandchild dies."""
    import threading, signal, os, time
    from claudeteam.commands import router as _r

    class FakeProc:
        def __init__(self): self.returncode = 137  # SIGKILL'd
        def poll(self): return self.returncode

    sigterms = []
    real_kill = os.kill
    os.kill = lambda pid, sig: sigterms.append((pid, sig))
    try:
        # Stale threshold high so we know the trigger was the dead child;
        # subscribe_watchdog_period_s low so the loop iterates fast.
        with env_patch(CLAUDETEAM_ROUTER_STALE_S="3600",
                       CLAUDETEAM_ROUTER_SUBSCRIBE_WATCHDOG_PERIOD_S="0.05"):
            stop_event = threading.Event()
            last_event_at = [time.monotonic()]  # fresh
            t = threading.Thread(
                target=_watch_subscribe_health,
                args=(FakeProc(), stop_event, last_event_at, [0]),
                daemon=True,
            )
            t.start()
            t.join(timeout=1.0)
        assert sigterms, "watchdog thread didn't SIGTERM on child exit"
        assert sigterms[0][1] == signal.SIGTERM
    finally:
        os.kill = real_kill


def test_diagnose_sidecar_exit_surfaces_ws_failure_hint():
    """When the sidecar's last output shows a WebSocket-connect failure, the exit
    diagnostic must NAME the two fixes (enable 长连接 / LARK_CLI_NO_PROXY) instead
    of the real error getting bad_json-dropped + lost in a respawn loop."""
    import io, contextlib
    recent = ["[info]: ws client connecting",
              "[error]: '[ws]', 'ws connect failed'",
              "[ws] reconnect... 放弃"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _diagnose_sidecar_exit(1, recent)
    out = buf.getvalue()
    assert "长连接" in out and "LARK_CLI_NO_PROXY" in out, out
    assert "ws connect failed" in out, "should echo the sidecar's own last lines"


def test_diagnose_sidecar_exit_no_ws_signature_prints_tail_only():
    """A clean/unknown exit still shows the tail (for debugging) but must NOT fire
    the WS-specific 长连接/proxy advice — that'd be a misleading false lead."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _diagnose_sidecar_exit(0, ["handled event om_x", "router quiet"])
    out = buf.getvalue()
    assert "router quiet" in out
    assert "长连接" not in out and "LARK_CLI_NO_PROXY" not in out, out


def test_diagnose_sidecar_exit_tolerates_none_recent_lines():
    """recent_lines=None (no buffer / older call site) must not raise."""
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        _diagnose_sidecar_exit(1, None)  # must simply not raise


# ── persisted dedup set (state/router.seen) ──────────────────────


def test_load_seen_returns_empty_when_file_missing():
    with isolated_env():
        assert _load_seen_msg_ids() == set()


def test_load_seen_reads_one_msg_id_per_line():
    from claudeteam.runtime import paths
    with isolated_env():
        paths.ensure_state_dir()
        paths.router_seen_file().write_text("om_a\nom_b\nom_c\n")
        assert _load_seen_msg_ids() == {"om_a", "om_b", "om_c"}


def test_load_seen_skips_blank_lines():
    from claudeteam.runtime import paths
    with isolated_env():
        paths.ensure_state_dir()
        paths.router_seen_file().write_text("om_a\n\nom_b\n   \n")
        assert _load_seen_msg_ids() == {"om_a", "om_b"}


def test_load_seen_truncates_huge_file_to_recent_window():
    """Bound the file size — long-running deploy can't grow seen.json
    indefinitely. Truncate to the last `router.seen_max_lines` on load.
    Use a tiny override (50) via env so the test stays fast without
    materialising a 5000-line file."""
    from claudeteam.runtime import paths
    cap = 50
    with isolated_env(), env_patch(CLAUDETEAM_ROUTER_SEEN_MAX_LINES=str(cap)):
        paths.ensure_state_dir()
        # Write more than the cap; oldest should be dropped.
        ids = [f"om_{i}" for i in range(cap + 200)]
        paths.router_seen_file().write_text("\n".join(ids) + "\n")
        loaded = _load_seen_msg_ids()
        assert len(loaded) == cap
        # Oldest dropped, newest kept
        assert "om_0" not in loaded
        assert f"om_{cap + 199}" in loaded
        # File on disk also truncated for next boot
        on_disk = paths.router_seen_file().read_text().strip().splitlines()
        assert len(on_disk) == cap


def test_on_progress_appends_msg_id_to_seen_file():
    """REGRESSION: manager's own /tmux manager card was forwarded into
    manager inbox every ~3.5min as the router self-restarted. Root
    cause: seen_msg_ids was an in-memory set, not persisted, so catchup
    replay after restart re-applied messages.
    Now: each on_progress fires append-to-file."""
    from types import SimpleNamespace
    from claudeteam.runtime import paths
    with isolated_env():
        last_event_at = [0.0]
        cb = _make_on_progress(last_event_at, [0])
        # Mock the catchup.record_decision side effect
        from claudeteam.feishu import catchup
        real_record = catchup.record_decision
        catchup.record_decision = lambda d: None
        try:
            cb(SimpleNamespace(msg_id="om_first"), object())
            cb(SimpleNamespace(msg_id="om_second"), object())
            cb(SimpleNamespace(msg_id=""), object())  # blank id is skipped
        finally:
            catchup.record_decision = real_record
        contents = paths.router_seen_file().read_text()
        assert "om_first" in contents
        assert "om_second" in contents
        # Empty id didn't add a blank line
        assert _load_seen_msg_ids() == {"om_first", "om_second"}


def test_seen_persists_across_simulated_restart():
    """Two consecutive _make_on_progress sessions sharing the same
    state dir: second session's _load_seen_msg_ids must see what the
    first session wrote."""
    from types import SimpleNamespace
    with isolated_env():
        from claudeteam.feishu import catchup
        real_record = catchup.record_decision
        catchup.record_decision = lambda d: None
        try:
            cb1 = _make_on_progress([0.0], [0])
            cb1(SimpleNamespace(msg_id="om_X"), object())
        finally:
            catchup.record_decision = real_record
        # Simulate restart: load again
        seen = _load_seen_msg_ids()
        assert "om_X" in seen


# ── post a skip-notice to the routing target ──


def test_notify_catchup_skips_posts_when_dropped_or_slash():
    from claudeteam.store import local_facts
    with isolated_env():
        _notify_catchup_skips("manager", dropped_stale=3, slash_skipped=2)
        msgs = local_facts.list_messages("manager")
    assert len(msgs) == 1
    body = msgs[0]["content"]
    assert "3 条" in body and "2 条" in body
    assert "task intent get" in body          # ties to live-read discipline


def test_notify_catchup_skips_noop_when_nothing_skipped():
    from claudeteam.store import local_facts
    with isolated_env():
        _notify_catchup_skips("manager", dropped_stale=0, slash_skipped=0)
        assert local_facts.list_messages("manager") == []
