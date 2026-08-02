---
name: tripcanvas-dev
description: TripCanvas/iTraval 代码库路径、知识库索引与增量文档规则。L1 及以上开发任务、跨模块变更或首次接触该仓库时读取；不要预读全库 document。
---

# TripCanvas 开发约定

## 仓库与知识库入口

优先以实际工作区路径为准；常见布局：

- 代码与知识库：`iTraval/tripcanvas/` 或 `MyAPPs/`（`tripcanvas-backend/`、`tripcanvas-frontend/`）
- 产品需求：`iTraval/需求文档.md`
- 工程总索引：`tripcanvas/document/document.md` 或 `MyAPPs/docs/document.md`
- 交付边界：`tripcanvas/document/implementation-gap.md`（若存在）

**不读、不写** `iTraval/openspec/`——OpenSpec 已停用；行为与接口以现有 `document/` 知识库和代码为准。

## 读文档顺序（按需，不要全读）

1. 主管派单里的 `paths` / `domains` / `modules`
2. 项目级 `document.md` 索引 → 只打开相关域或前端模块索引
3. 与本次改动直接相关的 1–3 个文件（见下方增量规则）

## document/ 文件类型（仅按需更新）

| 文件 | 何时才改 |
|------|----------|
| `api-index.md` + `api-design/*.md` | 新增/变更 REST 接口 |
| `database.md` | 表、字段、索引、迁移 |
| `conventions.md` | 响应格式、错误码、前后端字段映射 |
| `architecture.md` | 模块职责、分层、依赖、核心流程变化 |
| `tech-notes.md` | 重试/降级、实现决策、已知限制 |
| `document.md` | 新增/删除子文档时更新索引 |

纯内部重构、不改对外行为 → **不动 document**。

## 域与模块速查

| 语义 | 后端域 | 前端模块 |
|------|--------|----------|
| 行程 CRUD | `plan` | plan_list、plan_create、plan_editor |
| LLM / arq | `planner` | plan_editor |
| 可视化 / 分享图 | `viz` | plan_editor、share_view |
| 分享只读 | `share` | share_view |
| 地图推荐 | `recommendation` | plan_create、plan_editor |
| 旅行搭子 | `buddy` | buddy |
| 协作 | `collaboration` | plan_editor |

跨模块接口或状态机变更时，同步**直接相关**的域/模块文档与项目级 `api-index.md`；Worker 任务变更须涉及 `planner` 与 `plan` 的说明。

## 编码分层

- 后端：`router.py` → `schemas.py` → `service.py` → `models.py`；`planner` 另有 `tasks.py`
- 前端：`screens/` → `providers/` → `services/` → `models/`、`widgets/`
- 数据库变更：Alembic 迁移 + 对应域 `database.md`（若 L1+ 要求写文档）

## 测试

按改动风险最小充分：`pytest`（后端）、`flutter analyze`（前端）；不为低风险改动跑完整无关测试集。
