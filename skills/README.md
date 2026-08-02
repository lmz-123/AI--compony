# skills — 可复用过程能力索引

一个 skill 一个目录，主文件固定叫 `SKILL.md`（frontmatter + 何时用/输入/步骤/产物 四段）。
身份只教"何时用哪个"，过程细节在触发时现读对应 SKILL.md。新增 skill = 加目录 + 本索引加一行。

| skill | 一句话 | 适用角色 |
|---|---|---|
| [verify-status](verify-status/SKILL.md) | 确认员工在不在线、在不在岗：拉整段 pane 实录 LLM 通读判断；部署后必跑（一票否决，失败提示登录），平时怀疑掉线单点复用 | 部署 agent / manager |
| [reflect](reflect/SKILL.md) | 反思并收拾团队共享经验库：通读 → 合并重复 → 退役过时 → 把全队通用的提为置顶 | manager / 任意 agent 周期性跑 |
| [debug-tripcanvas](debug-tripcanvas/SKILL.md) | 从相关日志开始，按证据只读查询 TripCanvas 的运行状态、存储或可选 SLS | ops / manager |
| [build-tripcanvas-android](build-tripcanvas-android/SKILL.md) | 通过白名单 GitHub Actions 构建、校验、下载并发送 TripCanvas Android APK/AAB | deployer / manager |
| [tripcanvas-dev](tripcanvas-dev/SKILL.md) | TripCanvas 仓库路径、知识库索引与增量 document 规则；L1+ 开发任务按需读取 | developer |

> 已有方案待落地（见 expert-skills-proposal.md，等老板拍板）：patrol（主管巡视）。
