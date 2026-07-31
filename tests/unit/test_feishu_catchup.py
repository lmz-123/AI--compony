"""Tests for feishu/catchup.py — cursor + replay-on-restart."""
from __future__ import annotations

import json

from helpers import isolated_env
from claudeteam.feishu import catchup
from claudeteam.feishu.router import Action, Decision
from claudeteam.feishu.subscribe import process_lines
from claudeteam.runtime import paths


# ── cursor I/O ──────────────────────────────────────────────────


def test_read_cursor_empty_when_file_missing():
    with isolated_env():
        assert catchup.read_cursor() == {}


def test_write_then_read_roundtrip():
    with isolated_env():
        catchup.write_cursor("om_42", "1719000000000")
        cur = catchup.read_cursor()
        assert cur["message_id"] == "om_42"
        assert cur["create_time"] == "1719000000000"


def test_write_cursor_persists_tz_free_epoch_ms():
    """write_cursor must stamp `create_time_ms` — the epoch reading taken
    while writer and renderer still share TZ. lark-cli renders the
    minute string in process-local time (verified: same message lists
    as 03:55 under TZ=Asia/Shanghai and 19:55 under TZ=UTC), so the
    string alone is ambiguous across a TZ change."""
    with isolated_env():
        catchup.write_cursor("om_42", "1778047712000")
        cur = catchup.read_cursor()
        assert cur["create_time_ms"] == 1778047712000
        # minute-string form: ms field equals the local-time parse made now
        catchup.write_cursor("om_43", "2026-05-06 14:08")
        cur = catchup.read_cursor()
        assert cur["create_time_ms"] == catchup._to_epoch_ms("2026-05-06 14:08")
        assert cur["create_time"] == "2026-05-06 14:08"  # raw kept for debug


def test_pending_lines_prefers_epoch_ms_when_string_tz_shifted():
    """THE container-UTC vs host-+8 restart scenario: the cursor's minute
    string was rendered under a TZ 8h away from the current process's,
    so re-parsing it now puts the cutoff 8h in the future and the whole
    gap silently drops. With `create_time_ms` persisted at event time,
    the cutoff stays correct no matter what TZ catchup runs under."""
    minute_07 = "1778047620000"   # 14:07
    minute_09 = "1778047740000"   # 14:09
    history = [_msg("om_m7", minute_07), _msg("om_m9", minute_09)]
    with isolated_env():
        # Hand-write the cursor as a TZ-shifted renderer would have left
        # it: string says 22:08 (rendered under +8h), ms says 14:08:32.
        paths.router_cursor_file().parent.mkdir(parents=True, exist_ok=True)
        paths.router_cursor_file().write_text(json.dumps({
            "message_id": "om_b2",
            "create_time": "2026-05-06 22:08",
            "create_time_ms": 1778047712000,
        }), encoding="utf-8")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    ids = sorted(json.loads(l)["event"]["message"]["message_id"] for l in lines)
    # ms cutoff (14:08 floor − 120s = 14:06) keeps both; the bogus string
    # cutoff (22:06) would have dropped everything.
    assert ids == ["om_m7", "om_m9"]


def test_pending_lines_legacy_cursor_without_ms_still_filters():
    """Cursors written before create_time_ms existed keep working via the
    string-parse fallback (same-TZ assumption, the historical behavior)."""
    minute_05 = "1778047500000"   # 14:05 — beyond lookback → drop
    minute_09 = "1778047740000"   # 14:09 — keep
    history = [_msg("om_old", minute_05), _msg("om_new", minute_09)]
    with isolated_env():
        paths.router_cursor_file().parent.mkdir(parents=True, exist_ok=True)
        paths.router_cursor_file().write_text(json.dumps({
            "message_id": "om_b2",
            "create_time": "1778047712000",   # legacy: no create_time_ms
        }), encoding="utf-8")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    ids = [json.loads(l)["event"]["message"]["message_id"] for l in lines]
    assert ids == ["om_new"]


def test_write_cursor_skips_when_either_field_blank():
    with isolated_env():
        catchup.write_cursor("", "1234")
        catchup.write_cursor("om_x", "")
        assert not paths.router_cursor_file().exists()


def test_read_cursor_returns_empty_on_garbage_json():
    with isolated_env():
        paths.ensure_state_dir()
        paths.router_cursor_file().write_text("not json", encoding="utf-8")
        assert catchup.read_cursor() == {}


def test_record_decision_advances_cursor_for_route():
    decision = Decision(action=Action.ROUTE, targets=["m"], text="x",
                        msg_id="om_99", create_time="1719999999000")
    with isolated_env():
        catchup.record_decision(decision)
        cur = catchup.read_cursor()
        assert cur["message_id"] == "om_99"


def test_record_decision_advances_cursor_for_drop_with_msg_id():
    decision = Decision(action=Action.DROP, msg_id="om_drop",
                        create_time="1720000000000", reason="empty")
    with isolated_env():
        catchup.record_decision(decision)
        cur = catchup.read_cursor()
        assert cur["message_id"] == "om_drop"


def test_record_decision_skips_when_no_msg_id_or_create_time():
    decision = Decision(action=Action.DROP, reason="no_msg_id")
    with isolated_env():
        catchup.record_decision(decision)
        assert not paths.router_cursor_file().exists()


# ── pending_lines ───────────────────────────────────────────────


def _msg(msg_id, create_time, *, text="hi", chat_id="oc_x", sender="ou_user"):
    return {
        "message_id": msg_id,
        "create_time": create_time,
        "chat_id": chat_id,
        "msg_type": "text",
        "sender": {"id": sender, "id_type": "open_id"},
        "body": {"content": json.dumps({"text": text})},
    }


def test_pending_lines_keeps_recent_earlier_minutes_within_lookback():
    """Minute-floor + reorder-lookback semantics. Cutoff is floored to the
    cursor minute then stepped back by the lookback margin (default 120s),
    so a message a minute or two BEFORE the cursor — a cross-minute
    out-of-order miss — survives instead of being filtered to oblivion.
    Only messages older than the lookback window drop. Documented in
    feishu/catchup._newer_than."""
    # Real minute-aligned epoch ms so the floor doesn't collapse to 0.
    minute_05 = "1778047500000"  # 2026-05-06 14:05:00 — before lookback → drop
    minute_07 = "1778047620000"  # 2026-05-06 14:07:00 — within 120s lookback → keep
    minute_08 = "1778047680000"  # 2026-05-06 14:08:00 — cursor minute → keep
    minute_09 = "1778047740000"  # 2026-05-06 14:09:00 — after cursor → keep
    history = [
        _msg("om_old", minute_05),               # > lookback older → still drops
        _msg("om_reorder", minute_07),           # earlier-but-missed → now kept
        _msg("om_b1", minute_08),                # cursor minute → keep
        _msg("om_b2", "1778047712000"),          # cursor itself, sub-minute → keep
        _msg("om_c1", minute_09),                # after cursor → keep
    ]
    with isolated_env():
        catchup.write_cursor("om_b2", "1778047712000")  # 14:08:32 → floor 14:08, −120s = 14:06
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    import json
    ids = sorted(json.loads(l)["event"]["message"]["message_id"] for l in lines)
    # 14:06 cutoff: om_old (14:05) drops; om_reorder (14:07) now survives.
    assert ids == ["om_b1", "om_b2", "om_c1", "om_reorder"]
    assert "om_old" not in ids


def test_pending_lines_recovers_messages_when_cursor_has_subminute_precision():
    """REGRESSION: the deeper bug — cursor written from live events has
    millisecond precision, REST API list_recent returns minute precision
    strings. A bare `>=` still loses same-minute messages because REST
    14:08:00 < cursor 14:08:32. Cutoff must floor to minute boundary."""
    cursor_ms = "1778047712107"  # 2026-05-06 14:08:32.107
    rest_minute = "2026-05-06 14:08"  # REST API shape, parses to 14:08:00
    history = [
        _msg("om_processed_via_live", cursor_ms),
        _msg("om_missed_a", rest_minute),
        _msg("om_missed_b", rest_minute),
    ]
    with isolated_env():
        catchup.write_cursor("om_processed_via_live", cursor_ms)
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = sorted(p["event"]["message"]["message_id"] for p in parsed)
    # Both REST-precision missed messages must be returned despite REST
    # parsing to 14:08:00 < cursor's 14:08:32.107
    assert "om_missed_a" in ids
    assert "om_missed_b" in ids


def test_pending_lines_recovers_messages_at_same_minute_as_cursor():
    """REGRESSION: lark WebSocket missed 4 of 9 slash commands all
    sharing the same minute as the cursor. Strict `>` cutoff lost them
    permanently. With `>=`, they come back via catchup. Same minute
    simulated here as same epoch-ms."""
    same_minute = "1778047200000"
    history = [
        _msg("om_processed", same_minute),   # cursor lands here after live event
        _msg("om_missed_a", same_minute),    # lark WebSocket missed; same minute
        _msg("om_missed_b", same_minute),    # ditto
    ]
    with isolated_env():
        catchup.write_cursor("om_processed", same_minute)
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    # All 3 returned (cursor itself + 2 missed) so the missed ones get
    # a second chance; in-process dedup will skip om_processed if it's
    # still in seen_msg_ids.
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    assert sorted(ids) == ["om_missed_a", "om_missed_b", "om_processed"]


def test_pending_lines_returns_empty_when_no_cursor():
    """Fresh deploy: catchup must NOT replay arbitrary chat history.
    Otherwise `claudeteam up` re-fires every recent message including
    old dispatches from a previous team — a fresh up replaying a
    30-min-old dispatch would make manager re-dispatch workers for a
    task the boss had cleared. Live subscribe picks up from "now"
    forward; first live event writes the cursor so subsequent restarts
    catch up only the gap."""
    history = [_msg("om_a", "100"), _msg("om_b", "200")]
    with isolated_env():
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert lines == []


def test_pending_lines_sorts_oldest_first_even_when_history_newest_first():
    history = [_msg("om_c", "300"), _msg("om_a", "100"), _msg("om_b", "200")]
    # Need a cursor so pending_lines doesn't take the fresh-deploy
    # short-circuit; cursor at create_time=50 keeps everything.
    with isolated_env():
        catchup.write_cursor("om_seed", "50")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    cts = [p["event"]["message"]["create_time"] for p in parsed]
    assert cts == ["100", "200", "300"]


def test_pending_lines_emits_subscribe_compatible_shape():
    history = [_msg("om_x", "500", text="hello world", sender="ou_42")]
    with isolated_env():
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_chat", list_fn=lambda: history)
    line = json.loads(lines[0])
    msg = line["event"]["message"]
    assert msg["message_id"] == "om_x"
    assert msg["chat_id"] == "oc_x"
    assert msg["create_time"] == "500"
    assert json.loads(msg["content"])["text"] == "hello world"
    assert line["event"]["sender"]["sender_id"]["open_id"] == "ou_42"


def test_pending_lines_carries_post_message_through_to_subscribe():
    """Catchup 拉历史时遇到 post (图+文混合) 消息也要转成 subscribe NDJSON
    形状, msg_type=post 被 subscribe._normalise → _extract_text 处理."""
    post_content = json.dumps({
        "title": "",
        "content": [[
            {"tag": "text", "text": "看这个 "},
            {"tag": "img", "image_key": "img_screenshot"},
        ]],
    })
    history = [{
        "message_id": "om_post",
        "create_time": "1000",
        "chat_id": "oc_x",
        "msg_type": "post",
        "sender": {"id": "ou_boss", "id_type": "user"},
        "body": {"content": post_content},
    }]
    with isolated_env():
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_chat", list_fn=lambda: history)
    line = json.loads(lines[0])
    msg = line["event"]["message"]
    assert msg["message_type"] == "post"
    # subscribe._normalise + _extract_text 已经在 subscribe 单测里覆盖
    # 把 post content → "[image: image_key=...]" + 文字, 这里 catchup
    # 只要保证 content 透传即可
    assert "img_screenshot" in msg["content"]


def test_pending_lines_default_list_fn_uses_bot_when_env_says_bot():
    """Bot-only deploys (no `lark-cli auth login` user) trip
    `need_user_authorization` from chat.list_recent's historical
    as_user=True default. Honor CLAUDETEAM_LARK_SEND_AS=bot like
    `say` does so the router catchup actually fetches."""
    captured = {}
    from claudeteam.feishu import chat as _chat
    real_list_recent = _chat.list_recent
    def spy(chat_id, **kw):
        captured["as_user"] = kw.get("as_user")
        return []
    _chat.list_recent = spy
    try:
        from helpers import env_patch
        with isolated_env(), env_patch(CLAUDETEAM_LARK_SEND_AS="bot"):
            catchup.write_cursor("seed", "1")
            catchup.pending_lines("oc_x")
        assert captured["as_user"] is False
    finally:
        _chat.list_recent = real_list_recent


def test_pending_lines_default_list_fn_honors_toml_send_as_bot():
    """Container deploy without env CLAUDETEAM_LARK_SEND_AS but with
    [feishu] send_as = "bot" in claudeteam.toml: catchup must respect
    the tunable and use bot identity. Bot-only container catchup got
    rc=2 because the env var wasn't pinned in docker-compose; the
    tunables fallback should cover that."""
    captured = {}
    from claudeteam.feishu import chat as _chat
    real_list_recent = _chat.list_recent
    def spy(chat_id, **kw):
        captured["as_user"] = kw.get("as_user")
        return []
    _chat.list_recent = spy
    try:
        from helpers import env_patch
        with isolated_env() as tmp:
            (tmp / "claudeteam.toml").write_text(
                '[feishu]\nsend_as = "bot"\n', encoding="utf-8")
            from claudeteam.runtime import tunables
            tunables.reset_cache()
            with env_patch(CLAUDETEAM_LARK_SEND_AS=None,
                            CLAUDETEAM_CONFIG_FILE=str(tmp / "claudeteam.toml")):
                catchup.write_cursor("seed", "1")
                catchup.pending_lines("oc_x")
        assert captured["as_user"] is False
    finally:
        _chat.list_recent = real_list_recent


def test_pending_lines_skips_messages_with_bad_create_time():
    history = [
        _msg("om_ok", "1000"),
        {"message_id": "om_bad", "create_time": "not-a-number",
         "chat_id": "oc_x", "msg_type": "text",
         "sender": {"id": "ou_x"}, "body": {"content": "{}"}},
    ]
    with isolated_env():
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    assert ids == ["om_ok"]


# ── round-trip via subscribe.process_lines ──────────────────────


# ── live lark-cli 1.0.21 response shape (REGRESSION) ────────────
#
# `lark-cli im +chat-messages-list` emits messages with content at TOP
# LEVEL (not under .body.content) AND create_time as a human-readable
# string ("2026-05-03 18:53"), not epoch ms. Earlier, catchup silently
# dropped every replayed message because:
#   - body.get("content") returned "" (no .body key in live shape)
#   - int("2026-05-03 ...") raised ValueError → message skipped
# Tests below pin the live shape so we don't regress.


def _msg_live(msg_id, create_time_iso, *, text="hi", chat_id="oc_x", sender="ou_user"):
    """Mirror lark-cli 1.0.21+ chat-messages-list shape: content at top
    level, create_time as 'YYYY-MM-DD HH:MM[:SS]'."""
    return {
        "message_id": msg_id,
        "create_time": create_time_iso,
        "chat_id": chat_id,
        "msg_type": "text",
        "sender": {"id": sender, "id_type": "open_id"},
        "content": json.dumps({"text": text}),
    }


def test_pending_lines_handles_live_lark_cli_shape():
    """REGRESSION: live shape has content at top + ISO create_time. Old
    catchup silently dropped these as 'empty' / unparseable."""
    history = [
        _msg_live("om_live_1", "2026-05-03 18:50",
                  text="hello from live shape"),
    ]
    with isolated_env():
        catchup.write_cursor("om_old", "1700000000000")  # in 2023
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert len(lines) == 1, (
        f"live-shape message should be kept; got {len(lines)} lines")
    payload = json.loads(lines[0])
    msg = payload["event"]["message"]
    assert msg["message_id"] == "om_live_1"
    # Content survives the conversion
    assert "hello from live shape" in msg["content"]


def test_pending_lines_iso_time_compared_correctly_against_epoch_cursor():
    """The cursor stores epoch ms (set by record_decision from subscribe
    events), but list_recent returns ISO strings. The comparator must
    coerce both to the same scale."""
    history = [
        _msg_live("om_before", "2026-05-03 17:00"),
        _msg_live("om_after", "2026-05-03 19:00"),
    ]
    with isolated_env():
        # Derive the cursor from the SAME local-time parse the messages use, so
        # the between-ness holds regardless of the runner's TZ. Hardcoding an
        # absolute epoch made this pass at +0800 but fail under UTC (CI): the
        # code parses naive ISO strings as LOCAL time, so the cursor must too.
        cursor_ms = catchup._to_epoch_ms("2026-05-03 18:00")
        catchup.write_cursor("om_cursor", str(cursor_ms))
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    parsed = [json.loads(l) for l in lines]
    ids = [p["event"]["message"]["message_id"] for p in parsed]
    # only om_after should survive (newer than cursor's epoch)
    assert ids == ["om_after"], f"expected only om_after, got {ids}"


def test_pending_lines_round_trip_with_live_shape_through_process_lines():
    """End-to-end: replay using the live shape, the events go through
    subscribe.process_lines and produce a real handled Decision (not
    silently dropped as 'empty')."""
    history = [_msg_live("om_replay_live", "2026-05-03 19:00",
                          text="catch this live")]
    with isolated_env():
        catchup.write_cursor("seed", "1")
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
        applies = []
        stats = process_lines(
            lines,
            team_agents=["manager"],
            chat_id="oc_x",
            apply_fn=lambda d: applies.append(d),
        )
    assert stats.handled == 1, (
        f"live-shape replay should produce 1 handled, got {dict(stats.drops_by_reason)}")
    assert applies[0].text == "catch this live"


# ── REGRESSION: cross-minute out-of-order silent-drop (永久丢消息) ──
#
# Bug: the cursor is a MONOTONIC high-water mark. When
# lark's WebSocket silently drops (~120s on macOS), an earlier message can
# be missed live while a LATER message gets classified and pushes the
# cursor past it. On the next catchup that earlier message is older than
# the high-water cursor, and the bare minute-floor cutoff filtered it out
# forever — it never reached `seen`, so dedup couldn't help, and the
# minute floor was too coarse for a cross-minute skew. The lookback margin
# in _newer_than is the fix.


def test_cross_minute_out_of_order_message_survives_catchup():
    """The reported永久丢消息 path: M_early (14:08:50) is missed live; M_late
    (14:09:05) is classified and advances the cursor past it. On catchup
    chat-messages-list returns both — M_early MUST come back, not vanish."""
    m_early = "1778047730000"   # 2026-05-06 14:08:50 — missed live (WS drop)
    m_late = "1778047745000"    # 2026-05-06 14:09:05 — classified, set the cursor
    history = [
        _msg("om_late", m_late, text="later, arrived first"),
        _msg("om_early", m_early, text="earlier, silently dropped"),
    ]
    with isolated_env():
        # cursor sits at the LATER message (high-water mark past the miss)
        catchup.write_cursor("om_late", m_late)
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    ids = sorted(json.loads(l)["event"]["message"]["message_id"] for l in lines)
    assert "om_early" in ids, "cross-minute out-of-order miss was lost again"
    assert "om_late" in ids   # re-applied; seen dedup makes that harmless


def test_lookback_zero_reproduces_the_old_loss():
    """Discriminating control: with the lookback disabled (the old
    monotonic-max-minute-floor behaviour), the earlier message IS filtered
    out — proving the regression test above passes *because of* the fix,
    not because the row was surviving anyway."""
    m_early = "1778047730000"   # 14:08:50
    m_late = "1778047745000"    # 14:09:05
    history = [_msg("om_late", m_late), _msg("om_early", m_early)]
    # lookback_ms=0 → cutoff == floor(14:09:05) == 14:09:00 → 14:08:50 drops
    fresh = catchup._newer_than(history, m_late, lookback_ms=0)
    ids = [m["message_id"] for m in fresh]
    assert "om_early" not in ids   # the bug, reproduced
    assert ids == ["om_late"]


def test_cross_minute_miss_delivered_once_through_process_lines():
    """End-to-end: the recovered earlier message flows through
    process_lines and is delivered, while the already-handled later
    message is dedup-dropped via the persisted seen set (no re-fire)."""
    m_early = "1778047730000"   # 14:08:50 — the miss
    m_late = "1778047745000"    # 14:09:05 — already handled before restart
    history = [
        _msg("om_late", m_late, text="already handled"),
        _msg("om_early", m_early, text="the missed one"),
    ]
    with isolated_env():
        catchup.write_cursor("om_late", m_late)
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
        applies = []
        # seed seen with the already-handled later message (mirrors
        # router._load_seen_msg_ids restoring state/router.seen on restart)
        stats = process_lines(
            lines,
            team_agents=["manager"],
            chat_id="oc_x",
            apply_fn=lambda d: applies.append(d),
            seen_msg_ids={"om_late"},
        )
    delivered = [d.msg_id for d in applies]
    assert delivered == ["om_early"], f"expected only the miss delivered, got {delivered}"
    assert stats.handled == 1


# ── catchup volume cap + seed-forward ───────────────────────────────


def test_pending_lines_caps_backlog_and_seeds_cursor_forward():
    """A large bring-up backlog must not all replay (router choke). Over the
    cap, keep only the most recent N, seed the cursor forward past the rest
    so a mid-bring-up respawn doesn't re-fetch and re-choke."""
    base = 1778047500000
    history = [_msg(f"om_{i}", str(base + i * 60000)) for i in range(40)]
    with isolated_env():
        catchup.write_cursor("om_cursor", str(base - 600000))  # 10 min before
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
        cur = catchup.read_cursor()             # read INSIDE the temp state dir
    ids = [json.loads(l)["event"]["message"]["message_id"] for l in lines]
    assert len(ids) == 30                       # default cap
    assert ids[0] == "om_10" and ids[-1] == "om_39"   # most-recent 30
    assert cur["message_id"] == "om_39"         # cursor seeded forward


def test_pending_lines_under_cap_replays_all():
    base = 1778047500000
    history = [_msg(f"om_{i}", str(base + i * 60000)) for i in range(5)]
    with isolated_env():
        catchup.write_cursor("om_cursor", str(base - 600000))
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert len(lines) == 5                       # under cap → untouched


def test_pending_lines_cap_is_tunable_off():
    """router.catchup_max_messages <= 0 disables the cap."""
    base = 1778047500000
    history = [_msg(f"om_{i}", str(base + i * 60000)) for i in range(40)]
    with isolated_env() as tmp:
        toml = tmp / "controls.toml"
        toml.write_text("[router]\ncatchup_max_messages = 0\n", encoding="utf-8")
        from helpers import env_patch
        from claudeteam.runtime import tunables
        with env_patch(CLAUDETEAM_CONFIG_FILE=str(toml)):
            tunables.reset_cache()
            catchup.write_cursor("om_cursor", str(base - 600000))
            lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    assert len(lines) == 40                      # cap off → all replay


# ── same-minute must replay oldest-first ──────────


def test_pending_lines_same_minute_replays_oldest_first():
    """REST returns newest-first + minute precision → same-minute messages
    tie on the sort key. Sent 1→5 within one minute, REST gives 5→1; catchup
    must emit 1→5, not the reversed 5→1."""
    minute = "1778047680000"          # one minute, all share it
    history = [_msg("om_5", minute), _msg("om_4", minute), _msg("om_3", minute),
               _msg("om_2", minute), _msg("om_1", minute)]   # REST newest-first
    with isolated_env():
        catchup.write_cursor("om_cursor", "1778047620000")   # the minute before
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    ids = [json.loads(l)["event"]["message"]["message_id"] for l in lines]
    assert ids == ["om_1", "om_2", "om_3", "om_4", "om_5"]


def test_pending_lines_cross_minute_still_chronological():
    """Distinct minutes still order by time regardless of REST order."""
    history = [_msg("om_late", "1778047740000"),     # 14:09 (REST newest)
               _msg("om_mid", "1778047680000"),      # 14:08
               _msg("om_early", "1778047620000")]    # 14:07
    with isolated_env():
        catchup.write_cursor("om_cursor", "1778047500000")   # 14:05
        lines = catchup.pending_lines("oc_x", list_fn=lambda: history)
    ids = [json.loads(l)["event"]["message"]["message_id"] for l in lines]
    assert ids == ["om_early", "om_mid", "om_late"]


# ── cap reports dropped count via meta ───────


def test_pending_lines_meta_reports_dropped_stale():
    base = 1778047500000
    history = [_msg(f"om_{i}", str(base + i * 60000)) for i in range(40)]
    meta = {}
    with isolated_env():
        catchup.write_cursor("om_cursor", str(base - 600000))
        catchup.pending_lines("oc_x", list_fn=lambda: history, meta=meta)
    assert meta["dropped_stale"] == 10        # 40 - default cap 30


