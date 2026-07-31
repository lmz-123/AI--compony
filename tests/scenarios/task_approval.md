# Task intent + approval-suspend gate

## 场景

boss 一次性甩下一个大需求，manager 把它拆成若干子任务。两个大模型天生缺的
能力要靠机制补上：

1. **忠实记住原话** —— 子任务执行到后段会「意图漂移」，agent 读不懂 boss
   本意。把 boss 逐字原话存成**不可变 intent 记录**（`I-n`），子任务用
   `intent_id` 回链；任何时候都能从 store **现读**原话，不受 pane 上下文
   漂移 / `/compact` 改写污染。
2. **该停就停等拍板** —— 某一步需要 boss 定夺时，任务进入 `需审批` 硬挂起
   态，agent **不得自行往下做**，必须等 `approve` / `reject`。这道闸门不能
   被 `task update` 绕过。

## 范围

- 类型：local store（纯文件存储 + CLI；不碰 tmux / 飞书）
- 凭证：无
- 操作员：manager（建 intent / 拆任务）、worker（pause）、boss（approve/reject）

## Given

- `claudeteam` 可用，`CLAUDETEAM_STATE_DIR` 指向工作区
- 状态机：`待处理 → 进行中 → {已完成 | 需审批 | 已取消}`；
  `需审批 → {进行中 | 已完成 | 已取消}`，且只能经 approve/reject 离开

## When

```bash
# 1. 落 boss 原话（逐字、不可变），拿到 I-1
claudeteam task intent create "把支付页改成两步结账，第一步选地址第二步付款" --src msg_42

# 2. manager 拆出子任务并回链原话
claudeteam task create dev "重构结账流程" --by manager --intent I-1

# 3. agent 开工
claudeteam task update T-1 --status 进行中

# 4. 遇到要 boss 拍板的点 —— 挂起，附上待决问题
claudeteam task pause T-1 --note "两步还是三步结账？" --by dev

# 5. boss 现读原话确认本意，未漂移
claudeteam task intent get I-1

# 6a. boss 批准并直接收口
claudeteam task approve T-1 --done
#  —— 或 ——
# 6b. boss 打回返工（带反馈）
claudeteam task reject T-1 "就两步，别加第三步"
#  —— 或 ——
# 6c. boss 取消
claudeteam task reject T-1 "需求作废" --cancel
```

## Then

stdout 关键行：

```
✅ intent I-1
✅ created T-1: 重构结账流程 → dev
✅ updated T-1
⏸️  T-1 需审批 — awaiting user
I-1  by user
  raw: 把支付页改成两步结账，第一步选地址第二步付款
✅ approved T-1 → 已完成
```

副作用：

- `task pause` 往 **boss inbox** 投一条 `task_id=T-1`、priority 高的审批请求。
- `task approve` / `reject` 往 **assignee（dev）inbox** 投一条 `task_id=T-1`
  的裁决回执。
- 每次流转写一条 `local_facts.append_log(kind="task_transition", ref=T-1)`
  审计行，谁 / 何时 / X→Y 全程可回放。
- `task intent get I-1` 任何时候返回的 `raw` 与第 1 步**逐字一致**（不可变）。

错误路径 / 闸门：

| 输入 | exit | stderr / 行为 |
| --- | --- | --- |
| `task pause T-1`（T-1 不是「进行中」）| 1 | `❌ cannot pause T-1 (missing or not 进行中)` |
| `task approve T-1`（T-1 不是「需审批」）| 1 | `❌ cannot approve T-1 (not 需审批)` |
| `task update T-1 --status 已完成`（T-1 在「需审批」）| 1 | `❌ 需审批 transitions must use task pause/approve/reject` —— **闸门：状态不变** |
| `task update T-1 --status 需审批`（想绕过 pause 强行挂起）| 1 | 同上，拒绝 |
| `task intent create "   "` | 1 | `❌ intent raw_text cannot be empty` |
| `task intent get I-99` | 1 | `❌ no such intent: I-99` |

## Why this is here

`proposal-tasks-feature.md` §4「最终设计」+ §5「可靠代码结构设计」落地。架构只
补两样大模型天生缺的东西：**原话持久化**（intent 不可变 + `intent_id` 回链 +
注入现读）与**审批挂起态状态机**（`需审批` 硬闸门 + approve/reject 守卫 +
append_log 记账）。「一次拆几个 / 谁并行谁串行 / 何时该挂起」是**主管运行时
判断，不入架构** —— 这里只验证机制原语的可靠性。

## Out of scope

- **滴灌调度 / 自动拆子任务 / 焦点指针推进**：主管运行时行为，不在本机制内。
- **何时该挂起的判断**：由 agent / 主管运行时决定；架构只保证一旦 `pause`
  就锁住、且只能经 approve/reject 离开。
- **把原话写进 agent 原生 CLAUDE.md 锚的注入接线**：消费侧（deliver / manager
  运行时）的事；store 只提供 `get_intent` 供现读。
