# Wave 3 · Agent G（B4 后端域）任务卡——docs/parallel-dev/07

## Mission
完成 B4 后端 5 项：缩略图管线（生成/存储/下发）、流量约束后端支撑、微信图云端原件（媒体下载→COS）、软删除 30 天物理清理 job、COS 开通验证支撑。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B4_sync.md`——#1 缩略图 thumbnail_key 无写入方、#2 30 天清理无消费方、#8 微信图仅记 media_id、#10 COS 未开通（生产 fake/fs）、#11 客户端真分片后置。
3. 现状：存储抽象 fake/fs/minio/cos（storage.py）；分片上传状态机已完成（upload.py）；`upload_tasks` 表已补迁移（Wave 0）；wechat service.py:57-67 仅记 media_id；deleted_logs.cleanup_status 默认 pending 无消费。

## Scope（可改）
1. `backend/app/services/external/storage.py`、`backend/app/services/upload.py`、`backend/app/services/sync.py`、`sync_common.py`、`reconcile.py`
2. `backend/app/services/wechat/service.py`（媒体下载→COS，仅此文件）
3. `backend/app/api/upload.py`、`backend/app/api/sync.py`、`backend/app/api/wechat.py`
4. **新文件** `backend/app/services/thumbnails.py`（缩略图管线）、`backend/app/workers/cleanup_job.py`（30 天清理）、`backend/scripts/backfill_thumbnails.py`
5. `backend/tests/test_upload.py`、`test_sync.py`、`test_reconcile.py`、新建 `test_thumbnails.py`、`test_cleanup_job.py`

## 绝不碰（只读）
pipeline.py（缩略图生成如需接管线 → 登记需求或经 pipeline_ext/payload.py 由 Agent A 处理）；models.py/migrations/（thumbnail_key 列已存在；清理 job 只需消费 cleanup_status）；client/（客户端由 Agent H 管）；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **缩略图管线**：thumbnails.py——COS 图片处理（或本地 PIL）生成缩略图 → 写 thumbnail_key → GET 缩略图端点（供 Windows/列表加载，默认拉缩略图原图按需）；入库管线接线登记（photos 上传完成时生成）。
2. **流量约束后端支撑**：上传 API 接受 `upload_mode`（original/thumbnail_meta）参数 + WiFi 标记；为客户端 Agent H 提供"手动立即上传原图"端点（复用 upload complete）。
3. **微信图云端原件**：service.py 收到 image/voice → 下载 media 到 COS（cos_key 落库）→ 敏感排除（图片 CI 审核）→ 入管线（与 photo 同链路）。
4. **30 天物理清理 job**：cleanup_job.py 消费 deleted_logs.cleanup_status（≥30 天 → 物理删 COS 对象 + 清墓碑）；挂 RQ 定时（登记调度需求）。
5. **COS 验证支撑**：smoke 脚本（smoke_cos.py）更新 + 文档说明开通步骤（密钥/STS 角色）；生产默认 fake 保持不变直到 key 到位。

## Dependencies
- COS 密钥/STS（代码先行；未开通用 fs/fake 后端测试）
- 微信 media 下载需 WECHAT 凭证（代码先行，mock 沙箱测）

## DoD
1. 新增测试全过（thumbnails/cleanup/wechat media 用 fake 后端）。
2. 更新 .cowork-temp/audit_B4_sync.md 状态列。
3. 完成消息：文件清单 + 测试 + 表需求（如有）+ 待 key 项。

## Integration
分支 `wave3-agentG`；与 H/I 并行（G 后端、H 客户端、I 画像，文件域零重叠）；merge 后全量测试 + OpenAPI 重导出（新端点）。
