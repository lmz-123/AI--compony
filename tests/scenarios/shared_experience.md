# 团队共享经验库（shared experience）

验证「全队共读共写的经验库」这条链路：任何 agent 写入的团队级教训，落到
`state/share/experience.jsonl`；**置顶（`--pin`）**的会在每个 agent 下次唤醒时
常驻注入上下文，其余按需 `recall --team --grep` 现拉（just-in-time，避免把
常驻 system prompt 撑爆）。

不覆盖：per-agent 私有记忆（看 `claudeteam recall <agent>`，那是各自的
`agents/<name>/memory.jsonl`）、私有工作区（`agents/<name>/workspace/`）。

## 范围

- 类型：local-only（不依赖 tmux / 飞书 / 真模型）
- 凭证：无
- 操作员：boss / manager / 任意 worker

## Given

- ClaudeTeam 已 `pip install -e .`（`claudeteam` 在 PATH）。
- `CLAUDETEAM_STATE_DIR` 指向一个可写目录（决定 `state/share/` 落点）。

## When / Then

### 1. 写入团队经验（任意 agent 都能写）

```bash
claudeteam remember worker_cc learning "某次性细节，知道就行" --team
# → 🤝 team experience: E-1 [learning] by worker_cc

# 全队都该常驻的关键事实加 --pin（只有置顶的才注入身份）：
claudeteam remember worker_cc learning "本仓库测试用 python3 tests/run.py" --team --pin
# → 🤝 team experience: E-2 [learning] by worker_cc 📌置顶
```

- **Then**：`state/share/experience.jsonl` 出现对应行，`by` 记贡献者，`pin` 记置顶态。
- **Then**：它**没有**写进 `worker_cc` 的私有记忆——`claudeteam recall worker_cc`
  看不到（私有 / 共享两个池子分开）。

### 2. 全队可读 + 按需检索（不需要 agent 名）

```bash
claudeteam recall --team
#     [<ts>]    E-1  [learning] 某次性细节...        (@worker_cc)
#     [<ts>] 📌 E-2  [learning] 本仓库测试用 ...     (@worker_cc)   ← 📌 = 置顶

# 穷人版检索：只拉相关的（按内容/kind/by 子串过滤，零依赖、无 embedding）
claudeteam recall --team --grep 测试
```

- **Then**：列出团队经验，置顶条带 `📌`，每条带稳定 id（`E-n`，用于改 / 删 / 置顶）。
- `--grep` 只返回匹配子串的条目；`--json` 输出原始记录便于 jq / CI。

### 3. 更新 / 退役 / 置顶（活的知识库，不是只堆）

```bash
# 精炼：在原条目上改得更准，而不是新增一条矛盾的（可带 --pin / --unpin 改置顶态）
claudeteam remember manager learning "本仓库测试用 python3 tests/run.py（3.10+）" --team --update E-2
# → ✏️  team experience updated: E-2 [learning] by manager 📌

# 退役：某条已经错 / 过时，单条精确删（不需要 --yes）
claudeteam forget --team --id E-1
# → 🗑  team experience: retired E-1
```

- **Then**（update）：`E-2` 内容被**原地替换**，多出 `updated_by` / `updated_at`；总条目数不变。
- **Then**（retire）：`E-1` 从经验库消失，`recall --team` 不再列它。
- 整库清空（慎用）才需显式 `claudeteam forget --team --yes`。

### 4. 新 agent 唤醒时只注入「置顶核心」+ 现拉指针

```bash
claudeteam reidentify worker_codex      # 或首次 hire / 唤醒
# 检查注入到该 agent 的身份 / 原生记忆文本
```

- **Then**：worker_codex 的唤醒提示 / 原生记忆文件出现 `## 团队共享经验（置顶 · 全队常驻）`
  段，**只含置顶的那条**（E-2），写入者是别人也照样注入——"经验不按人重复踩坑"的闭环。
- **Then**：未置顶的不进身份，只留一行 `另有 N 条…recall --team` 指针——常驻上下文保持精简
  （守住 CLAUDE.md ~200 行红线）；需要时各 agent 自己 `recall --team --grep` 拉。

### 5. 定期反思收拾（reflect skill）

经验攒多会重复 / 过时。按 [`skills/reflect`](../../skills/reflect/SKILL.md) 跑一轮：
`recall --team` 通读 → `--update` 合并重复 → `forget --team --id` 退役过时 →
`--pin` 把全队通用的提为置顶。收拾完 `recall --team` 应当少而准、置顶清晰。

### 6. 私有工作区存在且独立

```bash
ls -d "${CLAUDETEAM_STATE_DIR}/agents/worker_cc/workspace"
```

- **Then**：每个被 provision 的 agent 都有自己的 `workspace/`；身份文件里告诉它
  "长报告 / 草稿写这里，别堆共享仓库根"。

## 清理

```bash
claudeteam recall --team --json    # 留档
# 经验库随 state 目录走；要清空：claudeteam forget --team --yes（或删 state/share/experience.jsonl）
```
