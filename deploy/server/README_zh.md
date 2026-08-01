# AI Company 服务器部署

这个部署在 Docker 内运行 `manager`、`developer`、`deployer`、`ops` 四个 Codex agent。四个角色统一使用 `gpt-5.6-terra`；开发使用 `high` 推理，主管使用 `medium`，部署和运维使用 `low`。四个 agent 随团队一起启动，避免首次派单等待。员工只在完成或阻塞时向主管汇报，主管不转述过程消息，简单任务只向群里回复最终结果。`ops` 从相关日志开始，只有证据指向某个组件时才继续查询运行状态、PostgreSQL、Redis 或 SLS。宿主机不需要安装 Codex；镜像内已包含 Codex CLI、飞书 sidecar、tmux、SSH 客户端和 TripCanvas 排障/Android 构建 skill。

## 目录初始化

在仓库根目录执行：

```bash
mkdir -p team-data/state team-data/artifacts projects server-secrets/codex server-secrets/ssh
cp -R templates/ai-company/. team-data/
cp deploy/server/examples/codex-config.toml server-secrets/codex/config.toml
cp deploy/server/examples/deploy-targets.toml team-data/deploy-targets.toml
cp deploy/server/examples/ops-targets.toml team-data/ops-targets.toml
cp deploy/server/examples/build-targets.toml team-data/build-targets.toml
cp deploy/server/examples/ssh_config server-secrets/ssh/config
chmod 700 server-secrets/ssh server-secrets/codex
chmod 600 server-secrets/codex/config.toml server-secrets/ssh/config
```

编辑 `team-data/claudeteam.toml`，填入已经绑定的飞书群 `chat_id`。把桌面机生成的 `state/feishu_app.json` 安全传到 `team-data/state/feishu_app.json`，权限设为 `0600`。

## Codex API Key

创建 `team-data/state/.env`：

```text
OPENAI_API_KEY=你的中转站Key
GITHUB_TOKEN=仅授权lmz-123/MyAPPs的细粒度Token
```

然后执行：

```bash
chmod 600 team-data/state/.env team-data/state/feishu_app.json
```

`GITHUB_TOKEN` 需要对 `lmz-123/MyAPPs` 具有 Contents 只读和 Actions 读写权限，用于触发 workflow 和下载产物。不要把这个文件、`team-data/` 或 `server-secrets/` 加入 Git。Codex 自定义 provider 位于 `server-secrets/codex/config.toml`；默认示例使用 `https://xiaoxin8.com` 和 Responses API。

容器启动时会从该 `.env` 自动生成 Codex 所需的临时 `/root/.codex/auth.json`，再把它共享给四个隔离的 agent HOME。无需在服务器交互执行 `codex login`；更换 Key 后重启容器即可。

## 部署 SSH

为部署员工创建权限受限的专用 SSH Key。私钥保存为 `server-secrets/ssh/deployer_ed25519`，公钥安装到目标服务器的 `deploy` 用户。不要复用 root 私钥。

编辑 `server-secrets/ssh/config` 后，用真实主机指纹生成 `known_hosts`：

```bash
ssh-keyscan -H -p 22 your-server.example.com > server-secrets/ssh/known_hosts
chmod 600 server-secrets/ssh/deployer_ed25519 server-secrets/ssh/known_hosts
```

编辑 `team-data/deploy-targets.toml`，只登记允许操作的服务器、仓库、目录和命令。目标服务器本身应配置 GitHub Deploy Key，部署容器不需要持有各项目的 GitHub 私钥。

## 智能运维排障

`team-data/ops-targets.toml` 是运维员工唯一可信的诊断目标清单。仓库示例已经登记当前 TripCanvas 生产拓扑：

- SSH 别名：`local-production`
- 项目目录：`/srv/apps/MyAPPs`
- Compose：`tripcanvas-backend/docker-compose.yml`
- 服务：`api`、`worker`、`db`、`redis`
- 健康检查：`http://127.0.0.1:8000/health`

确认这些值与服务器实际情况一致。`ops` 通过已有的 `/root/.ssh/config` 和只读挂载的密钥登录目标；对应 SSH 用户至少需要读取项目、执行该 Compose 项目状态和日志查询，以及在需要时执行数据库/Redis 只读探测的权限。不要给它数据库写权限，也不要把 Docker Socket 挂进 AI Company 容器。

阿里云 SLS 默认关闭：

```toml
[sls]
enabled = false
profile = "tripcanvas-sls"
project = ""
logstore = ""
```

只有确实使用 SLS 时才改为 `true` 并填写资源名。AK/SK 只能通过服务器上的合规 Aliyun CLI profile 提供，禁止写入 Git、`ops-targets.toml`、群消息或日志。当前镜像不默认安装 Aliyun CLI；未启用 SLS 不影响 Docker、PostgreSQL 和 Redis 排障。

## Android APK 构建与飞书交付

Android 构建在 GitHub Actions 执行，服务器不安装 Flutter、JDK 或 Android SDK。编辑 `team-data/build-targets.toml`，填写 Android 手机实际可以访问的后端地址：

```toml
default_api_base_url = "https://你的TripCanvas接口域名"
```

不能填写 `localhost` 或 `127.0.0.1`。默认只允许 `main` 和 debug APK；不要在尚未配置正式签名时打开 `allow_release`。

在 `lmz-123/MyAPPs` 的 GitHub 仓库 Settings -> Secrets and variables -> Actions 中新增 `AMAP_ANDROID_KEY`。debug 和 release 都需要这个高德 Android Key；不要把它写进服务器 `.env`、`build-targets.toml`、代码仓库或飞书消息。

飞书机器人还需要 `im:resource` 权限。进入当前机器人的飞书开放平台控制台，添加“获取与上传图片或文件资源”权限，创建新版本并发布/批准。旧版本不重新发布，文件上传不会生效。

需要检查配置时可运行 dry-run；常规 debug 构建无需先执行这一步：

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam \
  python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk \
  --dry-run
```

真实构建、校验并发送当前飞书群：

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam \
  python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk \
  --send-to-feishu
```

产物和 manifest 保存在 `team-data/artifacts/<request_id>/`。GitHub Artifact 保留 7 天；确认交付后可按 request 目录清理服务器副本，不要删除整个 `team-data`。

正式 release 还需要在 `lmz-123/MyAPPs` 的 GitHub Actions secrets 配置 `ANDROID_KEYSTORE_BASE64`、`ANDROID_KEY_ALIAS`、`ANDROID_KEY_PASSWORD`、`ANDROID_STORE_PASSWORD`，完成一次独立签名验证后再把 `allow_release` 改为 `true`。

## 启动与验证

迁移前先在旧机器运行 `claudeteam down`，同一个飞书 App 不允许两套 router 并行订阅。

```bash
docker compose -f deploy/server/compose.yaml up -d --build
docker compose -f deploy/server/compose.yaml exec claudeteam claudeteam health
docker compose -f deploy/server/compose.yaml exec claudeteam claudeteam team
docker compose -f deploy/server/compose.yaml logs --tail=100 claudeteam
```

随后在飞书群发送 `/team`，再发一条普通任务，确认主管、开发员工、部署员工和智能运维员工均完成点名。容器设置了 `restart: unless-stopped`，服务器重启后会自动恢复团队。

## 已部署三人团队升级为四人团队

`team-data/` 是持久化目录，`git pull` 不会自动覆盖其中真实的 `chat_id` 和 playbook。更新代码后，在 `/root/AI--compony` 执行：

```bash
git pull --ff-only origin main

cp templates/ai-company/manager.md team-data/manager.md
cp templates/ai-company/developer.md team-data/developer.md
cp templates/ai-company/deployer.md team-data/deployer.md
cp templates/ai-company/ops.md team-data/ops.md
cp deploy/server/examples/ops-targets.toml team-data/ops-targets.toml
cp -n deploy/server/examples/build-targets.toml team-data/build-targets.toml
mkdir -p team-data/artifacts
```

如果服务器仓库使用其他远程名，先用 `git remote -v` 确认后替换 `origin`。然后编辑 `team-data/claudeteam.toml`，保留原有真实 `chat_id`，在 `[team.agents.deployer]` 后加入：

```toml
[team.agents.ops]
cli        = "codex-cli"
model      = "gpt-5.6-terra"
reasoning_effort = "low"
role       = "智能运维员工：日志优先的只读排障、运行检查与例行构建"
specialty  = ["故障诊断", "日志分析", "按需存储排查", "Docker Compose", "阿里云 SLS", "Android debug 构建"]
playbook   = "ops.md"
card_color = "orange"
```

重新构建并强制重建容器，让镜像包含新 skill：

```bash
docker compose -f deploy/server/compose.yaml up -d --build --force-recreate
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam health
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam team
```

在飞书群发送 `/team`，确认出现 `manager`、`developer`、`deployer`、`ops`。再发送：

```text
请让 ops 查看 TripCanvas api 最近 15 分钟错误日志；只有日志指向具体组件时才继续做对应只读检查，不做全量巡检或任何修改。
```

## 推荐任务格式

```text
请分析需求并安排开发：<需求>。
项目路径：/workspace/projects/<project>。
验收标准：<标准>。
开发和测试完成后先汇报，不要部署。
```

```text
请把 <project> 的 <commit> 部署到 deploy-targets.toml 中的 <target>。
执行部署和健康检查，失败时按清单回滚并汇报证据。
```

```text
TripCanvas 出现 <症状>，发生时间约 <时间和时区>，关联 request_id/trace_id 是 <ID，如有>。
请主管先安排 ops 只读排障；若确认是代码问题，再安排 developer 修复和测试。未经我本次明确确认，不要部署。
```

```text
请打包 TripCanvas main 分支的 Android 测试版 APK。
使用 build-targets.toml 中的 API 地址，测试和构建成功后校验 SHA256，并把 APK 发送到当前飞书群。不要构建正式版，不要发布应用商店。
```
