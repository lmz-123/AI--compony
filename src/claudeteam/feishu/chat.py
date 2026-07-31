"""Feishu chat operations: send_text, send_card.

Identity: callers pick `as_user` (True) vs `as_bot` (False, default).
User identity needs OAuth login on the lark-cli profile; bot identity
needs the app to have im:message scope.

All functions take an optional `lark_run=` callable for tests.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable

from claudeteam.feishu import lark
from claudeteam.feishu.lark import call as _real_run


def _real_sidecar_send(chat_id: str, *, msg_type: str, content: str,
                       reply_to: str = "") -> dict | None:
    """Egress AS THE BOT via `sidecar.js send` (channel SDK message.create/reply),
    using the CONNECTED app's creds (subprocess_env reads state/feishu_app.json) —
    NOT lark-cli, whose macOS keychain may hold a different/stale app and silently
    hijack the sender identity ("Bot can NOT be out of the chat"). Returns
    {chat_id, message_id} on success, None otherwise."""
    env = {**lark.subprocess_env(),
           "FEISHU_MSG_TYPE": msg_type, "FEISHU_CONTENT": content}
    if chat_id:
        env["FEISHU_CHAT_ID"] = chat_id
    if reply_to:
        env["FEISHU_REPLY_TO"] = reply_to
    try:
        proc = subprocess.run(["node", str(lark.sidecar_path()), "send"],
                              env=env, stdout=subprocess.PIPE, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    for raw in reversed((proc.stdout or "").strip().splitlines()):
        s = raw.strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
            except json.JSONDecodeError:
                continue
            if d.get("event") == "sent":
                return {"chat_id": d.get("chat_id"), "message_id": d.get("message_id")}
    return None


def _as(as_user: bool) -> list[str]:
    """lark-cli identity flag fragment: `--as user` (with OAuth) or
    `--as bot` (with the app's im:message scope)."""
    return ["--as", "user" if as_user else "bot"]


def send_text(chat_id: str, text: str, *, profile: str = "", as_user: bool = False,
              reply_to: str = "", lark_run: Callable = _real_run,
              sidecar_send: Callable = _real_sidecar_send) -> dict | None:
    """Send a plain-text message to a Feishu chat.

    Bot identity (the default) goes through the @larksuite/channel sidecar `send`
    mode, which sends with the CONNECTED app's creds — NOT lark-cli, whose macOS
    keychain can hold a different/stale app and hijack the sender identity
    ("Bot can NOT be out of the chat"). User identity (`as_user`) stays on lark-cli:
    only it carries the boss's OAuth profile. `reply_to` attaches as a reply
    (message.reply on the bot path; `+messages-reply` on the user path).

    Returns `{chat_id, message_id}` on success, None on failure.
    """
    if not chat_id and not reply_to:
        return None
    if as_user:
        if reply_to:
            args = ["im", "+messages-reply", "--message-id", reply_to, "--text", text, *_as(True)]
        else:
            args = ["im", "+messages-send", "--chat-id", chat_id, "--text", text, *_as(True)]
        return lark_run(args, profile=profile)
    return sidecar_send(chat_id, msg_type="text",
                        content=json.dumps({"text": text}, ensure_ascii=False),
                        reply_to=reply_to)


def send_card(chat_id: str, card: dict, *, profile: str = "", as_user: bool = False,
              lark_run: Callable = _real_run,
              sidecar_send: Callable = _real_sidecar_send) -> dict | None:
    """Send an interactive card (`card` = the Feishu card schema dict). Bot identity
    (default) → SDK sidecar egress; `as_user` → lark-cli. See send_text."""
    if not chat_id:
        return None
    if as_user:
        args = [
            "im", "+messages-send",
            "--chat-id", chat_id,
            "--msg-type", "interactive",
            "--content", json.dumps(card, ensure_ascii=False),
            *_as(True),
        ]
        return lark_run(args, profile=profile)
    return sidecar_send(chat_id, msg_type="interactive",
                        content=json.dumps(card, ensure_ascii=False), reply_to="")


def list_recent(chat_id: str, *, page_size: int = 20, profile: str = "",
                as_user: bool = True, lark_run: Callable = _real_run) -> list[dict] | None:
    """List recent messages in a chat (newest-first per Feishu API).

    Returns the `messages` array; defaults to user identity since the
    bot often lacks chat-history read permission.
    """
    if not chat_id:
        return []
    args = [
        "im", "+chat-messages-list",
        "--chat-id", chat_id,
        "--page-size", str(page_size),
        *_as(as_user),
        "--format", "json",
    ]
    data = lark_run(args, profile=profile)
    if data is None:
        return None  # lark call FAILED (auth / proxy) — distinct from an empty chat
    return list(data.get("messages") or [])
