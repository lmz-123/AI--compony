#!/usr/bin/env node
// ClaudeTeam Feishu/Lark onboarding + ingress sidecar.
//
// A THIN wrapper over the official `@larksuite/channel` SDK — the same SDK
// zarazhangrui/lark-channel-bridge is built on. We deliberately reuse its
// proven onboarding slice rather than re-implement it: this file is small,
// `node_modules` is gitignored (installed at deploy, never vendored), and the
// Python router/deliver/slash pipeline downstream is reused unchanged.
//
// It replaces two fragile pieces of the old design and adds the one piece the
// (single-agent, DM-first) bridge has no need for — bootstrapping a *group*
// for the whole crew:
//   • Playwright bot-creator     → `register`      RFC-8628 device-flow QR.
//                                                  One scan creates the app AND
//                                                  pre-grants scopes+events via
//                                                  `addons` (gray-scale permitting).
//   • lark-cli event +subscribe  → `run`           official WebSocket → NDJSON,
//                                                  in the lark-cli `--compact`
//                                                  flat shape process_lines parses.
//   • (new) team group bootstrap → `create-group`  channel.createChat, inviting
//                                                  the QR scanner (the owner).
//
// Convention: stdout = the machine channel (one JSON object per one-shot mode,
// NDJSON stream for `run`); stderr = human logs (QR, status, errors). Never mix.
import { registerApp, createLarkChannel, LarkChannelError } from "@larksuite/channel";
import qrcode from "qrcode-terminal";
import { spawn } from "node:child_process";

const log = (...a) => process.stderr.write(a.join(" ") + "\n");
const emit = (o) => process.stdout.write(JSON.stringify(o) + "\n");

// Best-effort: open the auth URL in the operator's default browser so they can
// just click "authorize" instead of scanning a QR with their phone. Silent
// no-op on a headless host (the opener may be absent or fail with no $DISPLAY)
// or when CLAUDETEAM_NO_BROWSER is set (CI / scripted runs).
function openBrowser(url) {
  if (process.env.CLAUDETEAM_NO_BROWSER) return;
  const [cmd, args] =
    process.platform === "darwin" ? ["open", [url]]
    : process.platform === "win32" ? ["cmd", ["/c", "start", "", url]]
    : ["xdg-open", [url]];
  try {
    const child = spawn(cmd, args, { stdio: "ignore", detached: true });
    // A missing opener (no xdg-open in a slim/Docker image) makes spawn emit an
    // async 'error' event — NOT a sync throw — so try/catch alone leaves it
    // unhandled and crashes Node mid-registration. Swallow it: the QR + URL are
    // already printed, so the auto-open is pure convenience.
    child.on("error", () => {});
    child.unref();
  } catch {}
}

// Scopes/events ClaudeTeam needs, pre-filled into the scan confirm page via
// `addons` (additive — Feishu can't drop base permissions). Tunable here:
//   im:message:send_as_bot — crews post cards into the group as the bot
//   im:message             — read inbound message content
//   im:message.group_msg   — deliver un-@'d group chatter (crews talk freely).
//                            SENSITIVE: granted on personal editions (owner==admin
//                            auto-approves) so un-@'d works there, but a stricter
//                            tenant may drop it on a one-scan --quick app — which is
//                            why we ALSO request the non-sensitive fallback:
//   im:message.group_at_msg:readonly — deliver @'d group messages. So that even when
//                            group_msg is dropped, @bot-in-groups still works instead
//                            of a silently-dead group.
//   im:message.p2p_msg:readonly — deliver 1:1 DMs to the bot
//   im:chat                — create the team group + invite the owner
//   application:...self_manage — read the app's own scopes (the `scopes` verify)
//                            + resolve its owner to invite (create-group); a fresh
//                            app can't self-read without it
const TENANT_SCOPES = [
  "im:message:send_as_bot",
  "im:message",
  "im:message.group_msg",
  "im:message.group_at_msg:readonly",
  "im:message.p2p_msg:readonly",
  "im:chat",
  "application:application:self_manage",
];
const TENANT_EVENTS = ["im.message.receive_v1"];
const ADDONS = { scopes: { tenant: TENANT_SCOPES }, events: { items: { tenant: TENANT_EVENTS } } };

function appCreds() {
  const appId = process.env.FEISHU_APP_ID || process.env.LARKSUITE_CLI_APP_ID;
  const appSecret = process.env.FEISHU_APP_SECRET || process.env.LARKSUITE_CLI_APP_SECRET;
  if (!appId || !appSecret) { log("✗ 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET"); process.exit(2); }
  return { appId, appSecret };
}

function renderQR({ url, expireIn }) {
  const valid = expireIn ? `约 ${Math.max(1, Math.round(expireIn / 60))} 分钟内有效` : "限时有效";
  // Link first + prominent (auto-opened in the browser); QR is the fallback.
  log("");
  log("  ┌─ 在浏览器里打开下面的链接完成授权（已尝试自动打开浏览器）─");
  log(`  │   ${url}`);
  log(`  └─ ${valid}。没有浏览器 / 在远程服务器上？用飞书 App 扫码：`);
  log("");
  qrcode.generate(url, { small: true }, (q) => process.stderr.write(q + "\n"));
  openBrowser(url);
}

// ── register: scan QR → create + provision a fresh PersonalAgent app ──────────
async function doRegister() {
  log("→ 用飞书扫码完成应用创建与授权（设备授权流程 RFC 8628）：");
  const r = await registerApp({
    source: "claudeteam",
    createOnly: true,                              // never bind an existing app by accident
    appPreset: { name: "ClaudeTeam · {user}", desc: "ClaudeTeam 多智能体团队" },
    addons: ADDONS,                                // one scan provisions scopes+events
    onQRCodeReady: renderQR,
    onStatusChange({ status }) {
      if (status === "domain_switched") log("  已识别国际版租户，切换到 lark 域名");
      else if (status === "slow_down") log("  轮询降速");
    },
  });
  emit({
    event: "registered",
    client_id: r.client_id,
    client_secret: r.client_secret,
    owner_open_id: r.user_info?.open_id ?? null,   // the scanner = the team owner
    tenant: r.user_info?.tenant_brand ?? "feishu",
  });
  log("✅ 应用已创建并授权，凭据已输出（stdout）。");
}

// ── scopes: report the app's currently-granted tenant scopes ──────────────────
// getAppInfo() only surfaces owner/name, so (like the bridge's app-scope.ts) we
// read the scope list off the raw node-sdk client. Lets `feishu connect` verify
// the required scopes actually landed (the guided flow's gate) / warn when a
// PersonalAgent lacks group_msg (--quick). NOTE: this self-read itself needs
// `application:application:self_manage`, so a fresh app returns [] until that
// scope is granted + the version published/approved — callers treat [] as
// "unknown", not "none".
async function doScopes() {
  const { appId, appSecret } = appCreds();
  const channel = createLarkChannel({ appId, appSecret });
  let granted = [];
  try {
    const res = await channel.rawClient.application.application.get({
      params: { lang: "zh_cn", user_id_type: "open_id" },
      path: { app_id: appId },
    });
    granted = (res.data?.app?.scopes ?? []).map((s) => s.scope).filter(Boolean);
  } catch (e) {
    log("  ⚠️ 读取已授权权限失败：" + (e?.message || e));
  }
  emit({ event: "scopes", granted });
}

// ── create-group: auto-create the team group + invite the owner ───────────────
async function doCreateGroup() {
  const { appId, appSecret } = appCreds();
  const name = process.env.FEISHU_GROUP_NAME || "ClaudeTeam";
  const channel = createLarkChannel({ appId, appSecret });
  // Owner = the QR scanner. Prefer the open_id captured at register; else
  // resolve it from the app owner API (same fallback the bridge relies on).
  let invite = process.env.FEISHU_OWNER_OPEN_ID || "";
  if (!invite) { try { invite = (await channel.getAppInfo())?.ownerId || ""; } catch {} }
  const { chatId } = await channel.createChat({
    name,
    description: "ClaudeTeam multi-agent workspace",
    inviteUserIds: invite ? [invite] : [],
    userIdType: "open_id",
  });
  emit({ event: "group_created", chat_id: chatId, invited: invite || null });
  log(`✅ 群已创建：${chatId}（已邀请 ${invite || "—"}）`);
}

// ── send: SDK egress — post AS THE BOT using the connected app's creds, NOT
// lark-cli (whose macOS keychain may hold a stale/different app and silently
// hijack the sender → "Bot can NOT be out of the chat"). One-shot. ────────────
async function doSend() {
  const { appId, appSecret } = appCreds();
  const chatId = process.env.FEISHU_CHAT_ID || "";
  const replyTo = process.env.FEISHU_REPLY_TO || "";
  const msgType = process.env.FEISHU_MSG_TYPE || "text";
  const content = process.env.FEISHU_CONTENT || "";
  if (!content || (!chatId && !replyTo)) {
    log("✗ send 缺少 FEISHU_CONTENT 和 FEISHU_CHAT_ID/FEISHU_REPLY_TO"); process.exit(2);
  }
  const channel = createLarkChannel({ appId, appSecret });
  const res = replyTo
    ? await channel.rawClient.im.v1.message.reply({
        path: { message_id: replyTo }, data: { msg_type: msgType, content } })
    : await channel.rawClient.im.v1.message.create({
        params: { receive_id_type: "chat_id" },
        data: { receive_id: chatId, msg_type: msgType, content } });
  emit({ event: "sent", message_id: res.data?.message_id ?? null, chat_id: chatId || null });
}

// ── run: official WebSocket channel → NDJSON on stdout ────────────────────────
async function doRun() {
  const { appId, appSecret } = appCreds();
  // ClaudeTeam routes EVERY group message to the manager (the whole
  // "no @ → manager" UX). The SDK's policy DEFAULTS to requireMention:true,
  // which silently drops un-@'d group messages *before* our `on("message")`
  // handler (reason "no_mention") — so without this the boss must @bot for
  // anything. Override it: deliver all group messages, honor @all broadcasts,
  // and accept DMs. (Receiving un-@'d msgs still also needs the app's
  // `im:message.group_msg` scope; this is the SDK-side half of the gate.)
  // NB: SDK safety defaults are left as-is — it silently drops messages whose
  // create_time is >30 min old. That's only reachable via an in-process WS
  // reconnect replaying a stale buffered event; the Python catchup path recovers
  // real backlog on daemon restart independently, so we accept the default rather
  // than widen it and risk replaying stale history.
  const channel = createLarkChannel({
    appId, appSecret,
    policy: { requireMention: false, respondToMentionAll: true, dmMode: "open" },
  });

  channel.on("message", (msg) => {
    // NormalizedMessage → the flat lark-cli --compact shape. content is
    // JSON-encoded {text} (what process_lines._extract_text expects).
    emit({
      message_id: msg.messageId,
      chat_id: msg.chatId,
      sender_id: msg.senderId,
      sender_type: "user",                          // bot-self loops are SDK-filtered
      message_type: "text",
      content: JSON.stringify({ text: msg.content ?? "" }),
      create_time: String(Date.now()),
      chat_type: msg.chatType,                       // pass-through for @mention gating
      mentioned_bot: !!msg.mentionedBot,
      mention_all: !!msg.mentionAll,
    });
  });

  // Lifecycle → stderr. A periodic heartbeat (no chat_id → dropped after
  // counting as alive) keeps the Python subscribe-watchdog fresh in quiet spells.
  for (const ev of ["reconnecting", "reconnected", "disconnected"]) {
    try { channel.on(ev, () => log(`  channel: ${ev}`)); } catch {}
  }
  // Keep this timer referenced. Some @larksuite/channel versions unref their
  // WebSocket internally after connect(), so the heartbeat may be the only
  // handle keeping `run` alive once connect() resolves.
  setInterval(() => emit({ type: "heartbeat", ts: Date.now() }), 30000);

  await channel.connect();
  log("✅ Channel WebSocket 已连接 → 事件转发为 NDJSON(stdout)。");
}

const MODES = {
  register: doRegister,
  scopes: doScopes,
  "create-group": doCreateGroup,
  send: doSend,
  run: doRun,
};
// One-shot modes resolve and must exit (createLarkChannel keeps timers/sockets
// alive); only `run` stays up, held by its WebSocket + heartbeat interval.
const ONESHOT = new Set(["register", "scopes", "create-group", "send"]);
const mode = process.argv[2];
const fail = (e) => {
  log("✗", e instanceof LarkChannelError ? `${e.code}: ${e.message}` : (e?.stack || e?.message || e));
  process.exit(1);
};
if (MODES[mode]) MODES[mode]().then(() => { if (ONESHOT.has(mode)) process.exit(0); }).catch(fail);
else { log("usage: sidecar.js register | scopes | create-group | send | run"); process.exit(2); }
