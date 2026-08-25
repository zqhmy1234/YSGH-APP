# Wave 2 · Agent E（B3 端侧+UI 域）任务卡——docs/parallel-dev/05

> ✅ 已完成并集成（2026-08-26，merge ae801a7）

## Mission
完成 B3 端侧/UI 7 项：30min 保守模式开关、聚合预处理去重、照片→事件反向入口（API+UI）、L2 待确认区 UI、30s 验收计时埋点、事件卡片封面展示、端云参数单一来源优化（AGG 双跑维护）。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B3_events.md`——#10 30min 开关仅注释、#11 预处理去重（客户端感知哈希）、#12 照片→事件反向入口缺失、#7 待确认区、#14 30s 计时、端云双实现"单一来源"实为双份硬编码。
3. 现状：`client/utils/agg/`（agg_config.uts 参数源 + st_dbscan.uts + pipeline.uts）、`client/utils/agg_runner.uts`、`event_sync.ts`、`event_ops.ts`、`client/pages/index/index.uvue`（**你独占**）、`client/pages/debug/agg-check/agg-check.uvue`。
4. 真机验收链路：nova 11；AGG-016 双跑 fixtures（scripts/gen_agg_fixtures.py 生成）。

## Scope（可改）
1. `client/utils/agg/`（agg_config.uts/st_dbscan.uts/pipeline.uts/fixtures.uts）
2. `client/utils/agg_runner.uts`、`client/utils/event_ops.ts`、`client/utils/event_sync.ts`
3. `client/pages/index/index.uvue`（独占）、`client/pages/debug/agg-check/agg-check.uvue`
4. **新文件** `backend/app/api/event_items.py`（照片→事件反向查询 API，新建零冲突）
5. `backend/tests/test_event_items.py`（新建）
6. `scripts/gen_agg_fixtures.py`（双跑维护）

## 绝不碰（只读）
`backend/app/services/events.py`、`backend/app/api/events.py`（Agent D 独占；反向入口用新文件 event_items.py，内部可 import events 服务函数只读）；`backend/app/services/pipeline.py`；models.py/migrations/；client/pages/ 除 index/agg-check 外；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **30min 保守模式开关**：agg_config.uts 加正式配置项（conservative_mode bool），端侧 stDbscan 参数化；云侧对应 pipeline.py 由 Agent D 处理——你在任务卡备注与 D 对齐（AGG 双跑测试兜底）。
2. **预处理去重**：客户端感知哈希（photo hash）在 preprocess 阶段去重（重复跳过，与后端 uq_contents_user_hash 对齐）。
3. **照片→事件反向入口**：新 API `GET /api/v1/contents/{id}/events`（event_items 服务查询，返回事件列表+标题+跳转）→ 照片详情 UI（index.uvue 内照片点开显示"属于：事件列表"）。
4. **L2 待确认区 UI**：draft 事件（confidence<0.7）在时间轴独立分组"待确认"，提供确认/忽略操作（调 confirm API）。
5. **30s 验收计时埋点**：授权→首批日卡片渲染时间统计（性能日志 + agg-check 页展示）。
6. **封面展示**：事件卡片用 cover_content_id 显示封面（无封面回退首图）。
7. **AGG 双跑维护**：gen_agg_fixtures.py 增加新场景（30min 开关/去重），保持端云参数一致。

## Dependencies
- Agent D 的 events API 契约（confirm 已存在；draft 状态已存在）
- 真机 nova 11（UI 项验证）；模拟器可先冒烟

## DoD
1. UTS 编译通过（HBuilderX CLI，读 skills/hbuilderx-uniappx-runloop/SKILL.md）；agg-check 双跑 10/10 保持。
2. 新 API 测试通过（不依赖 Qdrant）。
3. 更新 .cowork-temp/audit_B3_events.md 状态列。
4. 完成消息：文件清单 + 测试 + 与 D 的参数对齐说明 + 真机验证状态。

## Integration
分支 `wave2-agentE`；与 D/F 并行；index.uvue 归你独占——**Wave 3 Agent H 的同步状态 UI 不得直接改 index.uvue**（H 建新组件，集成 Agent 接线一行）；merge 后全量测试 + OpenAPI 重导出（新 API）。
