# 飞书机器人注册 — `claudeteam feishu connect --quick`（默认）

## 目的

证明「扫一次登录码 → 建 bot 应用 → 建群 → 落凭证」这条**默认** `--quick` 链路真的成立：
一次扫码、零控制台、任意机器（含无界面服务器）都能跑，命令建出 bot 应用 + 团队群 +
凭证（`state/feishu_app.json` 0600）+ `chat_id`。注册完之后**事件入站走
`scripts/feishu_channel/sidecar.js run`**（官方 WebSocket → NDJSON），群里实发一句话
manager 真能收到回（个人版免 @ 也能收，严格租户则群里 @bot；以群里实发一条为准）。这是
host_smoke 的**前置**——host_smoke 默认你已经跑过本篇。

覆盖：

- `claudeteam init`（首次部署自动跑 `feishu connect`）/ 或单独 `claudeteam feishu connect --quick`
- **默认 `--quick` 流**：扫一次登录码 → 建 bot 应用 + 团队群 + 落凭证。零控制台、
  任意机器（含纯 headless 服务器）都能跑。
- 群自动创建 + 把 owner 拉进群
- 凭证落盘 `state/feishu_app.json`（0600）+ `chat_id` 写进 `claudeteam.toml`
- `claudeteam up` 后主管自动发起全员点名（自检）
- 群里实发一条 → manager 回（个人版免 @ 也能收，严格租户则群里 @bot；以群里实发为准）
- **跨租户保证免 @ 的路径（浏览器自动自建应用）**：无参 `claudeteam feishu connect` 扫一次
  登录码 → 在桌面浏览器里自动跑完 7 个控制台阶段（create-app → add-bot → import-scopes
  → data-range → events → callbacks → publish）→ 发布出带敏感权限 `im:message.group_msg`
  的企业自建应用，**跨所有租户都群里免 @ 也能收**；需桌面浏览器，撞上控制台改版会自动回退到
  `--manual`。这条 fragile-automation 路径在 §6 完整覆盖。

## 适用范围

- 平台：macOS / Linux **有桌面浏览器**的本机部署（host 模式）。默认流要开一个
  **有界面（headed）的浏览器**自动点控制台——**纯 headless / 无显示器的服务器跑不了**
  默认流（见下「无界面环境怎么办」）。Docker 部署见
  [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md)：App 凭证走 `.env`、chat_id 手填，
  不在本篇范围。
- 已装：Python 3.10+、tmux、node + npx、`lark-cli`（出站发卡用）、至少一个
  agent CLI（`claude` / `codex` / …）在 PATH 上；bot-creator 的 Playwright + Chromium
  依赖已装（首次在 `scripts/feishu_bot_creator/` 跑过 `npm install`，会下载 Chromium）。
- 已跑：`pip install -e .`（`claudeteam` 在 PATH 上）。
- 有飞书企业 + 管理员账号——发布自建应用要管理员能批准（你是管理员就直接过）。
  **真人只做一件事：在弹出的浏览器里扫一次登录二维码**；7 个控制台阶段由命令自动点完、
  自动发版，命令再负责验权 + 建群。

> **无界面环境怎么办**：默认流需要桌面浏览器，纯服务器跑不了。两条路：①在一台有桌面的
> 机器上跑 `claudeteam feishu connect` 生成 `state/feishu_app.json`，再把这个文件（0600）
> 拷到服务器，chat_id 写进服务器的 `claudeteam.toml`；②直接在服务器上用 `--manual`
> 手动点控制台（不需要浏览器自动化）。

## 前置条件

```bash
cd /path/to/ClaudeTeam
source .venv/bin/activate
# 本篇验证「从零注册」，先确认没有残留凭证
ls state/feishu_app.json 2>/dev/null && echo "已存在——connect 会覆盖；想干净重测先移走它"
```

## 操作（Given / When / Then）

### 1. 跑 connect，扫一次登录码（默认浏览器自动流）

**Given** 一台有桌面浏览器的机器 + 一个飞书账号（管理员能批发版），

**When** 跑

```bash
claudeteam init                  # 首次部署：写完 toml 后自动跑 feishu connect
# 或单独：
claudeteam feishu connect
# 跳过（CI / Docker / 已手填凭证）：claudeteam init --no-connect
```

命令弹出一个**有界面的浏览器**并在终端打出登录二维码，**用飞书手机端扫这一次码**，

**Then** 登录后命令自动把 7 个控制台阶段一路点完：

1. create-app（建自建应用，拿 App ID）
2. add-bot（加机器人能力）
3. import-scopes（批量导入权限，含敏感的 `im:message.group_msg`）
4. data-range（数据范围 = 全部）
5. events（长连接模式 + 订阅消息事件）
6. callbacks（长连接模式 + 卡片回调）
7. publish（创建版本 + 发布）

> **只这一步要真人**：扫那一次登录二维码。之后 7 个阶段全自动，**不用再贴 App
> ID/Secret，也不用手点权限链接或发版**。终端会逐阶段打 `Stage N/7 …` 的进度。

> **撞上飞书后台改版（控制台 UI 漂移）** → 命令自动**回退到 `--manual`**（见 §2）：
> 打印一条一键权限 deep-link + ④⑤⑥ 步骤、停在「按回车继续验证」，由你手点。这是健壮
> 兜底，不会卡死；回退后照样落同样的 `state/feishu_app.json` + `chat_id`，验收从 §3 起一样。

### 2. 备选路径（仅记一句，不在本篇主跑）

**Given** 默认流跑不了（无桌面浏览器）或你想手动，
**When** 二选一，
**Then**：

- `claudeteam feishu connect --manual` —— 同款企业自建应用，但**你手点控制台**：贴
  App ID/Secret → 点一键权限 deep-link → 发版。命令打完步骤停在「按回车继续验证」，
  做完控制台再回车，命令拉一次已授权权限核对。无桌面浏览器的服务器走这条。
- `claudeteam feishu connect --quick` —— 一次扫码注册**个人版应用**（零后台），但飞书
  不给个人版 `im:message.group_msg`，**群里必须 @bot**——本篇不覆盖这条。

> `im:message.group_msg` 是敏感权限：默认流靠 import-scopes + 自动 publish 拿到、
> `--manual` 靠 deep-link 勾上 + 发版 + 管理员批准后生效；个人版（`--quick`）拿不到。

### 3. 落盘核对（机判）

**Given** 应用发布、群已建，
**When** 看磁盘，
**Then** 满足全部三条：

```bash
# (a) App 凭证落盘且权限 0600
ls -l state/feishu_app.json          # 期望 -rw-------（0600）
python3 -c "import json; d=json.load(open('state/feishu_app.json')); print('app_id' in d and bool(d.get('app_id')))"
# 期望 True（含 app_id 且非空；app_secret 同理但别打印出来）

# (b) chat_id 写进 toml
grep -E '^\s*chat_id\s*=\s*"oc_' claudeteam.toml
# 期望命中一行 chat_id = "oc_..."

# (c) 飞书里出现「ClaudeTeam」群、owner 在群里
CHAT=$(grep -E '^\s*chat_id' claudeteam.toml | sed -E 's/.*"(oc_[^"]+)".*/\1/')
LARK_CLI_NO_PROXY=1 lark-cli im +chat-search --query "ClaudeTeam" --as user --format json \
  | python3 -c "import json,sys; print([c.get('chat_id') for c in json.load(sys.stdin).get('data',{}).get('items',[])])"
# 期望列表里含上面的 $CHAT
```

**通过条件**：(a) 文件 mode `-rw-------`；(b) `grep` 命中一行；(c) 群能搜到且
chat_id 对得上、你在成员里（飞书 App 里直接看群也行）。

### 4. 上线 + 主管点名（自检，全程无需真人）

**Given** 凭证 + chat_id 都就位，
**When**

```bash
claudeteam install-hooks         # 要在 up 之前
claudeteam up
```

**Then** 首次 `up` 后主管（manager）**自动发起全员点名**：先在群里宣布，再逐一通知
每个 worker，各 worker 自己在群里汇报身份与状态，最后主管汇总。`claudeteam health` 全绿。

> 若 `chat_id` 没设，`claudeteam up` 会直接报错并指向 `claudeteam feishu connect`。

**通过条件（看群里，无需真人发消息）**：`claudeteam up` 后几分钟内，群里能看到主管的
点名公告 + 每个非退休 worker 的汇报 + 主管的汇总。看到这些 = 主管派单 + worker 在群里回
整条链路都通。

### 5. 入站回环 — 不 @ 也能收（证明 `im:message.group_msg` + sidecar 通了）

**Given** 团队已上线，
**When** 在群里发一句**不 @ 任何人**的带锚定的话，

```bash
ANCHOR="connect-回环-$(date +%s)"
LARK_CLI_NO_PROXY=1 lark-cli im +messages-send \
  --chat-id "$CHAT" --text "收到请回复 $ANCHOR" --as user
```

**Then** 二选一即算通（任一成立都证明默认流给到了 `im:message.group_msg`）：

- **看群（首选）**：60 秒内群里能看到 manager 的回复卡，**内容里带 `$ANCHOR`**——证明自建
  应用的 `im:message.group_msg` 生效（不 @ 也推给 bot）+
  `node scripts/feishu_channel/sidecar.js run` 的 WebSocket 入站 → router → manager 整条
  链路通了（不是回复以前的消息）。
- **看 sidecar 收没收到（机判旁路）**：这句**不 @** 的消息要被 sidecar 收下、且标
  `mentioned_bot:false`。个人版 / `--quick` 没有 `im:message.group_msg` 的话，不 @ 的群
  消息根本不会被推给 bot，这一行就不会出现。

```bash
# 旁证：sidecar 入站进程在跑
ps -ef | grep -E "feishu_channel/sidecar\.js run" | grep -v grep
# 旁证：health 的 inbound 行从「none observed yet」翻成「last event …」
claudeteam health | grep -i inbound
# 机判：sidecar 把这条【不 @】消息转成 NDJSON，且 mentioned_bot:false（自建应用才有）
#   单跑一份 sidecar，把 stdout 落文件，再发上面那句 $ANCHOR，然后核对：
#   node scripts/feishu_channel/sidecar.js run > /tmp/ct_sidecar.ndjson 2>/dev/null &
grep -F "$ANCHOR" /tmp/ct_sidecar.ndjson | grep -F '"mentioned_bot":false'
# 期望命中一行：不 @ 的消息确实被推给了 bot（im:message.group_msg 生效的铁证）
```

## 期望（一句话）

验权通过后：`state/feishu_app.json`（0600）+ `claudeteam.toml` 的 `chat_id` 都写好、
ClaudeTeam 群里有你、`claudeteam up` 全员报到、群里发一句**不 @** 的话能在 60 秒内
拿到带锚定的回复。

## 失败排查

- **浏览器没弹 / 自动化中途回退到手动**——默认流要 bot-creator 的 Playwright + Chromium，
  且要桌面浏览器。先在 `scripts/feishu_bot_creator/` 跑过 `npm install`（会下载 Chromium）；
  无桌面的服务器跑不了默认流，改 §2 的 `--manual` 或在桌面机生成凭证后拷盘。回退到
  `--manual` 不算失败——它是设计内的健壮兜底（控制台改版时）。
- **命令报「权限未到位（缺 im:message.group_msg）」**——只会出现在 `--manual` 回退路径：
  deep-link 没确认 / 没发版 / 没批准；按 §2 的 `--manual` 说明补齐再回车（或重跑 connect）。
- **`state/feishu_app.json` 不是 0600**——connect 写盘权限有问题，重跑 connect；出站
  发卡 / sidecar 都靠 `feishu/lark.py:subprocess_env()` 从这个文件注入
  `FEISHU_APP_ID/SECRET` + tenant token。
- **群没建出来**——看命令尾部是不是「建群失败」；确认 app 有 `im:chat` 权限（默认流的
  import-scopes / `--manual` 的 deep-link 都已含）。
- **报到卡没出现**——`claudeteam health` 看是不是某个 agent 没起；CLI 没登录见
  [host_smoke.md](host_smoke.md) §1 的排查。
- **§5 不 @ manager 不回**——先确认 sidecar 进程在跑（上面那条 `ps`）；再确认 app
  **真有** `im:message.group_msg`（个人版 / `--quick` 拿不到，群里就必须 @bot）；再看
  `state/router.log` 是不是 ROUTE 到 manager；inbound 行没翻说明入站没进来，查 sidecar stderr。

## 不在范围

- 斜杠命令矩阵 / 反向路由 / catchup / lazy：跑通本篇后看 [host_smoke.md](host_smoke.md)。
- Docker 部署（凭证走 `.env` 覆盖、chat_id 手填、入站同样是 sidecar）：见
  [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md) 的 Docker 段。
- `--quick` 个人版扫码流（DM/@bot-only）：本篇只覆盖默认的自建应用流。
- 用户 OAuth（`--as user` 模拟自己发消息）：见 [host_smoke.md](host_smoke.md) §2。
