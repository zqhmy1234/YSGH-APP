# Wave 3 · Agent H（B4 客户端域）任务卡——docs/parallel-dev/08

## Mission
完成 B4 客户端 6 项：/sync 客户端接线（SQLite 队列六字段+op_id+增量拉取+reconcile 消费）、WiFi/蜂窝流量约束、2h 定时兜底、同步状态 UI、批量失败暂停恢复、照片上传指数退避。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B4_sync.md`——#3 客户端 /sync 零调用（后端 LWW/游标/软删已就绪）、#4 无流量约束、#5 无 2h 定时、#6 状态 UI 仅进度浮层、#7 无批量暂停、照片上传 MAX_RETRY=2 立即重试。
3. 现状：`client/utils/uploader.ts`（分片上传+断点续传）、`client/utils/event_ops.ts`（enqueueOp 仅三字段，非 SQLite）、`event_sync.ts`（事件上云指数退避）；后端 API：POST /sync/push、GET /sync/pull、POST /sync/reconcile（全部就绪未消费）。
4. XView/SQLCipher：自定义基座能力（progress.md:14 注记）；当前用 uni storage 行分隔 JSON 兜底。

## Scope（可改）
1. **新文件** `client/utils/sync_client.ts`（/sync 三接口客户端：操作日志队列 + op_id 幂等 + 增量拉取 + reconcile 消费）
2. `client/utils/uploader.ts`（WiFi 判断、退避重试、批量暂停）
3. `client/utils/event_ops.ts`（队列字段补齐 status/retry_count/created_at）
4. **新组件** `client/components/UploadStatusBanner.uvue`（待上传/失败标红/暂停横幅/继续上传）
5. `client/pages/messages/messages.uvue` 或 profile 页（同步状态入口，可选）

## 绝不碰（只读）
`client/pages/index/index.uvue`（Wave 2 Agent E 独占——你的状态 UI 用新建组件，**由集成 Agent 在 index.uvue 接线一行**，你不改该文件）；`client/utils/agg*`、`client/pages/debug/*`（Agent E）；backend/ 全部（后端已就绪，只消费 API）；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **sync_client.ts 接线**：本地操作日志队列（op_id/type/payload/status/created_at/retry_count，uni storage 或 XView 自定义基座可用时 SQLite）→ push 幂等；增量拉取 pull（游标持久化）；对账 reconcile 消费（差异提示）。
2. **流量约束**：uploader.ts 加 uni.getNetworkType 判断——WiFi 传原图、蜂窝只传缩略图+元数据（调用 Agent G 的 upload_mode 参数）、"立即上传原图"手动入口。
3. **2h 定时兜底**：App 运行时 setInterval 定时同步（2h）；后台定时（WorkManager）登记给 Agent K（Wave 4）。
4. **批量失败暂停恢复**：连续失败 ≥10 条 → 暂停剩余（保留队列）→ 顶部横幅"网络异常，已暂停同步" → 一键"继续上传"。
5. **退避重试**：照片上传改指数退避（2s→4s→8s→×5，复用 event_sync 模式）；4xx 停该条。
6. **同步状态 UI**：UploadStatusBanner 组件（待上传图标/失败标红可点击重试/暂停横幅/继续按钮）。

## Dependencies
- Agent G 的 API（/sync 已存在；upload_mode 参数 Wave 3 G 加）
- 自定义基座（XView/SQLCipher）未就绪时用 uni storage 兜底，代码做抽象
- 真机 nova 11（流量约束/后台行为验证）

## DoD
0. **门禁（2026-08-26 快/全量拆分新规）**：commit 时 pre-commit 自动跑快速门禁（秒级）；**完成声明前必须跑 `python scripts/review_agent.py --full` 全绿**（仓库级 + 全量测试，集成 Agent 与 CI 同口径验收）。
1. HBuilderX 编译通过；模拟器冒烟（同步链路、暂停恢复）。
2. 更新 .cowork-temp/audit_B4_sync.md 状态列。
3. 完成消息：文件清单 + 测试 + 需集成 Agent 接线项（index.uvue 一行）+ 真机验证状态。

## Integration
分支 `wave3-agentH`；与 G/I 并行；index.uvue 由集成 Agent 接线（你只建组件）；merge 后全量测试 + 契约同步。
