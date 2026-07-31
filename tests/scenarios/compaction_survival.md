# 抗压缩存活 — tasks 原话意图 /compact 后逐字幸存（容器真 agent · Layer B）

## 目的

证老板的核心担忧可验证：**一个长程任务，上下文涨大被 `/compact` 压缩后，真
Claude Code agent 是否还能完好回到之前状态、逐字记得原始意图。**

机制：tasks 特性 #3 把当前 active intent 的**逐字原话**锚进 agent 原生
`~/.claude/CLAUDE.md`（每轮原生重读、不进会话 transcript，所以 `/compact` 压不
掉），并在每次 task 流转时刷新这份常驻文件；agent 也可随时
`claudeteam task intent get I-n` 从不可变 store 现读。压缩后这两条是唯一幸存的
耐久通道——本剧本就验它们真能让模型逐字复原。

通过即证明：压缩/清空会话后，(A) 常驻文件仍带逐字原话、(B) 真 agent 受命能一字
不差回吐、(C) 它续推的是对的那条 active 任务（不认已完成的）。

> 进程内自动化版（守 CI 底线、证基质）在
> [`tests/integration/test_compaction_survival.py`](../integration/test_compaction_survival.py)
> 已绿；本剧本是它**碰不到的那一层**——真模型 + 真 `/compact`。

## 适用范围

- 跑前提：[host_smoke.md](host_smoke.md) §3-§7 已过（基础设施不通跑这个没意义）。
- 分支：跑在 `feat/tasks-intent-approval`（**on-disk 锚点刷新接线必须生效**，否则
  常驻文件会停在旧值、"新鲜性"判据无从谈起）。
- team：含真 claude-code worker（下记 `worker_cc`）。
- 凭证：用户 OAuth 已就绪（`lark-cli auth list` 有效）。
- 时长：约 10-20 分钟（含一轮 `/compact` + 一轮 `/clear`）。

## 前置条件

```bash
cd /path/to/ClaudeTeam
source .venv/bin/activate
export CLAUDETEAM_STATE_DIR="$PWD/state"
export LARK_CLI_NO_PROXY=1
export CLAUDETEAM_LARK_SEND_AS=bot

claudeteam health            # 应全绿
claudeteam team              # worker_cc 在线

# 固定金丝雀：唯一 nonce + 一条最易在总结里被丢掉的硬约束。
# 全部断言都 byte-exact 命中这两段 —— 非黑即白、可复跑、无主观判断。
NONCE='[ANCHOR-7F3A2C9E]'
CONSTRAINT='绝不加第三步'
RAW="把支付页改成两步结账：第一步选地址、第二步付款，${CONSTRAINT}。${NONCE}"
WORKER=worker_cc
PANE="ClaudeTeam:${WORKER}"
CLAUDE_MD="${CLAUDETEAM_STATE_DIR:-$HOME/.claudeteam}/agents/${WORKER}/home/.claude/CLAUDE.md"   # CLAUDETEAM_AGENT_HOME_ROOT 覆盖时见 agent_home()
```

## 操作

### 步骤 1 — 构造一个 active intent-task

```bash
claudeteam task intent create "$RAW"               # → I-1
claudeteam task create "$WORKER" "重构结账流程" --intent I-1   # → T-1
claudeteam task update T-1 --status 进行中          # 置 active（锚点只锚 进行中/需审批）
```

（也可走真实「群里发 → manager 派 worker」，只要最终 T-1 是 active 且回链 I-1。）

### 步骤 2 — 做大上下文 + 触发压缩

```bash
# 让 worker 真实推进几轮，把上下文做大（任意能产生多轮对话的活都行）。
# 随后向 worker pane 注入真实的 /compact —— 走自动压缩同一代码路径、确定性。
tmux send-keys -t "$PANE" '/compact' Enter
sleep 20    # 等压缩完成、新一轮提示符就绪
```

> 加跑一遍更狠的上界：`/clear`（清空整段会话，比 /compact 更彻底）。若
> `/clear` 后仍逐字复原，则必然扛得住 /compact。两轮都要绿。

### 步骤 3 — 压缩后三路独立客观判据（全部 byte-exact 命中 `$NONCE`）

```bash
# 判据 A — 承重墙在位（读盘，零 agent 主观）：常驻文件逐字带原话
grep -F "$RAW" "$CLAUDE_MD" && echo "A_PASS" || echo "A_FAIL"
grep -F "$CONSTRAINT" "$CLAUDE_MD"     # 那条最易丢的硬约束也必须在

# 判据 B — agent 确实重新摄入（逐字回吐）：命它现读并原样输出
claudeteam send "$WORKER" qa \
  "用 claudeteam task intent get I-1 现读，把 raw_text 一字不差原样输出到群里，不要改写/总结/翻译"
sleep 20
claudeteam peek "$WORKER" 60 | grep -F "$NONCE" && echo "B_PASS" || echo "B_FAIL"

# 判据 C — 回到正确 task 态（store 审计）：续推的是 T-1，不是别的/已完成的
claudeteam task get T-1                 # 仍 进行中、intent=I-1
claudeteam send "$WORKER" qa "继续推进你手上的 active 任务，并报一句你在做哪个 T-n"
sleep 20
claudeteam peek "$WORKER" 60 | grep -F "T-1" && echo "C_PASS" || echo "C_FAIL"
```

### 步骤 4 — 负向对照（证判据"会失败"、有判别力，不是白过）

```bash
# 对照①：关锚点必失败 —— 令 T-1 完成使锚点消失，重跑判据 B 必须 FAIL
claudeteam task done T-1
grep -F "$NONCE" "$CLAUDE_MD" && echo "NEG1_FAIL(锚点没消失!)" || echo "NEG1_PASS(原话已消失)"

# 对照②：done-drop 新鲜性 —— 上面已验常驻文件里 nonce 消失；
# 但 store 仍留历史可现读（不可变）
claudeteam task intent get I-1 | grep -F "$NONCE" && echo "STORE_KEEPS_HISTORY_OK"
```

## 通过条件

| # | 判据 | 绿 |
|---|---|---|
| A | `grep -F "$RAW" $CLAUDE_MD` | 命中（含 `$NONCE` + `$CONSTRAINT`） |
| B | peek 抓 worker 回吐 | 逐字命中 `$NONCE` |
| C | worker 续推 + `task get T-1` | 报 T-1、状态正确、不碰已完成任务 |
| 负① | done 后读盘 | `$NONCE` **已从** CLAUDE.md 消失 |
| 负② | done 后 `intent get I-1` | store 仍可现读历史 |

**全绿判定**：A/B/C 全部 byte-exact 命中 **且** 负①如期"消失"、负②历史仍在；
**且** `/compact` 与 `/clear` 两轮都满足。任一逐字命中缺失、或负向对照"没失败"
（关锚点后 nonce 还在）→ **红**，连同 pane 回复、`$CLAUDE_MD` 原文、`tasks.json`
一并留证。

## 失败排查

| 现象 | 可能问题 | 怎么查 |
|---|---|---|
| 判据 A FAIL（文件里没 nonce）| 常驻文件没被刷新到 active 原话 | `cat $CLAUDE_MD` 看有没有「老板原话锚点」段；确认跑在含 on-disk 刷新接线的分支；`claudeteam task get T-1` 是不是真 active |
| A 绿但 B FAIL | 模型读了文件但没逐字回吐（改写/总结了）| 看 `claudeteam peek $WORKER 80` 全文——是漂移还是根本没回应；漂移属 prompt 问题，不是 store bug |
| B 绿但 C FAIL（报错任务号）| 多任务时 agent 认错 active 任务 | `claudeteam task list --assignee $WORKER --status 进行中` 看 active 集合；锚点是否同时锚了多条 |
| 负① 没失败（done 后 nonce 还在）| **刷新接线漏了 done/update 路径** —— 真 bug | 回查 `commands/task.py` 的 `_refresh_anchor` 是否挂在 update/done 后；这正是 Layer A `test_completed_task_drops_verbatim_from_durable_file` 守的回归 |
| `/compact` 后 pane 卡住 / 没新提示符 | 压缩耗时或模型限速 | `claudeteam usage` 看限速；`claudeteam peek $WORKER 30` 看状态 |

## 已知风险

1. **`/compact` 时机**：真自动压缩何时触发取决于上下文大小；本剧本用**显式注入
   `/compact`** 规避"非得烧到百万 token"，走的是同一压缩代码路径，确定性更高。
2. **agent 自由文本非确定性**：判据 B 的回吐可能夹带寒暄。只断言**逐字包含**
   `$NONCE`（`grep -F`），不要求整条输出 == RAW，避免被无关字符搞红。
3. **凭证过期**：长跑期间 claude 凭证可能过期（pane 显 "Not logged in"）。临时解
   `claudeteam down && claudeteam up` 重新物化凭证，再从步骤 1 起（换个新 nonce）。
4. **分支前提**：跑在没有 on-disk 刷新接线的旧分支上，负①会假性失败——务必确认
   分支。

## 不在范围

- 进程内基质验证（锚点逐字渲染 / 刷新及时 / store 不可变）：已由
  `tests/integration/test_compaction_survival.py` 覆盖，不在本剧本重复。
- 自动拆子任务 / 滴灌调度 / 多任务并发调度：主管运行时行为，与本机制无关。

## 记录（qa 跑的时候填，实测证据回填这里当样例）

```
- 跑的分支 / commit: …
- NONCE: [ANCHOR-7F3A2C9E]
- /compact 轮：A=…  B=…  C=…  负①=…  负②=…
- /clear  轮：A=…  B=…  C=…  负①=…  负②=…
- 判据 A 证据（$CLAUDE_MD 里锚点段原文片段）: …
- 判据 B 证据（peek 抓到的 worker 逐字回吐行）: …
- 判据 C 证据（task get T-1 + 续推回报）: …
- 负① 证据（done 后 grep nonce 的输出）: …
- 通过 / 失败: …
- 备注（限速 / 凭证 / 漂移等）: …
```
