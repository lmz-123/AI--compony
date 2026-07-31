# AI Company 服务器部署

这个部署在 Docker 内运行 `manager`、`developer`、`deployer` 三个 Codex agent。宿主机不需要安装 Codex；`deploy/server/Dockerfile` 只安装 Codex CLI、飞书 sidecar、tmux 和 SSH 客户端，不构建当前团队用不到的其它 agent CLI。运行时通过 `/data/state/.env` 中的 API Key 调用配置好的远程模型网关。

## 目录初始化

在仓库根目录执行：

```bash
mkdir -p team-data/state projects server-secrets/codex server-secrets/ssh
cp -R templates/ai-company/. team-data/
cp deploy/server/examples/codex-config.toml server-secrets/codex/config.toml
cp deploy/server/examples/deploy-targets.toml team-data/deploy-targets.toml
cp deploy/server/examples/ssh_config server-secrets/ssh/config
chmod 700 server-secrets/ssh server-secrets/codex
chmod 600 server-secrets/codex/config.toml server-secrets/ssh/config
```

编辑 `team-data/claudeteam.toml`，填入已经绑定的飞书群 `chat_id`。把桌面机生成的 `state/feishu_app.json` 安全传到 `team-data/state/feishu_app.json`，权限设为 `0600`。

## Codex API Key

创建 `team-data/state/.env`：

```text
OPENAI_API_KEY=你的中转站Key
```

然后执行：

```bash
chmod 600 team-data/state/.env team-data/state/feishu_app.json
```

不要把这个文件、`team-data/` 或 `server-secrets/` 加入 Git。Codex 自定义 provider 位于 `server-secrets/codex/config.toml`；默认示例使用 `https://xiaoxin8.com` 和 Responses API。

容器启动时会从该 `.env` 自动生成 Codex 所需的临时 `/root/.codex/auth.json`，再把它共享给三个隔离的 agent HOME。无需在服务器交互执行 `codex login`；更换 Key 后重启容器即可。

## 部署 SSH

为部署员工创建权限受限的专用 SSH Key。私钥保存为 `server-secrets/ssh/deployer_ed25519`，公钥安装到目标服务器的 `deploy` 用户。不要复用 root 私钥。

编辑 `server-secrets/ssh/config` 后，用真实主机指纹生成 `known_hosts`：

```bash
ssh-keyscan -H -p 22 your-server.example.com > server-secrets/ssh/known_hosts
chmod 600 server-secrets/ssh/deployer_ed25519 server-secrets/ssh/known_hosts
```

编辑 `team-data/deploy-targets.toml`，只登记允许操作的服务器、仓库、目录和命令。目标服务器本身应配置 GitHub Deploy Key，部署容器不需要持有各项目的 GitHub 私钥。

## 启动与验证

迁移前先在旧机器运行 `claudeteam down`，同一个飞书 App 不允许两套 router 并行订阅。

```bash
docker compose -f deploy/server/compose.yaml up -d --build
docker compose -f deploy/server/compose.yaml exec claudeteam claudeteam health
docker compose -f deploy/server/compose.yaml exec claudeteam claudeteam team
docker compose -f deploy/server/compose.yaml logs --tail=100 claudeteam
```

随后在飞书群发送 `/team`，再发一条普通任务，确认主管、开发员工和部署员工均完成点名。容器设置了 `restart: unless-stopped`，服务器重启后会自动恢复团队。

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
