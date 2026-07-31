"""Tests for feishu/chat.py — chat operations using a fake lark_run."""
from __future__ import annotations

from helpers import CallRecorder as _Spy, KwRecorder
from claudeteam.feishu import chat


def test_send_text_bot_identity_default_routes_to_sidecar_sdk():
    """Bot egress goes through the channel-SDK sidecar (connected app's creds),
    NOT lark-cli — whose macOS keychain can hijack the sender identity."""
    spy = KwRecorder({"chat_id": "oc_xxx", "message_id": "om_1"})
    larkspy = _Spy({})
    out = chat.send_text("oc_xxx", "hello", sidecar_send=spy, lark_run=larkspy)
    assert out == {"chat_id": "oc_xxx", "message_id": "om_1"}
    assert larkspy.calls == []                        # never touched lark-cli
    assert spy.calls[0]["args"][0] == "oc_xxx"        # chat_id (1st positional)
    kw = spy.calls[0]["kwargs"]
    assert kw["msg_type"] == "text" and "hello" in kw["content"]


def test_send_text_as_user_when_flag_set():
    spy = _Spy({})
    chat.send_text("oc_x", "x", as_user=True, lark_run=spy)
    args = spy.calls[0]["args"]
    i = args.index("--as")
    assert args[i + 1] == "user"


def test_send_text_bot_reply_passes_reply_to_through_sidecar():
    """A bot reply attaches via the sidecar's reply_to (channel SDK message.reply),
    not lark-cli +messages-reply."""
    spy = KwRecorder({})
    larkspy = _Spy({})
    chat.send_text("oc_x", "x", reply_to="om_parent", sidecar_send=spy, lark_run=larkspy)
    assert larkspy.calls == []
    assert spy.calls[0]["kwargs"]["reply_to"] == "om_parent"


def test_send_text_as_user_reply_uses_lark_messages_reply():
    """REGRESSION: the user (OAuth) reply path still uses +messages-reply with
    --message-id (lark-cli +messages-send has no --reply-to)."""
    spy = _Spy({})
    chat.send_text("oc_x", "x", reply_to="om_parent", as_user=True, lark_run=spy)
    args = spy.calls[0]["args"]
    assert "+messages-reply" in args and "--message-id" in args and "om_parent" in args
    assert "+messages-send" not in args and "--reply-to" not in args


def test_send_text_returns_none_when_chat_id_empty():
    spy = _Spy({})
    assert chat.send_text("", "x", lark_run=spy) is None
    assert spy.calls == []  # never even called lark


def test_send_text_as_user_threads_profile_through_to_lark_run():
    """profile only applies on the lark-cli (user) path now; the bot path is the
    sidecar, which takes no lark-cli profile."""
    spy = _Spy({})
    chat.send_text("oc_x", "x", as_user=True, profile="prod", lark_run=spy)
    assert spy.calls[0]["kwargs"]["profile"] == "prod"


def test_send_card_bot_routes_to_sidecar_with_interactive_content():
    spy = KwRecorder({})
    larkspy = _Spy({})
    chat.send_card("oc_x", {"title": "hi"}, sidecar_send=spy, lark_run=larkspy)
    assert larkspy.calls == []
    kw = spy.calls[0]["kwargs"]
    assert kw["msg_type"] == "interactive"
    assert kw["content"].startswith("{") and "title" in kw["content"]


def test_send_card_as_user_uses_lark_cli_interactive():
    spy = _Spy({})
    chat.send_card("oc_x", {"title": "hi"}, as_user=True, lark_run=spy)
    args = spy.calls[0]["args"]
    assert "--msg-type" in args and "interactive" in args
    assert args[args.index("--as") + 1] == "user"
    content = args[args.index("--content") + 1]
    assert content.startswith("{") and "title" in content


def test_list_recent_returns_messages_list():
    spy = _Spy({"messages": [{"id": 1}, {"id": 2}], "has_more": False})
    out = chat.list_recent("oc_x", lark_run=spy)
    assert out == [{"id": 1}, {"id": 2}]


def test_list_recent_returns_empty_when_chat_id_blank():
    spy = _Spy({})
    assert chat.list_recent("", lark_run=spy) == []
    assert spy.calls == []


def test_list_recent_returns_none_when_lark_fails():
    # lark call failed (auth / proxy) → None, so catchup can tell it apart from
    # an empty chat ([]) and warn instead of silently losing the restart gap.
    spy = _Spy(None)
    assert chat.list_recent("oc_x", lark_run=spy) is None


def test_list_recent_returns_empty_list_for_empty_chat():
    # reachable chat with no messages → [] (NOT None)
    spy = _Spy({"messages": []})
    assert chat.list_recent("oc_x", lark_run=spy) == []


def test_list_recent_uses_user_identity_by_default():
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", lark_run=spy)
    args = spy.calls[0]["args"]
    assert args[args.index("--as") + 1] == "user"


def test_list_recent_can_override_to_bot_identity():
    """When the user OAuth profile has expired or isn't available,
    callers can fall back to bot — provided the app has chat-history
    read scope. Verify the override is wired correctly."""
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", as_user=False, lark_run=spy)
    args = spy.calls[0]["args"]
    assert args[args.index("--as") + 1] == "bot"


def test_list_recent_threads_page_size_into_argv():
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", page_size=50, lark_run=spy)
    args = spy.calls[0]["args"]
    assert "--page-size" in args
    assert args[args.index("--page-size") + 1] == "50"


def test_list_recent_threads_profile_through_to_lark_run():
    spy = _Spy({"messages": []})
    chat.list_recent("oc_x", profile="prod", lark_run=spy)
    assert spy.calls[0]["kwargs"]["profile"] == "prod"


def test_send_card_returns_none_when_chat_id_empty():
    """Sister to send_text's same guard — silently skip on empty
    chat_id rather than letting lark-cli error."""
    spy = _Spy({})
    assert chat.send_card("", {"title": "x"}, lark_run=spy) is None
    assert spy.calls == []


