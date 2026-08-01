"""`claudeteam feishu <subcommand>` — Feishu app lifecycle.

`feishu connect` registers the bot ClaudeTeam talks through and auto-creates the
team group. Three modes:

  • DEFAULT — **browser-automated 自建应用**. Drives the Feishu console in a
    headed browser to create + scope + publish a self-built app that holds
    `im:message.group_msg`, the *sensitive* scope that lets the bot receive
    un-@'d group messages (plain text + slash + catchup). Scan the login QR
    once; the 7 console stages then run automatically. On any console-UI drift
    it **falls back to --manual**, so a broken selector degrades to "click it
    yourself" rather than a hard stop.

  • `--manual` — the same self-built app, but **you do the console clicks**,
    guided step-by-step with a ONE-CLICK permission deep-link (pre-selects every
    scope incl. `im:message.group_msg`). The robust fallback; no Playwright.

  • `--quick` — **one-scan PersonalAgent** (`@larksuite/channel` device-flow QR).
    Zero console clicks. The addons request BOTH `im:message.group_msg` (un-@'d)
    and `im:message.group_at_msg` (@'d). Whether the *sensitive* group_msg lands is
    tenant-dependent: a **personal edition** (you're the admin, so it auto-approves)
    gets it → even un-@'d group messages work; a stricter tenant may drop it →
    @'d-only via group_at_msg. `connect` verifies after register + tells you which.

Both persist App creds → `state/feishu_app.json` (0600) + `chat_id` →
`claudeteam.toml`, so `claudeteam up` then just works. The sidecar's machine
output (one JSON line per mode) is read off stdout; QR / human logs go to stderr.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from claudeteam.feishu import lark
from claudeteam.runtime import config
from claudeteam.util import (
    error_exit, maybe_print_help, pop_bool_flag, pop_flag, reject_extra_args,
)


USAGE = (
    "usage: claudeteam feishu connect [--quick|--manual] [--group-name NAME] "
    "[--tenant feishu|lark]\n"
    "       claudeteam feishu send-file <path> [--name FILE_NAME]\n"
    "  Register a Feishu/Lark bot for this team + auto-create the team group.\n"
    "  (default)  browser-automates a 自建应用 (un-@'d group messages work):\n"
    "             scan the login QR once, the rest is auto; falls back to\n"
    "             --manual if the console UI drifted.\n"
    "  --manual   step-by-step console guide (no browser automation).\n"
    "  --quick    one-scan device-flow QR (zero console) but the group needs @bot\n"
    "             and catchup is limited.\n"
    "  --group-name NAME     name for the auto-created group (default: ClaudeTeam).\n"
    "  --tenant feishu|lark  .cn (default) or .com.")

# Scopes a working ClaudeTeam bot MUST have to operate in a GROUP: post cards AND
# receive un-@'d messages (the whole point — "no @ → manager", plus slash commands
# + catchup). `im:message.group_msg` is the *sensitive* one: a 自建应用 gets it via
# the console + admin approval (guaranteed), and --quick's one-scan register also
# REQUESTS it via `addons` — landing on tenants that honor them (Feishu gray-scale),
# else falling back to @bot-in-groups. So it's not "guided=full vs quick=DM-only":
# _connect_quick verifies at runtime and tells the operator which they got.
_REQUIRED_SCOPES = frozenset({
    "im:message:send_as_bot",   # post cards into the group
    "im:message.group_msg",     # receive un-@'d group messages (boss types freely)
})

# Full scope set we pre-fill into the one-click permission deep-link so the
# operator approves them all in a single console confirm instead of ticking the
# checkboxes one by one. (Pattern lifted from AstrBot's Lark setup.)
# `application:application:self_manage` is what lets the app READ ITS OWN scopes
# (the `scopes` verify below) and resolve its owner to invite into the group
# (`create-group`'s getAppInfo) — without it a fresh app can't self-read and the
# verify/owner-invite silently fail. It is NOT in _REQUIRED_SCOPES (we don't gate
# on confirming it), just an enabler the deep-link grants.
_DEEPLINK_SCOPES = (
    "im:message:send_as_bot",
    "im:message.group_msg",
    "im:message.group_at_msg:readonly",
    "im:message.p2p_msg:readonly",
    "im:chat",
    "im:resource",
    "application:application:self_manage",
)


def _deeplink(app_id: str, tenant: str = "feishu") -> str:
    """One-click console link that opens the permission dialog with every
    ClaudeTeam scope pre-selected — the operator just clicks 确认."""
    host = "open.larksuite.com" if tenant == "lark" else "open.feishu.cn"
    return (f"https://{host}/app/{app_id}/auth"
            f"?q={','.join(_DEEPLINK_SCOPES)}&op_from=openapi&token_type=tenant")


def _sidecar_path() -> Path:
    """Resolve `scripts/feishu_channel/sidecar.js` (shared with the router
    ingress; honors CLAUDETEAM_FEISHU_SIDECAR_DIR)."""
    return lark.sidecar_path()


def _ensure_node_deps(sidecar_dir: Path) -> bool:
    """Install the sidecar's node_modules on first run (no-op once present)."""
    if (sidecar_dir / "node_modules" / "@larksuite" / "channel").exists():
        return True
    print("📦 installing sidecar deps (npm install)…")
    try:
        rc = subprocess.run(
            ["npm", "install", "--omit=dev", "--no-fund", "--no-audit"],
            cwd=str(sidecar_dir)).returncode
    except OSError:
        return False
    return rc == 0


def _run_sidecar(mode: str, *, extra_env: dict | None = None) -> dict | None:
    """Run `node sidecar.js <mode>`. stderr (QR + human logs) passes through to
    the operator's terminal; stdout (one JSON line) is captured and parsed.
    Returns the parsed object, or None on non-zero exit / no JSON line."""
    sidecar = _sidecar_path()
    env = {**os.environ, **(extra_env or {})}
    try:
        proc = subprocess.run(["node", str(sidecar), mode], env=env,
                              stdout=subprocess.PIPE, text=True)
    except OSError as e:
        print(f"  ✗ cannot run node sidecar: {e}")
        return None
    if proc.returncode != 0:
        return None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _creator_path() -> Path:
    """Resolve scripts/feishu_bot_creator/create_feishu_bot.js (sibling of the
    sidecar; honors CLAUDETEAM_FEISHU_CREATOR_DIR)."""
    env = os.environ.get("CLAUDETEAM_FEISHU_CREATOR_DIR")
    base = Path(env) if env else lark.sidecar_path().parent.parent / "feishu_bot_creator"
    return base / "create_feishu_bot.js"


def _run_bot_creator(name: str, tenant: str) -> tuple[str, str] | None:
    """Drive the Playwright bot-creator to stand up a PUBLISHED self-built app
    that holds im:message.group_msg (so the bot replies to un-@'d group
    messages). The creator opens a headed browser; the operator scans the login
    QR once, then the 7 console stages run automatically. stdio is inherited so
    the operator sees the QR + live progress; on success the App ID/Secret are
    read from the creator's state file. Returns (app_id, app_secret), or None on
    ANY failure (creator/deps missing, non-zero exit, unreadable state) so the
    caller falls back to the manual guided flow."""
    creator = _creator_path()
    if not creator.exists():
        print(f"  ℹ️ 未找到 bot-creator（{creator}）— 回退手动")
        return None
    if not (creator.parent / "node_modules" / "playwright").exists():
        print(f"  ℹ️ bot-creator 依赖未安装；先在 {creator.parent} 跑 "
              f"`npm install`（会下载 Chromium）— 本次回退手动")
        return None
    try:
        rc = subprocess.run(
            ["node", str(creator), "create", name],
            env={**os.environ, "FEISHU_TENANT": tenant}).returncode
    except OSError as e:
        print(f"  ℹ️ 无法运行 bot-creator: {e} — 回退手动")
        return None
    if rc != 0:
        return None
    try:
        data = json.loads(
            (creator.parent / ".state" / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    aid, sec = data.get("appId"), data.get("appSecret")
    return (str(aid), str(sec)) if aid and sec else None


def _sidecar_ready() -> str:
    """Validate the sidecar file + its node deps. Returns "" if ready, else an
    error message for the caller to error_exit with."""
    sidecar = _sidecar_path()
    if not sidecar.exists():
        return (f"sidecar not found at {sidecar}\n"
                f"   set CLAUDETEAM_FEISHU_SIDECAR_DIR to its directory")
    if not _ensure_node_deps(sidecar.parent):
        return f"failed to install sidecar deps; run `npm install` in {sidecar.parent}"
    return ""


def _granted_scopes(app_id: str, app_secret: str) -> set[str] | None:
    """The app's granted tenant scopes, or None if they couldn't be READ.

    The self-read (`application.get`) itself needs `application:application:
    self_manage`, which only lands after the version is published + approved —
    so an empty/failed read means "unknown", NOT "nothing granted". Callers must
    distinguish: a confirmed-missing scope is an abort; an unreadable app is a
    warn-and-proceed (else a correctly-set-up app dead-ends on a read it can't do
    yet)."""
    sc = _run_sidecar("scopes", extra_env={
        "FEISHU_APP_ID": app_id, "FEISHU_APP_SECRET": app_secret})
    if not sc:
        return None
    granted = sc.get("granted")
    return set(granted) if granted else None


def _create_group_and_persist(app_id: str, app_secret: str, group_name: str,
                              owner: str = "") -> int:
    """Shared tail: bot creates the team group (+ invites the owner) and we write
    chat_id into the config. owner='' → the sidecar resolves it via getAppInfo."""
    grp = _run_sidecar("create-group", extra_env={
        "FEISHU_APP_ID": app_id, "FEISHU_APP_SECRET": app_secret,
        "FEISHU_GROUP_NAME": group_name,
        **({"FEISHU_OWNER_OPEN_ID": owner} if owner else {})})
    if not grp or not grp.get("chat_id"):
        return error_exit("❌ 建群失败")
    chat_id = str(grp["chat_id"])
    ok, msg = config.set_chat_id(chat_id)
    if not ok:
        return error_exit(f"❌ {msg}")
    print(f"✅ 群已创建：{chat_id}")
    print("✅ feishu connect 完成 — 运行 `claudeteam up` 让团队进群报到。")
    return 0


# ── DEFAULT: guided self-built app (full group permissions) ───────────────────
def _connect_guided(argv: list[str], *, prompt=input) -> int:
    rest = list(argv)
    group_name = pop_flag(rest, "--group-name") or "ClaudeTeam"
    tenant = pop_flag(rest, "--tenant") or "feishu"
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    if err := _sidecar_ready():
        return error_exit(f"❌ {err}")
    console = "open.larksuite.com" if tenant == "lark" else "open.feishu.cn"

    print("=== 引导注册飞书【自建应用】（全权限：群里免 @ + 斜杠命令 + catchup）===\n")
    print(f"① 打开 https://{console}/app → 「创建企业自建应用」→ 填名字")
    print("② 「添加应用能力」→ 机器人 → 添加")
    print("③ 「凭证与基础信息」→ 复制 App ID + App Secret，粘贴到下面：\n")
    app_id = prompt("   App ID (cli_...): ").strip()
    app_secret = prompt("   App Secret: ").strip()
    if not app_id or not app_secret:
        return error_exit("❌ App ID / Secret 不能为空")
    lark.save_app_creds(app_id=app_id, app_secret=app_secret, tenant=tenant)
    print("\n✅ 凭据已存到 state/feishu_app.json (0600)\n")

    print("④ 点这个链接，一次把全部权限勾上 → 确认（已含敏感权限 im:message.group_msg）：\n")
    print(f"   {_deeplink(app_id, tenant)}\n")
    print("⑤ 「事件与回调」→ 订阅方式 = 使用长连接接收事件 → 添加事件「接收消息」")
    print("⑥ 「应用发布」→ 创建版本 → 申请发布 →（你是管理员就直接批准；个人版免审核）\n")
    prompt("   ④⑤⑥ 都做完后，按回车继续验证…")
    print()

    # Three outcomes — DON'T conflate "couldn't read scopes" with "scope missing":
    # the self-read needs self_manage + publish/approval, so a brand-new app that
    # was set up correctly may still be unreadable here. Only abort on a CONFIRMED
    # miss; otherwise warn and proceed (the group still gets created, and the live
    # round-trip after `up` is the real proof).
    granted = _granted_scopes(app_id, app_secret)
    if granted is None:
        print("⚠️  暂时读不到已授权权限（自建应用要 ⑥ 发版 + 管理员批准后才能自查）——先继续建群。")
        print("   若 `up` 后群里【不 @】的消息主管收不到：回控制台用 ④ 的链接确认勾了 "
              "im:message.group_msg + ⑥ 发版批准了，再重跑本命令即可。")
    elif missing := (_REQUIRED_SCOPES - granted):
        print(f"⚠️  确认还缺权限：{', '.join(sorted(missing))}")
        if "im:message.group_msg" in missing:
            print("   im:message.group_msg 是敏感权限：用 ④ 的链接勾上 + ⑥ 发版 + 管理员批准后才生效。")
        return error_exit("❌ 权限未到位 — 补齐后重跑 `claudeteam feishu connect`")
    else:
        print("✅ 关键权限到位（含 im:message.group_msg → 群里不 @ 也能收）")
    return _create_group_and_persist(app_id, app_secret, group_name)


# ── --quick: one-scan PersonalAgent (DM/@bot-only) ────────────────────────────
def _connect_quick(argv: list[str]) -> int:
    rest = list(argv)
    group_name = pop_flag(rest, "--group-name") or "ClaudeTeam"
    tenant_flag = pop_flag(rest, "--tenant") or ""
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    if err := _sidecar_ready():
        return error_exit(f"❌ {err}")

    print("📱 扫码注册【个人版应用】（零后台，但群里要 @bot；终端显示二维码 / 链接）…")
    reg = _run_sidecar("register")
    if not reg or not reg.get("client_id"):
        return error_exit("❌ registration failed or was cancelled")
    app_id = str(reg["client_id"])
    app_secret = str(reg.get("client_secret", ""))
    owner = str(reg.get("owner_open_id") or "")
    tenant = tenant_flag or str(reg.get("tenant") or "feishu")
    lark.save_app_creds(app_id=app_id, app_secret=app_secret,
                        owner_open_id=owner, tenant=tenant)
    print(f"✅ 应用已注册并保存凭据：{app_id}")

    # The addons request BOTH group_msg (un-@'d) and group_at_msg (@'d). Which the
    # tenant grants is the open question: a personal edition (owner==admin) auto-
    # approves the sensitive group_msg → un-@'d works; a stricter tenant may keep
    # only group_at_msg → @'d-only. The scope-read returns a broad template bundle,
    # so it's a hint not proof — the real check is a live group message after `up`.
    granted = _granted_scopes(app_id, app_secret)
    if granted and "im:message.group_msg" in granted:
        print("✅ 已申请到免 @ 的 im:message.group_msg + @ 的 group_at_msg。个人版一般免 @ 也能收——"
              "`up` 后群里发条【不 @】的试试；收不到就是本租户没真放行敏感权限，那就 @bot，"
              "或改用 `claudeteam feishu connect`（自建应用）拿稳的免 @。")
    elif granted and "im:message.group_at_msg:readonly" in granted:
        print("✅ 已授予 @ 的 im:message.group_at_msg → 群里 **@bot** 能收（没拿到免 @ 的 "
              "im:message.group_msg；要免 @ 改用 `claudeteam feishu connect` 自建应用）。")
    else:
        print("ℹ️  暂时读不到已授权权限——先建群。`up` 后群里发条试试；@bot 都收不到再改用 "
              "`claudeteam feishu connect`（自建应用）。")
    return _create_group_and_persist(app_id, app_secret, group_name, owner=owner)


# ── DEFAULT: browser-automate the self-built app (un-@'d group messages) ──────
def _connect_auto(argv: list[str], *, run_creator=None, prompt=input) -> int:
    """Drive the Feishu console in a browser to stand up a published self-built
    app with im:message.group_msg, then create the group. Scan the login QR
    once; the rest is automated. Falls back to the manual guided flow on ANY
    automation failure (Feishu UI drift) — a broken selector degrades to "click
    it yourself", never a hard stop."""
    rest = list(argv)
    group_name = pop_flag(rest, "--group-name") or "ClaudeTeam"
    tenant = pop_flag(rest, "--tenant") or "feishu"
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc
    run_creator = run_creator or _run_bot_creator
    print("=== 浏览器自动注册飞书【自建应用】（群里免 @）：扫一次登录码，其余自动点 ===")
    print("（撞上飞书后台改版会自动回退到手动引导，不卡死）\n")
    creds = run_creator("ClaudeTeam", tenant)
    if not creds:
        print("\n⚠️ 浏览器自动化未完成 — 回退到手动引导：\n")
        return _connect_guided(argv, prompt=prompt)
    app_id, app_secret = creds
    lark.save_app_creds(app_id=app_id, app_secret=app_secret, tenant=tenant)
    print(f"✅ 应用已创建并发布：{app_id}（凭据已存 state/feishu_app.json）")
    return _create_group_and_persist(app_id, app_secret, group_name)


def _connect(argv: list[str]) -> int:
    rest = list(argv)
    try:
        if pop_bool_flag(rest, "--quick"):
            return _connect_quick(rest)
        if pop_bool_flag(rest, "--manual"):
            return _connect_guided(rest)
        return _connect_auto(rest)
    except (EOFError, KeyboardInterrupt):
        # No stdin / non-TTY (a piped or CI invocation) or ^C mid-prompt — the
        # guided flow's input() prompts raise here. Abort cleanly instead of
        # dumping an EOFError traceback through the generic cli error handler.
        print()
        return error_exit("❌ 已取消（无输入 / 非交互终端）— "
                          "在可交互终端里重跑 `claudeteam feishu connect`")


def _send_file(argv: list[str], *, run_sidecar=None) -> int:
    """Upload one regular artifact from the configured artifact directory."""
    rest = list(argv)
    file_name = pop_flag(rest, "--name") or ""
    if not rest:
        return error_exit(f"❌ 缺少文件路径\n{USAGE}")
    raw_path = rest.pop(0)
    if (rc := reject_extra_args(rest, USAGE)) is not None:
        return rc

    artifact_dir = Path(os.environ.get("CLAUDETEAM_ARTIFACT_DIR", "/data/artifacts")).resolve()
    try:
        file_path = Path(raw_path).resolve(strict=True)
    except OSError as e:
        return error_exit(f"❌ 文件不可用：{e}")
    try:
        file_path.relative_to(artifact_dir)
    except ValueError:
        return error_exit(f"❌ 只允许发送 {artifact_dir} 内的构建产物")
    if not file_path.is_file():
        return error_exit("❌ 目标不是普通文件")
    size = file_path.stat().st_size
    if size <= 0 or size > 200 * 1024 * 1024:
        return error_exit("❌ 文件必须大于 0 且不超过 200 MiB")
    safe_name = file_name or file_path.name
    if Path(safe_name).name != safe_name or safe_name in {".", ".."}:
        return error_exit("❌ --name 只能是文件名，不能包含目录")

    chat_id = config.chat_id()
    if not chat_id:
        return error_exit("❌ chat_id is empty; run `claudeteam feishu connect`")
    env = lark.subprocess_env()
    extra_env = {
        "FEISHU_CHAT_ID": chat_id,
        "FEISHU_FILE_PATH": str(file_path),
        "FEISHU_FILE_NAME": safe_name,
        "CLAUDETEAM_ARTIFACT_DIR": str(artifact_dir),
        **{k: env[k] for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET") if env.get(k)},
    }
    runner = run_sidecar or _run_sidecar
    result = runner("send-file", extra_env=extra_env)
    if not result or result.get("event") != "file_sent":
        return error_exit("❌ 飞书文件上传或发送失败；确认机器人已获得 im:resource 权限")
    print(f"✅ 已发送文件到飞书群：{safe_name} ({size} bytes)")
    return 0


_SUBCOMMANDS = {"connect": _connect, "send-file": _send_file}


def main(argv: list[str]) -> int:
    rest = list(argv)
    if maybe_print_help(rest, USAGE):
        return 0
    if not rest:
        print(USAGE)
        return 0
    sub = rest.pop(0)
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        return error_exit(f"unknown feishu subcommand: {sub!r}\n{USAGE}")
    return handler(rest)
