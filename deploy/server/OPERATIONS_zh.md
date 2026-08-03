# AI Company 服务器从 0 到 1 部署与运维手册

这份文档面向新的 Linux 服务器，目标是把 AI Company 从零部署起来，并说明后续如何：

- 查看 `manager / developer / deployer / ops` 的状态
- 查看某个 agent 的执行详情
- 查看最近任务、日志、监控、构建产物
- 排查飞书没响应、任务卡住、构建产物不可下载等问题

本文默认：

- 服务器系统：Ubuntu 22.04
- 仓库目录：`/root/AI--compony`
- Git 远端：`origin`
- 使用 Docker 部署 AI Company
- Android 构建产物目录：`/srv/ai-company-artifacts`

---

# 1. 新服务器初始化

## 1.1 安装基础依赖

```bash
apt update
apt install -y git docker.io docker-compose-plugin nginx apache2-utils curl
systemctl enable --now docker
```

作用：

- `git`：拉取和更新仓库
- `docker.io` / `docker-compose-plugin`：运行 AI Company 容器
- `nginx`：对外提供 APK / manifest 下载链接
- `apache2-utils`：如果需要可给下载目录加 Basic Auth
- `curl`：做健康检查、接口排查
- `systemctl enable --now docker`：开机自动启动 Docker，并立即启动

如果服务器启用了 UFW，开放基础端口：

```bash
ufw allow 22/tcp
ufw allow 8000/tcp
ufw allow 8081/tcp
ufw allow 8765/tcp
ufw status
```

作用：

- `22`：SSH 登录
- `8000`：业务服务健康检查/接口
- `8081`：APK/产物下载
- `8765`：只读监控页面

如果使用阿里云安全组，也要同步放行这些端口。

---

# 2. 拉取 AI Company 仓库

```bash
cd /root
git clone git@github.com:lmz-123/AI--compony.git
cd /root/AI--compony
```

作用：

- 把 AI Company 代码放到固定目录，后续所有命令都基于这里执行

如果服务器没有 GitHub SSH 权限，也可以先用 HTTPS 拉，再后续补 SSH。

---

# 3. 初始化目录与配置

## 3.1 创建目录

```bash
cd /root/AI--compony

mkdir -p team-data/state
mkdir -p projects
mkdir -p server-secrets/codex
mkdir -p server-secrets/ssh
mkdir -p /srv/ai-company-artifacts

cp -R templates/ai-company/. team-data/
cp deploy/server/examples/codex-config.toml server-secrets/codex/config.toml
cp deploy/server/examples/deploy-targets.toml team-data/deploy-targets.toml
cp deploy/server/examples/ops-targets.toml team-data/ops-targets.toml
cp deploy/server/examples/build-targets.toml team-data/build-targets.toml
cp deploy/server/examples/ssh_config server-secrets/ssh/config

chmod 700 server-secrets/ssh server-secrets/codex
chmod 600 server-secrets/codex/config.toml server-secrets/ssh/config
chmod 755 /srv/ai-company-artifacts
```

作用：

- `team-data/`：团队配置、状态、记忆、任务数据
- `projects/`：本地工作区，给 agent 操作项目代码
- `server-secrets/`：Codex 与 SSH 敏感配置
- `/srv/ai-company-artifacts`：正式 APK/manifest 下载目录

---

## 3.2 写 AI Key 与 GitHub Token

创建：

```bash
nano /root/AI--compony/team-data/state/.env
```

填入：

```text
OPENAI_API_KEY=你的中转站Key
GITHUB_TOKEN=你的GitHub细粒度Token
```

然后：

```bash
chmod 600 /root/AI--compony/team-data/state/.env
```

作用：

- `OPENAI_API_KEY`：给容器内各 agent 调用模型
- `GITHUB_TOKEN`：给 Android 构建脚本触发 GitHub Actions、下载 Artifact

`GITHUB_TOKEN` 推荐权限：

- Repository: `lmz-123/MyAPPs`
- Contents: Read
- Actions: Read and write

---

## 3.3 放入飞书应用信息

把桌面机已有的：

- `state/feishu_app.json`

复制到：

```bash
/root/AI--compony/team-data/state/feishu_app.json
```

然后：

```bash
chmod 600 /root/AI--compony/team-data/state/feishu_app.json
```

作用：

- 让容器直接复用飞书机器人身份，不需要在服务器内重新 connect

---

## 3.4 配置团队主文件

编辑：

```bash
nano /root/AI--compony/team-data/claudeteam.toml
```

至少确认这些内容：

- `chat_id` 已填正确
- `manager / developer / deployer / ops` 都存在
- 模型配置符合当前策略
- `[startup] roll_call = false`

作用：

- 决定团队人数、模型、提示词、是否启动点名

---

# 4. 配置 Android 构建与公开下载

编辑：

```bash
nano /root/AI--compony/team-data/build-targets.toml
```

推荐至少确认：

```toml
version = 1
artifact_dir = "/data/artifacts"
artifact_public_base_url = "http://你的服务器公网IP:8081/artifacts"
token_secret = "GITHUB_TOKEN"

[[targets]]
name = "tripcanvas-android"
owner = "lmz-123"
repository = "MyAPPs"
workflow = "android-build.yml"
default_ref = "main"
allowed_refs = ["main"]
default_api_base_url = "https://你的后端公网地址"
allow_release = false
```

作用：

- `artifact_dir = "/data/artifacts"`：容器内产物目录
- `artifact_public_base_url`：构建成功后回传的直链前缀
- `default_api_base_url`：写进 APK 的后端接口地址，必须是手机能访问的公网地址

---

# 5. 配置 Nginx 下载目录

创建配置：

```bash
nano /etc/nginx/conf.d/ai-company-artifacts.conf
```

写入：

```nginx
server {
    listen 8081;
    server_name _;

    location /artifacts/ {
        alias /srv/ai-company-artifacts/;
        autoindex on;
        autoindex_exact_size off;
        autoindex_localtime on;
        types {
            application/vnd.android.package-archive apk;
            application/octet-stream aab;
            application/json json;
        }
        add_header Cache-Control "no-store";
    }
}
```

然后：

```bash
nginx -t
systemctl enable --now nginx
systemctl reload nginx
```

作用：

- `8081` 对外暴露产物下载
- `alias /srv/ai-company-artifacts/` 必须和真实目录一致
- `autoindex on` 方便直接看目录与文件名

如果之前目录权限出过问题，可统一修正：

```bash
chmod 755 /srv
chmod 755 /srv/ai-company-artifacts
find /srv/ai-company-artifacts -type d -exec chmod 755 {} \;
find /srv/ai-company-artifacts -type f -exec chmod 644 {} \;
chown -R www-data:www-data /srv/ai-company-artifacts
```

作用：

- 确保 Nginx 的运行用户 `www-data` 有读权限，避免 `403 Forbidden`

---

# 6. 配置部署 SSH

编辑：

```bash
nano /root/AI--compony/server-secrets/ssh/config
```

然后生成/补齐 known_hosts：

```bash
ssh-keyscan -H github.com >> /root/AI--compony/server-secrets/ssh/known_hosts
chmod 600 /root/AI--compony/server-secrets/ssh/known_hosts
```

如果部署员工要 SSH 到其他生产机，也要继续追加：

```bash
ssh-keyscan -H -p 22 your-server.example.com >> /root/AI--compony/server-secrets/ssh/known_hosts
```

作用：

- 让 deployer 在容器内 push GitHub 或 SSH 到目标机时，能够严格校验 host key

---

# 7. 启动 AI Company

```bash
cd /root/AI--compony
docker compose -f deploy/server/compose.yaml up -d --build
```

作用：

- 构建镜像
- 启动 `manager / developer / deployer / ops` 所在容器

当前正式设计中：

- 容器内 `/data/artifacts`
- 宿主机 `/srv/ai-company-artifacts`

已经通过 `compose.yaml` 直接挂载，后续不需要再手动同步 APK。

---

# 8. 首次验收命令

```bash
cd /root/AI--compony

docker compose -f deploy/server/compose.yaml ps
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam health
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam team
docker compose -f deploy/server/compose.yaml logs --tail=200 claudeteam
curl -fsS http://127.0.0.1:8765/api/monitor
curl -I http://127.0.0.1:8081/artifacts/
```

作用：

- `ps`：看容器是否正常运行
- `health`：看配置、tmux、router、watchdog 是否健康
- `team`：看各 agent 当前状态
- `logs`：看容器启动日志
- `8765`：看监控接口是否起来
- `8081`：看下载目录是否对外服务

---

# 9. 更新代码与重启

以后更新 AI Company：

```bash
cd /root/AI--compony
git pull --ff-only origin main
docker compose -f deploy/server/compose.yaml up -d --build --force-recreate
```

作用：

- 拉最新代码
- 重建容器并用新代码启动

如果模板文件有更新，需要覆盖团队副本：

```bash
cp templates/ai-company/manager.md team-data/manager.md
cp templates/ai-company/developer.md team-data/developer.md
cp templates/ai-company/deployer.md team-data/deployer.md
cp templates/ai-company/ops.md team-data/ops.md
```

作用：

- 把仓库里的最新提示词同步到运行中的团队配置

---

# 10. 查看不同 agent 的状态

## 10.1 总览状态

```bash
cd /root/AI--compony
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam team
```

作用：

- 查看 `manager / developer / deployer / ops` 当前状态
- 常见状态如：
  - `ready`
  - `initializing`
  - `进行中`
  - 具体任务标题

---

## 10.2 健康状态

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam health
```

作用：

- 看：
  - `chat_id` 是否存在
  - `codex` 是否可用
  - tmux pane 是否 ready
  - router / watchdog 是否活着
  - 最近是否有 inbound event

---

## 10.3 只看监控 JSON

```bash
curl -fsS http://127.0.0.1:8765/api/monitor
```

作用：

- 机器可读的团队状态快照
- 可用于你自己的面板或简单排查

---

# 11. 查看某个 agent 的执行详情

## 11.1 看 agent 收件箱

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam inbox manager
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam inbox developer
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam inbox deployer
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam inbox ops
```

作用：

- 看是否有未读消息
- 判断为什么某个 agent 没继续工作

---

## 11.2 查看最近输出

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek manager 80
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek developer 80
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek deployer 80
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek ops 80
```

作用：

- 看指定 agent 最近若干行执行内容
- 最适合排查“它现在到底卡在哪”

---

## 11.3 直接看 tmux pane 原始内容

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam tmux capture-pane -pt AI-Company:manager | tail -n 120
docker compose -f deploy/server/compose.yaml exec -T claudeteam tmux capture-pane -pt AI-Company:developer | tail -n 120
docker compose -f deploy/server/compose.yaml exec -T claudeteam tmux capture-pane -pt AI-Company:deployer | tail -n 120
docker compose -f deploy/server/compose.yaml exec -T claudeteam tmux capture-pane -pt AI-Company:ops | tail -n 120
```

作用：

- 看 pane 里最真实的 CLI 输出
- 适合排查模型报错、提示词注入、503、401、403、任务卡住等问题

---

# 12. 查看任务状态与流转

## 12.1 查看任务清单

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam task list
```

作用：

- 看当前任务列表
- 识别哪些任务：
  - `待处理`
  - `进行中`
  - `已完成`
  - `阻塞`

---

## 12.2 看某个任务详情

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam task show T-38
```

作用：

- 看任务描述、指派人、验收标准、创建时间、当前状态

---

## 12.3 查看任务日志

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam status-log --tail 50
```

作用：

- 看最近的状态变更记录
- 适合排查“为什么任务没自动推进”

如果你的版本没有这个子命令，就退回用：

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek manager 120
```

---

# 13. 常见“飞书发了但没响应”排查命令

```bash
cd /root/AI--compony

docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam health
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam team
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam inbox manager
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek manager 80
docker compose -f deploy/server/compose.yaml exec -T claudeteam tail -n 100 /data/state/router.log
docker compose -f deploy/server/compose.yaml logs --tail=200 claudeteam
```

作用：

- `health`：确认 router/watchdog 是否在
- `team`：确认 manager 是否还是 ready / processing
- `inbox manager`：看 manager 是否有 unread
- `peek manager`：看它是否真的消费了消息
- `router.log`：看飞书事件有没有进来
- `logs`：看容器内是否有异常

---

# 14. 常见“deployer 卡住/没继续”排查命令

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam team
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam inbox deployer
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam peek deployer 120
docker compose -f deploy/server/compose.yaml exec -T claudeteam tmux capture-pane -pt AI-Company:deployer | tail -n 200
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam task list
```

作用：

- 看 deployer 当前是不是还卡着旧任务
- 看它有没有收到 manager 的 follow-up
- 看它是不是卡在 push、SSH、GitHub、部署脚本或模型 503 上

---

# 15. 常见“APK 是否已经构建出来”查询命令

## 15.1 看服务器产物目录

```bash
find /srv/ai-company-artifacts -maxdepth 3 -type f | sort
find /srv/ai-company-artifacts -name '*.apk' | sort
```

作用：

- 快速确认 APK / manifest 是否已经落盘

## 15.1.1 推荐的后台构建方式

如果你不希望 deployer 在 pane 里一直等待长时间的 APK 构建或部署脚本，可以直接把真实命令包进后台执行器：

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam \
  python /app/scripts/deploy/run_async_and_notify.py \
  --agent deployer \
  --notify manager \
  --task-id T-XX \
  --label "TripCanvas debug APK 构建" \
  -- \
  python /app/skills/build-tripcanvas-android/scripts/build_android.py \
  --target tripcanvas-android \
  --ref main \
  --build-type debug \
  --format apk
```

作用：

- 立即返回 `job_id`
- 真实构建脚本在后台继续跑
- 结果写入 `/data/state/async-jobs/`
- 脚本结束后自动把摘要发给 `manager`

部署脚本也可以同样包一层：

```bash
docker compose -f deploy/server/compose.yaml exec -T claudeteam \
  python /app/scripts/deploy/run_async_and_notify.py \
  --agent deployer \
  --notify manager \
  --task-id T-XX \
  --label "TripCanvas publish+deploy" \
  -- \
  python /app/scripts/deploy/publish_and_deploy.py \
  --target local-production \
  --project tripcanvas-backend \
  --message "your commit message"
```

---

## 15.2 验证下载目录是否可访问

```bash
curl -I http://127.0.0.1:8081/artifacts/
curl -I "http://127.0.0.1:8081/artifacts/<request_id>/<apk-file>"
curl "http://127.0.0.1:8081/artifacts/<request_id>/build-manifest.json"
```

作用：

- 确认 Nginx 直链是否通
- 区分：
  - `401`：还开着 Basic Auth
  - `403`：目录权限不够
  - `404`：alias 路径错了，或文件没在那个目录
  - `200`：可以直接交付链接

---

# 16. 常见错误与含义

## 16.1 `401 Unauthorized`

常见含义：

- 下载目录还开着 Basic Auth
- 飞书机器人权限不足时，也可能在其他接口出现鉴权失败

---

## 16.2 `403 Forbidden`

常见含义：

- Nginx 能找到目录，但没有读权限
- `/srv/ai-company-artifacts` 权限不足

常用修复：

```bash
chmod 755 /srv
chmod 755 /srv/ai-company-artifacts
find /srv/ai-company-artifacts -type d -exec chmod 755 {} \;
find /srv/ai-company-artifacts -type f -exec chmod 644 {} \;
chown -R www-data:www-data /srv/ai-company-artifacts
systemctl reload nginx
```

---

## 16.3 `404 Not Found`

常见含义：

- Nginx alias 路径写错
- 访问的 request_id / 文件名不对
- 文件没复制/没产出

检查：

```bash
cat /etc/nginx/conf.d/ai-company-artifacts.conf
find /srv/ai-company-artifacts -maxdepth 3 -type f | sort
```

---

## 16.4 `unsupported_country_region_territory`

常见含义：

- 你当前走的是不适用地区的登录链路
- 这也是为什么服务器方案里推荐完全依赖 `.env` 注入 Key，而不是在服务器上手工 `codex login`

---

## 16.5 `INVALID_API_KEY`

常见含义：

- `OPENAI_API_KEY` 配错
- 中转站 key 无效
- 容器没重启，仍在使用旧 key

检查：

```bash
cat /root/AI--compony/team-data/state/.env
docker compose -f deploy/server/compose.yaml up -d --build --force-recreate
docker compose -f deploy/server/compose.yaml exec -T claudeteam claudeteam health
```

---

# 17. 常用维护命令速查

## 17.1 更新 AI Company

```bash
cd /root/AI--compony
git pull --ff-only origin main
docker compose -f deploy/server/compose.yaml up -d --build --force-recreate
```

## 17.2 看容器状态

```bash
docker compose -f deploy/server/compose.yaml ps
```

## 17.3 看容器日志

```bash
docker compose -f deploy/server/compose.yaml logs --tail=200 claudeteam
```

## 17.4 重启容器

```bash
docker compose -f deploy/server/compose.yaml restart
```

## 17.5 停止容器

```bash
docker compose -f deploy/server/compose.yaml down
```

## 17.6 重启 nginx

```bash
systemctl restart nginx
systemctl status nginx --no-pager
```

## 17.7 看 nginx 错误日志

```bash
tail -n 50 /var/log/nginx/error.log
```

## 17.8 看 8081 / 8765 监听情况

```bash
ss -ltnp | grep 8081
ss -ltnp | grep 8765
```

---

# 18. 推荐交付方式

对于 Android 包，推荐最终交付方式是：

1. deployer 完成构建
2. 构建脚本产出：
   - `file`
   - `download_url`
   - `sha256`
3. deployer 把：
   - 下载链接
   - 文件名
   - SHA256
   - workflow URL

   交给 manager
4. manager 再把最终信息回给老板

这样可以：

- 避开飞书大文件限制
- 避开飞书 `im:resource` 权限依赖
- 让手机下载更直接

---

# 19. 文档使用建议

如果未来再换一台新服务器，推荐按这个顺序执行：

1. 安装依赖
2. 拉仓库
3. 初始化目录与 secrets
4. 配飞书 `chat_id`
5. 配 `.env`
6. 配 SSH
7. 配 `build-targets.toml`
8. 配 Nginx 下载目录
9. 启动容器
10. 用 `health / team / monitor / curl 8081` 做首轮验收

这套流程走通后，后续基本就是：

- `git pull`
- `docker compose up -d --build --force-recreate`
- `claudeteam health`
- `claudeteam team`

就可以维护了。
