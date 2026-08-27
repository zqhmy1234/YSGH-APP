## 2026-08-27 · 重构批次 G1（认证安全，基准 develop @ 5fcbd29，分支 techdebt/g1）

- **① refresh single-flight（R6#6，client/utils/auth.ts）**：模块级 `_refreshInflight` 共享 in-flight——并发 401 只触发一次 `/auth/refresh`，其余 await 同一 Promise，落定后清除（成败均清）；消除并发双轮换竞态（后端轮换本就是原子 single-use，双轮换必一 401）
- **② logout/revoke 端点（R6#7，AUTH-006）**：新增 `POST /api/v1/auth/logout`（请求 `{refresh_token}`，幂等：token 无效/过期仍 200）→ 服务层 `logout()` 把 devices 行 `refresh_token_hash/refresh_token` 置 NULL（吊销后 refresh() 校验落 401 已吊销）；客户端 `auth.ts` 新增 `logout()` 调端点 + 清本地凭据（真实消费方：scripts/test_auth_singleflight.mjs 断言调用 + 路由登记）
- **③ refresh_token HMAC 哈希列（R6#8）**：TD-P3 已加列核实接线后补强——`_hash_refresh_token` 由裸 SHA-256 升级为 **HMAC-SHA256 + 独立密钥 `refresh_token_hmac_key`（与 jwt_secret 隔离）+ `hmac$` 版本前缀**；新增 `_verify_refresh_token_hash`（现行 HMAC 校验 + 存量无前缀 SHA-256 迁移期兼容）；轮换 WHERE 用 OR(hmac, legacy) 匹配，原子 single-use 语义不削弱；生产强制非默认密钥（_apply_production_safety 新门禁 + 单测）
- **④ SMS 验证码加盐（R6#9）**：`sms_codes.salt` 列（新迁移 f1a2b3c4d5e6 + schema.sql），send 侧 `sha256(salt:code)` 落库（随机盐 `secrets.token_hex(8)`），校验按行盐重算（存量无盐行走兼容分支）；原子消费（R2#8 条件 UPDATE）保留
- **⑤ 通用限流中间件（R6#2/#3，backend/app/core/ratelimit.py 新增）**：Redis 固定窗口（INCR+EXPIRE，`yishu:rl:{scope}:ip|user:{key}`）按 client_ip/user 双维度，先覆盖 auth / ASR(含 guard) / 搜索三域；白名单（`rate_limit_whitelist`，trust_proxy 时读 X-Forwarded-For）；Redis 故障自动降级进程内 MemoryStore（**降级不 500**）；中间件置于 RequestID 内侧（main.py 最外层=RequestID→CORS→…→RateLimit）——**429 响应同样带 X-Request-ID，不破坏 request_id 链路**；429 信封 `{code: RATE_LIMITED, message, request_id, details:{scope, dimension}}`
- **契约快照（只增不减）**：openapi 45→46 路径（新增 /api/v1/auth/logout；旧路径零消失）；errors.py ERROR_REGISTRY 零改动零消失；feature_list.json 零改动
- **测试**：auth 域精准 **85+ passed**（test_auth_g1 新增 10 单测：HMAC 格式/密钥隔离/存量兼容/加盐；test_ratelimit 新增 9 单测：429/白名单/降级不500/非三域放行/disabled 短路/user 维度/request_id 保留；test_auth_db 新增 logout×2 + 加盐落库断言，适配 HMAC；test_security_p3/test_config_alias 适配）；**refresh single-flight 并发单测**：scripts/test_auth_singleflight.mjs（node --test，真实导入 client/utils/auth.ts，Promise.all 5 并发 → 1 次 refresh + logout 消费方断言，4/4 绿）
- **验证**：快速门禁 EXIT=0；本地 alembic upgrade head=f1a2b3c4d5e6 应用
- **⚠️ 集成登记**：main.py 含 G2 在途重构（create_app/安全响应头/healthz 收敛）+ G1 限流中间件接线（1 import + 中间件注册，RequestID 保持最外层）；main.py 属 G2 文件域，由集成 Agent 统一合并/提交，勿重复覆盖
- 提交：见 techdebt/g1 分支（本地未 push；报告文件留集成 Agent 统一提交）

## 2026-08-27 07:20 · 重构批次 F 第一波集成（6 Agent）+ 遗留 bug 修复

- **merge（4 个 --no-ff）**：F-Auth（d47684c）/F-Rag（284fd2b）/F-Asr（1a60fe2）/F-ClientA（0f64c73）/F-ClientB（5 commits）/F-Content（9f0b2f4）全部合入 develop；共享工作区导致的链式 SHA（auth→clientb#1→rag→asr/content/clientb#2-5）git 自动去重，index.uvue（clienta+clientb 双收口）ort 策略无冲突自动合并
- **代码质量审核**：10 个 commit 逐条文件域复核零跨域；契约快照（openapi 45 路径/78 schemas）零 diff；tsc EXIT=0；快速门禁 EXIT=0；受影响域精准 227 passed
- **O6 落实确认**（用户点名）：F-ClientB 4ca9fbf 完整落地——queue_store.ts 单 key（yishu_offline_queue）承载六字段契约，sync_client（批推）与 event_ops（confirm/merge/split 顺序）共享存储、路由差异保留在各消费方 flush；旧双 key 一次性迁移（仅读+删，升级不丢操作）；event_ops flush 统一走 retry.ts retryAsync 退避
- **遗留 bug 修复**：`enqueue_idempotent`（R4#4）与 `enqueue_unique`（F4）的 job_id 拼接含非法字符（冒号/空格/中文）会在 RQ 2.x validate_job_id 抛 ValueError（真实 client_request_id 入队即炸）——新增 `_safe_job_id_part` 净化器统一处理 + 2 个回归测试（test_photo_content）
- 契约快照 diff：docs/openapi.json、core/errors.py ERROR_REGISTRY 零消失
- **CI 确认**：develop @ a64f60a → Actions run #41（33022074746）conclusion=success（F 批次第一波全绿；CDE 04:40 推送 #40 亦在其上顺延绿）
- 下一步：F 批次波次 2 = **F-Events**（F3 聚合独立 per-user 任务 + F5 events.py 拆包），前置 F-Content 已合入，可开；提示词见 docs/重构批次F提示词_20260827.md F-Events 节

## 2026-08-27 08:30 · 重构批次 F-Events（F3 聚合独立 per-user 任务 + F5 events.py 拆包）

- **F5/R1#5 events.py 拆包**：`services/events.py` → `services/events/` 子包（aggregate.py 聚合 / sync.py 事件上云与拉取 / timeline.py 时间轴 / edit.py merge·split·confirm·set_cover 手动操作）；`__init__.py` 重导出原公开函数（外部 import 不变，含测试引用的私有函数）；聚合细节收敛在 aggregate.py 窄端口不外泄到 pipeline；纯搬移零行为改动（`git grep "import app.services.events"` 旧模块路径调用点=0）
- **F3/R5-3 聚合独立 per-user 任务**：process_content 不再同步跑聚合——主提交后经 `core/queue.enqueue_unique` 按 user 级 key（`user:<uid>`）SETNX 去重合并入队 `run_user_aggregation`（自开 Session 独立执行、失败静默返回 error dict、幂等可重投）；同用户并发多内容只跑一次聚合（聚合任务扫描该用户全部未成候选内容，一次覆盖并发批次）；low 队列 DEFAULT_JOB_TIMEOUT；workers/worker.py 登记
- **测试**：新增 `tests/test_aggregation.py`（F3 聚合专属：任务单测 full 落 L1 / 失败静默 / per-user 去重并发：同用户只入队一次、不同用户各自入队、user key 净化 / _write_upper_candidates 幂等：候选已落库即跳过重写）+ `tests/test_events.py`（包级/入队契约：process_content 恰一次 enqueue_unique 按 user 级 key + 参数透传、入队失败不否定主转写、RQ 模块路径可解析 + worker 登记）；test_pipeline 事件用例更新为入队契约断言
- **验证**：受影响域精准 144 passed（event_ops/event_sync/event_items/event_sensitive/events/pipeline/contents/upload/content_upload/photo_content/queue/requeue_job）+ 快速门禁 EXIT=0 + app.main import 无环 + 契约快照（docs/openapi.json / core/errors.py / feature_list.json）零 diff
- **环境**：Docker Desktop 起 yishu-redis/yishu-qdrant 容器（此前引擎未启动 → Redis ConnectionError/Qdrant 版本告警为环境问题，非代码回归）
- 提交：c0b74d1（F5 拆包）+ 28ba960（F3），本地 develop 未 push；报告文件留集成 Agent 统一提交

## 2026-08-27 06:50 · 重构批次 F-Content（F1 照片双轨收口 + F4 process_content job 级去重）

- **F1/P0-6 照片注册双轨收口**：抽 `services/photo_content.py` 唯一注册编排，参数化 `dedup_key`（perceptual_hash 409 / cos_key 幂等）/ `moderate` / `mode`（original/thumbnail_meta/update）；`api/contents.py::upload_photo` 与 `services/upload.py::register_photo_content` 只做协议适配，两套幂等键都保留；`_reflow_violation`/`_require_photo_bytes` 下沉 photo_content 单源
- **F4/R5-4#5 job 级去重**：`core/queue.py` 新增 `enqueue_unique(func, key)`（job_id 下划线拼接 + Redis SETNX 原子预占位；同键不重复入队、既有 job failed 重建；queue_name/job_timeout 可覆盖），收敛 contents/upload/wechat/pipeline 的 process_content/thumbnail/emotion 入队点
- **pipeline.py 尾段先入队后提交**：情绪任务在 done 主提交前入队（消除 commit→enqueue 间隙崩溃丢任务；同 content 键不重复入队；入队失败回写 enqueue_failed 审计标记 + requeue_job 兜底）
- **测试**：新增 `tests/test_photo_content.py`（双幂等键各锚定 + enqueue_unique 同键不重复入队/失败重建/队列透传 4 单测 + original/thumbnail_meta/update/moderate 模式）；test_pipeline/test_upload 随入队机制下沉更新 monkeypatch 目标
- **契约**：openapi.json `/contents/upload` 路径与字段不变（仅 docstring 注释级）；errors.py 幂等错误码无消失（CONTENT_002/UPLOAD_* 全在）
- **验证**：受影响域精准测试 149 passed（test_upload/content_upload/contents/techdebt_p0/queue/sync/pipeline/wechat*/requeue_job/thumbnails/photo_content）+ 快速门禁 EXIT=0（首次 lint I001 修复 + RQ job_id 字符集教训已登记 lessons.md）
- **遗留登记**：`enqueue_idempotent`（R4#4，classify/corrections 域）的冒号 job_id 在 RQ 2.x validate_job_id 下存在同一潜在不兼容（真实 client_request_id 入队会 ValueError），不在 F-Content 文件域，登记待归口（F-Auth/集成 Agent）
- 提交：9f0b2f4 `refactor(content)`（分支 techdebt/f-content，未 push；报告文件留集成 Agent 统一提交）

## 2026-08-27 04:40 · 重构批次 CDE 三批集成 + 遗留项处理

- **批次 C（客户端收敛 R3）**：C1 网络层统一（api.ts rawRequest 401 重放+5xx Sentry、event_sync 401 不静默丢批、O4/O5/O9）+ C2 收口与死代码（time.ts 消费切换/parseIsoMs 统一/标签单源/死导出清理，O1/O2/O7/O8/O10/O11/O12/O13）——全 client 域
- **批次 D（测试基建 R8）**：D1 存量迁移+隔离（14 份 db_user 迁移 conftest 公共版、test_queue 独立 Redis /15、热词全局状态快照恢复、test_amap 定向删除、test_sync _eid 随机 UUID）+ D2 覆盖与提速（storage/event_aggregation 覆盖补强、参数化、_to_filter 提纯、correction mock、轮询替代固定 sleep）——全 backend/tests 域
- **批次 E（契约与输入 R4/R6）**：E1 契约一致性（22 处裸码→ERR_*、uuid4_str 共享校验、服务层异常细分 404/409/413/422）+ E2 输入校验与幂等（client_generated_id 幂等键+部分唯一索引、schema 约束补齐、Interview 白名单、search 魔数/后缀白名单、enqueue_idempotent Redis SETNX 预占位）——schemas/api/services/migrations 域
- **31 个 commit 逐条文件域复核通过**（无跨域污染；e53cb02 顺带 docs/lessons.md 属标准教训登记）
- **集成遗留项处理**：O13 messages.uvue shortTime 委托 time.formatIsoShortTime；O11b search.uvue contentTypeCn 委托 search_api 单源；O11#11 profile.uvue 恒真守卫条件删除；test_correction db_user 迁移 conftest 公共版（R8#7 c4ecfac 合入后）；openapi.json 重导出（45 路径 78 schemas）；docs 提交（E1 执行记录 + D2/E2 四条 lessons）
- **决策**：O6 双离线队列合并移交 F9（共享 queue_store.ts 属 F9 域，缺共享存储地基不宜半合并）；qcloud_cos.sts 登记已知问题（UPLOAD_005/008，STS 归口待团队子账号 ARN）；run_validation main() 保持不单测（CLI 入口，现有测试只覆盖纯函数，安全形态）
- **TD-P3 schema.sql 漂移闭环**：57a9af1 已同步 devices 哈希列（refresh_token_hash/refresh_rotated_at），bb9c72e CI=success 确认 Full Gate 通过（cf9d480 失败根因已除）
- **门禁**：快速门禁 ✅ + 精准域 439 passed / 1 deselected（2:27，排除 rag 模型组交 CI）
- **推送**：12f1006（bb9c72e..12f1006，37 commits），CI run 33011027116 in_progress

## 2026-08-26 23:10 · 技术债 TD-P1B 性能与索引批次完成

- **S6-1 aggregate_user 增量游标化（最大性能债）**：l2l3 只扫 30 天增量窗口内未成候选内容（不再全量重扫 400 条远古内容）；接线 `incremental_aggregate`（以"已落库 level>=2 候选"重建 previous 状态，新内容先匹配并入，失败回退本批候选不丢）；`_write_upper_candidates` 批量预载 + 已存在候选（成员组合相同）跳过 LLM 裁决与重查 → 批量导入 O(N²)（反复 LLM）→ 近线性
- **S6-2 N+1 上提循环外**：`_l3_confirmed_exists` 预载合并进 `_write_upper_candidates`（用户标题/确认事件/成员一次查）；L3 linked/owned 检查合并 IN(cluster) 单条；`sync_client_events` 幂等 `client_event_id IN (...)` 批量 + photo_ids 归属按批合并
- **S6-3 sync.push_ops 批量预取**：op_id 幂等查 / content 归属查 / SyncFieldVersion 按 (entity_type,entity_id) 组合 IN 预取（逐 op 冲突判定语义保留）
- **S6-4/S6-6 4 个缺失索引**：`deleted_logs(cleanup_status,deleted_at)` / `offline_queue(user_id,id)` / `messages(user_id,id)` / `profile_l2_evidence(user_id,dimension)`（schema.sql + 迁移 a7b8c9d0e1f2，本地库 upgrade 后 `\d deleted_logs` 确认索引存在）
- **S6-5 reconcile O(N×M)→O(N)**：client_items 先建 set(entity_id)；`_cloud_entities` 投影列只取所需（不再整行 ORM）
- **S6-6 profile_annotator**：`get_or_create_profile` 提循环外（懒加载共享实例，批内只查/建一次）；`_trim_history` 合并单条 DELETE（子查询 LIMIT）；L2 evidence 索引
- **S6-7 echo**：画像敏感一次加载复用（逐候选 N 次 → 每调用 1 次）；LLM 检测仅首候选（20 次 → ≤1 次）
- **S6-8 correction.mark_global_candidates**：全表载入 → 单条 SQL 聚合（GROUP BY+HAVING COUNT(DISTINCT user_id)≥2）+ 批量 UPDATE
- **S6-9 wechat `_corp_access_token` 进程内缓存**（TTL 7200s−200s 余量；40014/42001 失效清缓存重取一次）
- **S6-10 storage cos/minio 进程级单例**（同 fake 模式，懒加载；reset 同步清空）
- 验证：test_event_ops/test_event_sync/test_sync/test_reconcile/test_echo/test_profile_annotator/test_correction/test_pipeline/test_rag/test_wechat*/test_upload/test_cleanup_job/test_notify 等全绿（累计 82+110+62 passed）+ review_agent 快速门禁全绿
- **遗留登记（明确不做）**：upload.py 流式合并（COS copy_object/append 依赖后端能力，P2 批次或另排）；pipeline.py patch_extra 样板收敛（P2B，文件域与 P1B 冲突，P1B 合入后再做）
- 提交：7324e19 perf(techdebt-p1b)（pre-commit 快速门禁通过；提交同时带入了其他 Agent 已暂存的 CI/conftest/test-infra 文件（ci.yml/vector_store/conftest/test_event_ops/pytest.ini/api_smoke/test_agent），已在全量门禁覆盖内，无数据丢失；harness 文件 progress/feature_list 留待集成）
## 2026-08-26 22:20 · Wave4 AgentK（B5d 后台域）集成 + J/H 代劳 + 决策落地

- merge wave4-agentK（B5d 后台域：WorkManager 单队列 P0-P4 + dataSync 前台服务短命化 + attribution tag + 标准基座降级 pending/setInterval，nova 11 真机验证；自定义基座项待验）——client/uni_modules/yishu-background-tasks/ 新插件 19 文件 + yishu-photo-watch 3 文件
- 代劳 J 遗留：test_notify 3 个 care 断言失败根因 = 测试依赖墙钟（22:00-05:00 深夜时段走 late_night）+ _care_streak_days 上界 sent_at<=now 的时钟一致性陷阱 → 测试固定非深夜时段 + 查询去掉上界（14 passed）
- 代劳 H 建议：client api.ts/sync_client.ts 三处 res.data 强转加 typeof object 守卫（后端不可达裸值不再主线程 FATAL）
- 4 决策项全部按推荐落地：① FinetuneJob 删 ORM 模型（表由基线迁移+schema.sql 建、reflow_global 裸 SQL 写，链占位迁移保留并说明）② presign 删除（STS 归口 /upload/sts；ContentUploadResult/CosPresign schema 一并删；OpenAPI 重导 45 路径）③ 短信 501 冻结（P0-1 已生效，确认登记）④ 依赖升版：python-multipart 0.0.18+/httpx 0.27.2+/Pillow 11+（实装 0.0.32/0.28.1/12.3.0）
- 基线：502 passed / 19 deselected（升版后重跑）+ review_agent --full 全绿
- K 遗留待验：自定义基座云打包验证 FGS/WorkManager 真实执行/attribution panel；K-2 后端 asr.py 每通道 max_duration 注入 + _CHANNELS 适配器工厂（排期）

## 2026-08-26 21:40 · 技术债清理 P0 批次完成（安全/正确性 8 项）

- 技术债全面侦察（8 个并行 subagent，报告 docs/技术债审查报告_20260826.md + 计划 docs/技术债清理计划_20260826.md）后启动 P0 执行批次
- P0-1 短信 mock 生产门控（production→501 + 验证码 SHA-256 哈希）；P0-2 COS STS 路径级白名单 policy（photos/voice/thumbnails/{user_id}/*，防前缀逃逸）+ /upload/sts 生产门控；P0-3 上传魔数嗅探（file_magic.py）+ Image.MAX_IMAGE_PIXELS 40MP 炸弹防护；P0-4 process_content 非 voice 失败回写 failed+extra.error；P0-5 complete 建内容 photo 幂等对齐 voice + enqueue 失败不 500；P0-6 StorageError(code,retryable) 包装 + commit 失败 best-effort delete + 孤儿扫描登记；P0-7 错误码登记表（40+3 码唯一真源 + ERR_* 常量 + CONTENT_008/EVENT_005/UPLOAD_008 拆分）；P0-8 RQ job_timeout（ASR 600s）+ Retry(3,[10,30,90])
- 新增 36 个测试（test_techdebt_p0.py 17 + 各套件补齐）；基线 pytest 502 passed / 19 deselected + review_agent --full 全绿
- 遗留登记：STS root ARN 降级待子账号 role、thumbnail_meta 移 worker、超龄 processing 重扫、孤儿对象扫描、分队列 worker 部署（均入代码注释）
- 下一步：P1（配置契约 + 性能测试 2 个并行批次）→ P2（死代码 + 重复收敛）

## 2026-08-26 20:20 · Wave 4 集成（J/L，K 未完成）

- merge wave4-agentJ（ab6d447：ASR 消费域 J-1~J-8）+ wave4-agentL（a0fe630：M3 微信域）→ 集成接线 deb6e24
- 集成接线：upload/complete voice 分支（register_photo_content，对象搬 voice/ 前缀）+ /contents voice cos_key 幂等 + 客户端 uploadVoicePersistent 优先 content_id + pipeline enrich_content_emotion 补 consume_emotion + OpenAPI 重导出 + test_pipeline fixture 补 Message 清理
- 22:00 复盘调度登记（部署侧 cron 跑 backend/scripts/daily_review.py，幂等）
- 基线：pytest 467 passed / 19 deselected + review_agent --full exit 0 全绿
- 遗留：Agent K（B5d）完成后二次集成；WECHAT key 待申请（code2session 已接真实链路，未配保持 mock/501）

## 2026-08-26 19:00 · CI 全链路修复完成（#8-#21）——CI #21 首次双绿

**状态**：CI #21 Fast + Full Gate 全绿｜本地验证 419 passed + api_smoke 6/6 + research 18 全过

### 根因链（7 个，全部登记 docs/lessons.md）
1. #8 postgres 容器就绪竞态（加 qdrant 后 psql 连接被拒）→ Init PG 加重试循环
2. #9-#12 alembic 迁移链不自包含（baseline 仅 alter_column，假设表已由 schema.sql 预建）→ 回退 schema.sql 建库；**issue #2 方向修正**
3. #13 步骤级 env PGPASSWORD 单密码覆盖多用户 psql（-U yishu_app 拿 admin 密码）→ 每条命令内联各自密码
4. #15 schema.sql 缺 profile_annotation_pool（迁移 b0b1c2d3e4f5 建表未同步）→ 补齐，本地临时库验证 38 表
5. #16 pgvector 扩展缺失 + 测试 FK 清理不完整（本地旧库 27 表/4 FK 掩盖）→ schema.sql/setup_pg.sql 加 CREATE EXTENSION + CI 镜像 pgvector/pgvector:pg16 + 测试 fixture 补子表清理
6. #17-#18 qdrant server 1.9.7 与 client>=1.19 不兼容（api_smoke payload 404）→ 镜像升 v1.19.0
7. #19-#20 CI 全新缓存 BGE-M3 现场下载失败（pipeline 测试 status=failed）→ Warm HF models 步骤（scripts/warm_hf_models.py 强制在线）+ 失败详情写 annotation（API 匿名可读）

### 关键决策
- CI 建库源 = schema.sql（#6/#21 验证）；alembic 仅用于本地/生产增量；漂移检测另行设计（issue #2 修正）
- 本地库与 schema.sql 曾严重漂移（27 表/4 FK vs 38 表/20+ FK），本地全绿掩盖 FK 测试问题——测试 fixture 已补子表清理

**下一步**：Wave 4（J/K/L 三 Agent 并行）｜issue #2 关闭文案已备

## 2026-08-25 · PR 评论 5 项修复并更新现有 PR

**状态**：分支已对齐 `origin/develop`｜PR base=`develop`｜音频范围 `49 passed`｜ruff/py_compile 通过

1. 分支已 rebase 到 `develop`，冲突合并后保留音频改动和团队最新管线；现有 PR base 改为 `develop`。
2. `numpy>=1.26` 已显式加入 requirements。
3. 新增 SenseVoice 部署预置脚本与资产校验；生产缺少 `SENSEVOICE_MODEL_DIR` 时显式失败，禁止首个请求下载模型。
4. 阿里云 workspace Host 按 `DASHSCOPE_REGION` 拼接，并保留 `DASHSCOPE_BASE_URL` 覆盖。
5. 主转写与本地情绪拆成两个 RQ 阶段；情绪失败/入队失败不影响真实转写，`auto` 模式在主通道已有情绪时跳过本地推理。

**下一步**：等待团队下一轮审查；生产发布时执行 SenseVoice 模型预置步骤。

## 🔧 2026-08-25 · 第二波遗留全清 + 真机/模拟器验证 + RAG 管线审查

**状态**：全量 pytest 254 passed（+2 回归测试）｜client 编译通过 + 模拟器/真机验证｜review_agent 待跑

### 第二波遗留收尾（nova11 + 模拟器双端验证）
1. **S-ST-1 分片上传真机链路打通**：修复 `uni.getFileSystemManager().getFileInfo` 在 uni-app x 沙箱读不了 MediaStore 绝对路径（真机实测报"读取文件信息失败"）→ 文件大小改从 MediaStore SIZE 列注入 PhotoItem；修复后 上传→端侧聚合→L1 事件上云 accepted 全通
2. **S-MO-1 菜单真机验证**：菜单弹出确认（确认/合并/拆分/取消）；发现 ⋯ 被标题文本 z-order 覆盖（点下半部无效）→ card-ops 加 z-index + 标题右 padding；**双"取消"bug**（itemList 手动加"取消" + showActionSheet 原生自带 → 重复）→ 移除 itemList 里的"取消"
3. **split UI 全链路**（后端 GET /events/{id}/items + 客户端选片面板 + POST split）：模拟器实测通过（items→split 200→timeline 刷新）
4. **split/merge 时间窗 bug（autoflush=False）**：db.add(EventItem) 未落库时 _refresh_event_window 查不到新成员 → 拆出新事件 start_time=None → 时间轴分组到"1月1日" → merge/split 前加 db.flush()；+2 回归测试
5. **EXIF 排查实锤**：MediaStore scan_file 提取 DateTimeOriginal（datetaken 正确）但**丢弃 GPSInfo**（latitude/longitude=NULL）→ 注入测试链路无 GPS；真实相机照片（相机直写 MediaStore）GPS 可用
6. **AMAP 后端全链路 E2E**：login→init→chunk→complete(meta GPS)→worker→place=上海市浦东新区陆家嘴街道东方明珠广播电视塔（真实逆地理）
7. **fs 存储后端新增**（FilesystemStorageBackend）：fake 是进程内单例，uvicorn/worker 跨进程读不到（复盘坑 24）→ 本地文件系统后端跨进程共享；.env STORAGE_BACKEND=fs
8. **S-EM-1 模拟器**：Android 35 x86_64 system-image + AVD yishu_test 创建并启动成功（备用验证设备）
9. **S-XV XView**：仍等自定义基座（SQLCipher 需云打包/本地打包）

### RAG 管线系统性审查（docs/RAG管线审查报告_20260825.md）
1. **recall@3=0.0841 低的根因**：指标分母=expected_label 类全集（15-20 条），Top-3 上限 3/类 ≈ 0.15-0.20，实测已达上限 50-70%——不是检索坏了（hit_rate@3=0.82 / precision@3=0.52 / mrr=0.77）
2. **修复 3 个真 bug**：①rerank 自 8-24 起从未生效（CrossEncoder model_kwargs 参数在 ST 3.4.1 已移除 → 静默降级）→ automodel_args；②时间正则误伤（句中"上个月"被当过滤意图 → length 层空结果）→ 仅句首触发；③关键词精确命中被稠密噪声稀释 → _boost_exact_matches（词元全命中 ×1.8）
3. **rerank 默认关闭**（rerank_enabled=false）：CPU 实测 ~850ms/对，50 候选 ≈ 40s 远超 P95<3s 门禁；GPU 部署时开启
4. **修复后基准**：hit_rate@3=0.7273（门禁 PASS）｜length 层 0.5→1.0｜mrr 0.66
5. **架构级短板**：描述性/释义查询召回不足（双编码器语义鸿沟，需 LLM 改写或 SetFit 类目路由）；评测集结构性缺陷（label 全集作分母 + 无 taken_at payload）

---

## 🔧 2026-08-25 · RAG 测试体系核实修复 + AMAP 逆地理落地 + Sentry 客户端接线

**状态**：全量 pytest 247 passed（+9 test_amap）｜pytest -m rag 14/14 通过（修复后）｜client 编译成功｜review_agent 待跑

### RAG 测试体系核实（第四问答复依据）
1. **指标体系全貌**：research/rag_benchmark/metrics.py 实现 recall@k / hit_rate@k / precision@k / mrr / ndcg@3（k=1/3/5/10），分层（descriptive/keyword/typo/length）+ 行为层（temporal_acc/route_acc）+ overall 全量输出在 evaluation_report.json（hit_rate@3=0.8182 / mrr=0.7727 / ndcg@3=0.5668 / recall@3=0.0841 / route_acc=1.0 / temporal_acc=1.0 / overall_pass=true，11 查询，B+C 混合库 117 条）。门禁只取 hit_rate@3≥0.70（产品口径 Top3≥70%）+ route_acc/temporal_acc + P95<3s
2. **完整测试套件答案**：默认 pytest 套件 238 项 addopts `-m "not rag"` **排除 RAG 重测试**；RAG 集成测试（test_rag.py/test_image_search.py）需单独 `pytest -m rag`（前置 Docker Qdrant + BGE-M3）
3. **实测发现回归**：`pytest -m rag` 1 failed（test_dense_search_recall）——test_rag.py 与生产 yishu_contents 共用 collection，生产库有真实数据（08-24 真机 E2E）后测试点被挤出 Top-k → **修复**：改用独立 collection yishu_test_rag（与基准评测同隔离策略），修复后 14/14 通过
4. **F5 缺口项核实**：①Qwen3-VL 图片塔已真实接线（image_caption + search_by_image + pipeline 写 image_vec）——feature_list 旧 evidence 过时；②corpus-A 500 张截图基准已完成（image_search_report.json：15 查询 hit_rate@3=1.0）——已存在；③双层 Rerank 第一层 bge-reranker 粗排已接线，**第二层 qwen-flash LLM 精排未实现=真实缺口**；④**新发现缺口**：以图搜图延迟 P95=7629ms 超 3s 门禁（未列入 feature_list）

### AMAP 逆地理（高德 Key 落地）
5. **services/external/amap.py**：geohash 精度 6 纯函数（与 geohash2 独立库交叉验证）+ regeo（httpx + with_retry 3 次退避）+ get_place（geo_cache 缓存优先 ≤30 天合规，mock 生产拒落库）
6. **GeoCache 模型 + 迁移**：alembic 4d00dfec7b46 add_geo_cache 已应用，check 零漂移
7. **pipeline._process_photo 接线**：photo GPS → contents.place（失败静默）
8. **config 别名**：amap_api_key AliasChoices（AMAP_API_KEY / AMAP_WEB_API_KEY——Infisical 存量名）
9. **验证**：test_amap 9 项全过；真实调用（MOCK_EXTERNAL_AI=false + infisical run）：外滩坐标 31.2304,121.4737 → 上海市黄浦区南京东路街道（免费额度内）

### Sentry 客户端接线（SENTRY_DSN 落地）
10. **Infisical 核实**：SENTRY_DSN（dev，命名无 _DEV/_PROD 后缀，us.sentry.io；DSN 为公开标识按 Sentry 官方惯例内嵌 client）——后端 main.py 生产环境 sentry_sdk 初始化已有；**客户端缺失** → 补齐
11. **utils/sentry.ts**：轻量 Envelope 协议上报（uni.request POST /api/<project>/envelope/，零三方依赖、标准基座可用；@sentry/vue 因 uni-app x App 端无 DOM 不可用）；captureException/captureMessage/addBreadcrumb（环形缓冲 10 条）
12. **接线**：App.uvue onLaunch initSentry + onError（UTS error17：生命周期参数须声明 any）；api.ts 5xx + 网络失败上报（4xx 不打扰）
13. **编译**：HBuilderX CLI 编译通过（config.ts 曾被 PowerShell 编码破坏重写——教训：改 client 配置勿用 PS 写 UTF-8，用 write 工具）

### 真值数据规格 v1（2026-08-25 续 · 峰宝 grill-me 拍板）
19. **四个决策**：①数据来源=真实用户（beta W18 起采集，非团队自造）→ M1/M2 门禁在 beta 前仍以合成基准为准，真实数据成为 beta 期校准/上线验证层 ②搜索期望结果 expected_ids+expected_label 双轨 ③人脸打码+录音只收自述片段 ④JSON 交付
20. **定位修正（峰宝 2026-08-25 复核）**：这是**给产品部的人工采集手册**——产品部是收集者（招募/访谈/授权导出 → AI 辅助整理 → 人工确认 → JSON 交付）；**不做自动采集管道**（明确不做 search_log 自动埋点）；技术侧仅提供可选辅助：export 初稿脚本 / 人脸打码脚本 / 评测 --real 模式
21. **交付物**：docs/真值数据规格标准_v1.md（5 批字段级规格 + 人工采集操作流程 + 产品部怎么收指引）+ research/truth-data/ 模板（templates/*.example.json）+ scripts/validate_truth_data.py 校验器（产品部交付前自检，全绿入 manifest）

### S-ST-1 分片上传 + 断点续传（2026-08-25 续）
14. **后端集成（关键：否则分片链路与内容管线断裂）**：/upload/complete 接 meta → services/upload.py register_photo_content 建 contents 记录（cos_key）+ enqueue_high(process_content)，语义与 /contents/upload 对齐（taken_at ISO / gps 边界 / source 白名单）；返回 content_id
15. **/upload/chunk 加 POST 别名**：uni-app x uni.uploadFile 不支持 PUT method（编译实测 No parameter named 'method'）——POST 语义与 PUT 一致（幂等+校验）
16. **客户端 uploader.ts v2**：分片协议 init→chunk→complete + 断点续传（upload_id 持久化 uni storage 'yishu_pending_uploads' + GET /status 补缺片）+ GPS 入 meta（PhotoItem.lat/lng，联动 AMAP 逆地理）+ FileSystemManager.getFileInfo 取尺寸（uni.getFileInfo 在 uni-app x 不可用）；urlencoded 表单（后端 Form 字段，uni.request 发 UTSJSONObject 会变 JSON 不匹配）
17. **分片粒度=单块**（chunk_size=file_size）：UTS 无可靠 ArrayBuffer 切片，MVP 照片 ≤20MB，>8MB 真分片留 Windows 波次（后端已支持任意 chunk_size）
18. **验证**：test_upload 13 项全过（+2 集成测试）；全量 pytest 248 passed；HBuilderX 编译通过（200s）；OpenAPI 重导出 41 路径；真机 E2E（注入→init→chunk→complete→管线→时间轴）待峰宝 nova 11

### S-MO-1 手动操作 UI（2026-08-25 续 · confirm + merge）
22. **客户端 event_ops.ts**：confirmEvent（POST /events/confirm）+ mergeEvent（POST /events/merge，source→target）；后端已就绪（test_event_ops 7 项）
23. **时间轴页集成**：L1/L2 卡片右上角 ⋯ 菜单（showActionSheet）——L1：确认这张卡/合并到上一张；L2：确认这个主题；确认后 reload；合并 target=相邻上一张 L1（getPrevL1Id 跳过 L2 组）；用户操作优先语义由后端保证（算法不覆盖）
24. **split 后置**：客户端 timeline 无事件内容列表（EventOut 无 content_ids）→ 需后端补 GET /events/{id}/items 后做选片拆分 UI
25. **编译**：HBuilderX 编译通过（47s 增量）；真机验证待峰宝（操作菜单点按 → 确认/合并 → 时间轴刷新）

### review_agent 内存优化（2026-08-25 续）
26. **根源**：smoke 单进程 SetFit fp32 2.2GB + BGE-M3 fp16 1.7GB + reranker fp32 ~1GB ≈ 5.5-6GB 峰值；可用内存 0.9GB 时 commit 被 SIGKILL
27. **修复**：SetFit/reranker 改 fp16（实测 SetFit 加载 2.2GB→596MB，预测峰值~1.75GB；rerank ~1GB→~0.5GB；-m rag 85s→27s）；smoke 跳过 reranker（RERANKER_MODEL=__disabled__）；test_agent 内存探测 + OOM 友好提示（returncode<0 识别）+ 可用内存 <4GB 警告；教训登记（commit 前确保可用内存 ≥4GB；编译后清理 HBuilderX 残留）

### 遗留/待办
- 以图搜图延迟优化（P95 7.6s→<3s）——F5 真实缺口
- 双层 Rerank 第二层 qwen-flash 精排——F5 真实缺口
- S-MO-1 split 拆分 UI（需后端 GET /events/{id}/items）
- S-XV XView（等自定义基座波次）、S-EM-1 模拟器、离线 op_log、EXIF 兼容性排查
- 真值数据技术侧可选辅助：export 初稿脚本 / 人脸打码脚本 / 评测 --real 模式

---

## 🚀 客户端第三波（2026-08-24 晚 · T-NA/T-TX/T-AU/T-SR/T-PL 多入口+玩法层）

**状态**：✅ 全部真机验证通过（nova 11，提交 e6398cc）｜review_agent 全绿｜pytest 238 passed

### 交付（15 任务 10 项真机验证 PASS）
1. **T-NA-1 四宫格导航**（components/yishu-tabbar）：时间轴/记录/搜索/我的，reLaunch 切换，选中态锈红
2. **T-TX-1/2 文字入口**（pages/record + utils/text_recorder.ts）：POST /contents(text) → 分类异步 job 轮询 → 标签展示 → 点标签三层裁决纠错（correction_log 回写）
3. **T-AU-1/2/3 语音入口**（utils/voice.ts）：uni.getRecorderManager 录 wav → /asr/transcribe 转写 → 可编辑+情绪标签 → voice 入库
4. **T-SR-1/2/3/4 搜索**（pages/search + utils/search_api.ts）：混合结果卡片 + trace 溯源（召回：语义+关键词 · 语义分） + uni.chooseMedia 以图搜图 + degraded 降级黄条
5. **T-PL-1 回响卡片**（首页）：去年今日 GET /echo/today + dismiss 划掉（角贴+泛黄）
6. **T-PL-2 冷启动访谈**（pages/interview）：三层披露 → 三问 → 复述确认 → 可跳过；画像 cold_start_done 生效
7. **T-PL-3 消息中心**（pages/messages）：未读/全部过滤 + 单条已读 + 全部标为已读

### 本波教训（docs/lessons.md +5）
1. uni-app x App 端无 uni.chooseImage，用 uni.chooseMedia
2. UTS setTimeout 自引用箭头函数不可用，轮询用模块级 function+done 回调
3. /interview/questions data 是裸数组；/messages status 仅 unread/read/archived；搜索 trace 结构是 {matched,dense_score,...}
4. uiautomator dump 对 uni-app x 自绘 UI 不可靠，真机定位用截图像素分析（#B05A3A）+ image 坐标交叉验证
5. am start 启动会"未检测到应用资源"，必须 HBuilderX CLI launch；大进程并发（worker+review_agent+pytest）内存不足需先释放

---

## 🚀 客户端第二波 · 第二批（2026-08-24 晚 · S-AG-3/S-SY-4 客户端闭环）

**状态**：客户端代码完成 ✅ 编译通过 ✅；真机 E2E 验证被环境阻塞（HBuilderX 弹窗 → 已解决；设备 USB offline → 待峰宝拔插）

### 客户端（S-AG-3/4 + S-SY-4）
1. **PhotoItem 增加 GPS**（interface.uts + app-android/index.uts）：MediaStore LATITUDE/LONGITUDE 读取（无 GPS=null，端侧按时间窗归组）
2. **uploader.ts 返回 content_id**（UploadedPhoto[]）：上传响应解析 data.id，端侧聚合/事件上云依赖
3. **S-AG-3 端侧聚合运行器**（client/utils/agg_runner.uts）：上传成功照片（本地元数据+content_id）→ UTS ST-DBSCAN（同 AGG-016 同参）→ L1 日卡片事件（client_event_id 幂等键）
4. **S-SY-4 客户端事件上云**（client/utils/event_sync.ts）：POST /events/sync + 指数退避（2s/4s/8s/8s/8s）+ 4xx 停批 + client_event_id 幂等
5. **index 页接线**：监听攒批 → 上传 → 端侧聚合 → 事件上云 → 刷新时间轴（B3-6 端侧 L0/L1 真值闭环）
6. **编译验证**：HBuilderX 编译成功（多轮修复：TS 环境 UTSJSONObject 无 parse/getArray 泛型、无 any、签名类型）

### 环境坑（教训已登记 docs/lessons.md +2）
1. **HBuilderX 模态弹窗静默阻塞 CLI launch**（更新提示 + AI 介绍弹窗）→ computer-use 关弹窗后恢复
2. **Windows 防火墙拦入站 8000**（设备 ping 不通本机）→ 改用 adb reverse USB 隧道（config.ts REAL_DEVICE_HOST=localhost）
3. **设备 USB offline**（待峰宝拔插恢复后执行最终真机验证）

### ✅ 真机 E2E 闭环验证（2026-08-24 21:27 · nova 11，全链路通过）
1. **注入**：10+ 批测试照片 scan_file 注入（新目录 w2tN）→ 观察者触发
2. **上传**：multipart 200 + content_id 解析（修复：uploadFile res.data 是 string，JS 引擎用 split 提取）
3. **端侧聚合**：[yishu] 端侧聚合: 2 张 → 0 簇 → 1 个 L1 事件（UTS ST-DBSCAN 真机运行）
4. **事件上云**：[yishu] 事件上云: accepted=1 dup=0 rejected=0（POST /events/sync，后端需重启加载新路由）
5. **DB 落库**：events 表 generated_by=device + client_event_id 唯一（ev-1787578023265-857538）
6. **时间轴渲染**：首页截图显示端侧提交的 L1 卡片（2026-08-24 · 1条 / 1张照片）
7. **S-SY-5 前台触发**：App 重启后自动恢复监听（onLoad 检查权限自动 startWatch），无 CTA 也触发

### 环境坑（本批再踩，教训 +3）
1. **uni.uploadFile res.data 是 string**（与 uni.request 的 UTSJSONObject 不同）→ JS 引擎用字符串操作解析；诊断日志定位（解析失败被误判为上传失败数小时）
2. **华为增强纯净模式拦截 HBuilderX 安装**（pure_enhanced_mode_state=1）→ 应用市场反复弹窗抢前台 + App 卡 D 状态 → settings put secure pure_enhanced_mode_state 0
3. **端侧 EXIF 兜底对 PIL 写入的 EXIF 不生效**（ExifInterface getAttribute 返回 null 无异常）→ 真实相机照片 DATE_TAKEN 可靠；测试注入场景时间窗偏移（后端 EXIF 权威已兜底 contents）
4. 设备时钟错位（显示 8/25 09:22，实际 8/24 21:21）——第一波已知，上线前校准

### 遗留（后续波次）
- S-XV XView（SQLCipher 随自定义基座波次，标准基座无三方依赖）；S-SY-4 离线 op_log 队列；S-EM-1 模拟器；S-ST-1 STS；S-MO-1 手动操作 UI
- 端侧 EXIF 兼容性（PIL 写入格式）待查
# Session Progress Log — 忆述光华

## 2026-08-27 14:50 · 重构批次 G 集成（G1 认证安全 + G2 越权纵深）

- **merge 顺序（共享工作树，两分支均从 5fcbd29 切出）**：`--no-ff techdebt/g1`（67f50f1，22 文件）→ `--no-ff techdebt/g2`（1563cde，7 文件），均无冲突；main.py 双方都动过，终态取 G2 提交的合并态超集（create_app + 安全头 + healthz 收敛 + G1 限流接线，与工作区 G1 副本哈希一致验证后丢弃）
- **G1 认证安全**：refresh single-flight（client auth.ts 共享 in-flight，并发 401 只一次 refresh，node 单测 4/4）；POST /auth/logout（AUTH-006 吊销，坏 token 仍 200 幂等）；refresh_token HMAC-SHA256 + 独立密钥 refresh_token_hmac_key（hmac$ 版本前缀 + 兼容存量 SHA-256 + 轮换 OR(hmac,legacy) 原子）+ 生产强制非默认密钥门禁；SMS 验证码加盐（sms_codes.salt 迁移 f1a2b3c4d5e6 + schema.sql）；通用限流中间件（core/ratelimit.py，Redis 固定窗口按 client_ip/user，覆盖 auth/ASR/搜索三域，IP 白名单 + trust_proxy + Redis 故障 MemoryStore 降级 + 置 RequestID 内侧保 429 带 X-Request-ID + 429 信封 RATE_LIMITED）；conftest autouse 默认关限流防跨用例 flaky
- **G2 越权与纵深**：wechat 回调 timestamp 新鲜度窗口（±300s 防重放，GET/POST 双入口经 gateway verify 生效）；安全响应头 + 生产关 /docs（create_app()，docs/openapi/redoc_url 生产置 None）；/healthz 收敛为 {status:ok}；sync_pull limit（limit<1 或 >500 → 422 SYNC_001，errors.py 登记）
- **门禁**：受影响域精准 113 passed（auth_g1/ratelimit/auth_db/security_p3/config_alias/techdebt_p0/wechat/security_g2/sync）+ single-flight node 4/4 + 快速门禁 EXIT=0；契约只增不减（openapi 45→46 仅 +logout；errors +SYNC_001）
- **教训登记**：G2 14:30 --full 失败为共享工作树混合在途代码假象（G1 未提交 + DB 未迁移）+ healthz 字段断言未同步 → 已登记 lessons 并解除阻断（集成先合并再复验、字段契约同步断言）
- **遗留登记**：REFRESH_TOKEN_HMAC_KEY 生产部署需在 Infisical/.env 配独立强随机密钥；限流阈值/白名单按部署环境复核
- 推送：progress.md + lessons.md 集成提交后 push develop，CI 复验中

## 🚀 客户端第二波 · 首批交付（2026-08-24 晚 · W5 起）

**状态**：S-SY-1 / S-SY-2 / S-AG-1 / S-AG-2 ✅（含真机验证）；剩余 S-XV/S-SY-4/5/6/S-ST/S-MO 按依赖序推进

### 后端（S-SY-1/2 全绿 · pytest 234 passed）
1. **S-SY-1 `POST /api/v1/events/sync`**（B3-6 端侧 L0/L1 真值落云）：client_event_id 幂等（同用户部分唯一索引兜底并发）+ 照片归属校验（越权整条 rejected）+ 落库 L1（generated_by=device）+ 变更日志写 offline_queue（其他端增量拉取可见 → M4 端间一致）+ 受影响照片云侧补 L2/L3 候选
2. **S-SY-2 aggregate_user 重构**：默认 mode="l2l3"（云侧只跑 L2/L3，caption/CI 打标保留 _process_photo；L1 由端侧提交）；mode="full" 保留第一波全量管线作基线迁移；修复 _write_upper_candidates 幂等检查按 level>=2（照片挂 L1 不再拦截 L2/L3 候选）
3. **迁移**：events.client_event_id 列 + uq_events_user_client_event 部分唯一索引（alembic a1b2c3d4e5f6 已应用）
4. **测试**：test_event_sync.py 7 项（幂等/越权/空列表/落库+变更日志/L2 触发/重发不重复/API+时间轴/并发唯一索引兜底）+ test_pipeline 聚合契约更新（云侧不再自动建 L1；full 模式基线回归）+ test_agg_reference.py 4 项（参考端语义锁）

### 客户端（S-AG-1/2 ✅ 真机 10/10）
5. **S-AG-1 UTS ST-DBSCAN 算法层**（client/utils/agg/）：agg_config.uts（参数单一来源 ↔ pipeline.py AGG_CONFIG）/ st_dbscan.uts（Photo/DayCard/haversineM/stDbscan/l1DailyAggregate，时区偏移参数化）/ pipeline.uts（RawPhoto/preprocess：连拍折叠+GPS 漂移置空）——纯计算层，无平台依赖
6. **S-AG-2 AGG-016 一致性**：scripts/gen_agg_fixtures.py（Python 同参双跑 → 生成 fixtures.uts 10 用例 57 照片）+ pages/debug/agg-check 自检页（逐用例比对簇成员集合+日卡片）
7. **真机验证（nova 11）**：HBuilderX 编译成功 → 实机运行自检页 **10/10 PASS**（连拍折叠/两天两簇/散片稀疏/无 GPS 归组/深夜归属/漂移修正/稀疏多天/单张/UTC 日界/30 张规模）——截图证据 .cowork-temp/agg7.png
8. **恢复**：临时导航开关已回退（config.ts AGG_CHECK_ON_DEVICE=false），设备已恢复首页

### 教训登记
docs/lessons.md +1：AGG-016 测试断言不得手写期望（先跑参考实现再写断言）

### 下一批（按依赖序）
- S-AG-3 Kotlin 桥接（相册读取/EXIF/定位 → 算法层输入）→ S-AG-4 增量触发
- S-XV XView（SQLCipher 5 表 + 迁移 + 轻量 DAO）
- S-SY-4/5/6 客户端同步协议（op_log 队列/退避/三路触发/LWW）
- S-ST-1 STS 直传分片；S-MO-1 手动操作 UI

## 📱 真机 E2E 全链路验收（2026-08-24 下午 · nova 11 FOA-AL00）

**链路已全通**：相册监听（ContentObserver）→ 游标去重 → 4s 静默窗口攒批 → multipart 上传 → 后端 EXIF → 云侧聚合 → F8 时间轴渲染 ✅

### 验收证据（B-UT/B-UP/B-F8/B-VA）
1. **编译**：HBuilderX 5.15 CLI 全量编译通过（纯 UTS 插件 + utils + 页面，~30s/轮，多轮迭代修复）
2. **运行**：标准调试基座安装→同步→启动成功（onLaunch 3s，页面渲染 234ms）
3. **空状态**（B-F8-3）：截屏验证——标题/副标题/插画/文案/CTA 按钮，配色符合视觉规范 ✅
4. **真实上传**：50 张测试照片 → 观察者分批发现（found 4/1/...）→ 上传 200 OK → 自动刷新时间轴 ✅
5. **EXIF 真值**：后端 PIL 提取 DateTimeOriginal 覆盖客户端时间 → contents taken_at = 08-22(40)/08-23(10) ✅（曾实测 scan_file 污染 DATE_TAKEN → 后端 EXIF 修复）
6. **时间轴渲染**（B-F8-1）：截屏验证 L1 日卡片（“2026-08-22 · 1条 / 40 张照片”等）双卡片结构正确、视觉规范落地 ✅
7. **隐私防护**：首扫游标初始化到 max(id)（不导入存量相册）——事故教训已修复并验证（只收新照片）

### 真机暴露问题（已修 + 教训登记）
1. 首扫全量上传 9319 张存量相册（隐私红线）→ 游标初始化修复
2. scan_file 不提取 EXIF → 后端 EXIF 权威解析（新增 pytest：EXIF 覆盖客户端时间）
3. 并发双 ensureLogin 撞 devices 唯一约束（后端 500）→ _issue_tokens IntegrityError 兜底 + 客户端单飞
4. fake 存储 512MB 容量上限触发（防护生效，误伤后续上传）→ 重启进程即恢复；真实联调用 minio/cos
5. 标准基座权限以基座 manifest 为准；SDK31 用 READ_EXTERNAL_STORAGE（pm grant 验证）

### 遗留说明
- 手机时钟/时区错乱（设备显示 08-25 05:00）→ 日标签偏移一天；设备时间正常后自愈（非代码缺陷）
- L2 语义归并待真实数据（P2-07 已知）；L2 候选结构在 50 张全链路验证中已存在
- 30s 门禁：服务端链路 4.2s 上传 + 5.6s 管线（单进程验证）；设备侧受 WiFi/扫描节奏影响，观察者分批触发（4s 窗口）
- 设备上测试照片目录已清理；后端 dev-client 测试用户数据已清

## 🚀 客户端第一波（2026-08-24 · W3）

**状态**：后端交付完成 ✅ / 客户端代码全部就绪（待 HBuilderX 编译 + 真机验收）

### 后端（B-BE-1/2/3 ✅ 全绿）
- 新增 `POST /api/v1/contents/upload`（multipart file + meta JSON）→ storage 存原件（cos_key）→ contents 落库（photo/processing）→ enqueue_high(process_content)；复用 409 去重 / moderate 护栏 / source 白名单 / GPS 边界
- 校验：图片类型白名单（jpg/jpeg/png/webp/heic/heif）、空文件 422、超 20MB 413、坏 meta 422
- 测试：`backend/tests/test_content_upload.py` 9 项新增全过（成功/去重/未授权/类型/空文件/超限/坏 meta/护栏/HEIC）
- curl 冒烟：上传 200 → 落库；重复哈希 409 CONTENT_002 ✅
- 全链路单进程验证（`.cowork-temp/verify_wave1_server_chain.py`）：50 张生成照片 → upload 4.2s → 管线 5.6s → timeline L1=3 日卡片（20/15/15 与真值一致）✅；L2 候选存在（cloud-proto，语义归并待真实数据，P2-07 已知）

### 客户端（B-CL/B-UT/B-UP/B-F8 代码就绪，编译/真机待峰宝）
- `client/` uni-app x 工程：manifest/pages/main.uts/App.uvue + pages/index/index.uvue（F8 时间轴）
- utils：config.ts（baseURL 开关）/ auth.ts（mock 登录 + EncryptedSharedPreferences + 401 refresh）/ api.ts（统一请求+错误映射+全局 toast）/ uploader.ts（并发≤3+重试2+进度）/ timeline.ts（ISO 解析+日期分组）
- uni_modules/yishu-photo-watch：UTS 插件（Hybrid Mode）——PhotoObserver.kt（ContentObserver + 游标去重 + 4s 静默窗口攒批）、SecurePrefs.kt（EncryptedSharedPreferences）、index.uts 桥接
- 视觉规范 v1 落地：相纸白 #F6F1E7 / 墨褐 #3A2E25 / 锈红 #B05A3A、衬线标题、撕边卡片+底部投影、空状态（空白相纸 SVG）
- `scripts/generate_test_photos.py`：50 张带 EXIF 拍摄时间测试照片（3 天 4 片段，L1/L2 真值已知），--push 注入 MediaScanner

### Harness
- ruff.toml 排除 client/；review_agent 三处扫描（syntax/secrets/todos）加 `_skip_path`（client 非 Python 工具链，B2 决策）
- feature_list.json：F1/F8 置 in-progress + 证据更新

### ⛔ 阻塞/待办（需峰宝/设备）
1. ~~nova 11 adb unauthorized~~ ✅ 已授权（2026-08-24 下午真机 E2E 全链路验收完成）
2. HBuilderX 编译验证：✅ 已通过（纯 UTS 插件 + 标准基座，多轮编译修复）
3. L2 语义归并：待 50 张真实照片 + 事件真值（团队）
4. DASHSCOPE/TENCENT/COS/AMAP/SENTRY key 在 Infisical（本地 .env 为空，mock 模式开发）；真实联调按 skills/infisical-secrets/SKILL.md 注入
5. EncryptedSharedPreferences（SecurePrefs）随自定义基座波次恢复（当前 uni storage 临时）

### 📌 环境经验（2026-08-24）
- 本机 LobsterAI python 不加载 cwd/PYTHONPATH：`python -m app.workers.worker` 报 No module named 'app' → 需 `python -c "import sys; sys.path.insert(0, r'D:\GuangH-App\backend'); from app.workers.worker import main; main()" high low` 启动
- fake 存储为进程内单例：uvicorn 与独立 worker 不共享 → 本地 E2E 用单进程 TestClient 验证；dev 真联调用 minio/cos（docker hub 当前不可达，minio 镜像拉不动）

## 📌 当前状态（2026-08-20）

**质量门禁**：pytest 215 passed（14 deselected，覆盖率 75.20%，2026-08-21 00:42 全量证据）｜ruff 全绿｜review_agent 全绿（2026-08-24 修复 research 段模块路径后恢复）｜教训登记 hook 生效

**测试/基础设施**：PG/Redis/Qdrant 本地运行中（Docker 重启后需手动 `docker start yishu-redis yishu-qdrant`）；BGE-M3 / SetFit / reranker-v2-m3 本地模型就绪；新增 webrtcvad-wheels（长录音分段）

### 🔧 三方审查修复（2026-08-20 · 51 项 checklist）

**P0 安全+数据正确性（7/7 ✅）**：上传 IDOR 归属校验｜敏感词护栏 URL 早退绕过｜wechat/delete 鉴权｜搜索时间过滤 epoch 秒 payload（Qdrant 实测修复）｜照片 caption 先下载再调用｜mock 转写生产拒绝入库｜护栏未配 key 默认拒发（用户拍板）

**P1 技术债+偏离（17/17 ✅）**：同步 naive/aware 时间 500｜分片大小校验｜mock 凭证生产 501｜并发 IntegrityError 竞态｜CORS 白名单｜回响敏感双查（用户拍板：标记+LLM）｜纠错三道噪音闸门（≥3 次一致/3 天回改）｜错误契约统一 ApiError｜常量去重(sync_common/标签词表)｜模型路径 CWD 独立｜N+1 修复(时间轴/merge/回响)｜队列优先级(voice/photo 高优)｜敏感词打码映射修复｜server_version 用户级游标｜updated_at onupdate+interview 单事务｜长录音 VAD 分段(webrtcvad)｜测试质量(恒真断言/SetFit 评估口径)

**P2 架构重构（7/7 ✅，2026-08-20）**：推理移 worker（classify/arbitrate 异步+job 轮询，search 并发信号量）｜worker 拆分（process_content 下沉 services/pipeline.py）｜research 包边界（event_aggregation 移入 backend，删 sys.path hack）｜单例收敛（security 函数内读/correction Qdrant 统一/fake 容量上限+reset）｜Alembic 落地（ORM 唯一权威，check 零漂移，FinetuneJob 纳入 ORM，遗留空表收敛）｜前缀/游标统一（asr 域拆分+guard 独立，删 cursor 死字段）｜事件 L2/L3 候选落库 + 以图搜图 image_vec 生产接线

> ⚠️ P2-01 待办（用户确认时要求）：同步改设计文档（MVP方案_v3/B2/B5a）与 OpenAPI 契约（classify/arbitrate 改异步）
> ⚠️ P2-07 L2/L3 为候选级 draft 落库（generated_by=cloud-proto），LLM 语义归并待真实数据到位

**P3 顺手清理（部分完成 2026-08-20）**：未用依赖已删（openai/slowapi/datasketch/passlib/bcrypt/python-dotenv）｜死代码已删（token_is_valid/_PRESET_SENSITIVE_WORDS/_rule_check）｜conftest.py 建立（27 测试文件 sys.path 样板清除）｜storage fake 容量上限+reset。暂缓：状态枚举化（DB 迁移风险）、22:00 调度固化（待部署决策）、rag 测试 collection 隔离（待 CI 决策）

**设计文档同步（2026-08-20，P2-01 契约变更）**：OpenAPI契约.md 新增「分类与裁决」异步化节（变更前→原因→变更后）+ ASR 域拆分说明 + 39 路径；MVP方案_v3.md 新增「实现变更记录」节（4 项技术变更）；开发决策清单 #9 补落地注记；docs/openapi.json 已重新导出（39 路径）

> 详见 [review-report.md](file:///D:/GuangH-App/review-report.md) 与 [refactor-plan.md](file:///D:/GuangH-App/refactor-plan.md)

### ✅ 已完成功能（对照 MVP F1-F9）

| 功能 | 状态 | 说明 |
|---|---|---|
| F2 文字碎片输入 | ✅ 后端 | SetFit 5 类分类（classify_batch）+ 内容入库管线 |
| F3 语音输入 | ✅ 后端 | FunASR 云端真实转写 + 本地 CPU SenseVoiceSmall 声学情绪 + 多格式解码 + 入库管线 |
| F4 分类纠错 | ✅ 后端 | 三层裁决 + 共性纠错微调流水线（≥50 触发） |
| F5 描述性搜索 | ✅ 后端 | BGE-M3+Qdrant RRF + NER + mixed 融合 + 以图搜图 + reranker-v2-m3；RAG 门禁 hit_rate@3=0.8182 / route_acc=1.0 / temporal_acc=1.0；文字搜图 hit_rate@3=1.0 |
| F7 冷启动访谈 | ✅ 后端 | interview API + 画像扩展队列 |
| P2 回响机制 | ✅ 后端 | 去年今日 + 敏感排除 + 每天≤1 |
| B4 数据同步 | ✅ 后端 | LWW/软删/幂等/COS 分片续传/对账 |
| 推送消息中心 | ✅ 后端 | messages + 复盘 22:00 + mock 通道 |
| 微信"找" | ✅ 沙箱 | 消息解析→RAG→回复（真实企微凭证待办） |
| 事件聚合 | ✅ 后端 | L1 日卡片落库 + 用户手动 merge/split/confirm |
| 护栏 | ✅ 后端 | 开源词库 4 类 + 网址黑名单 1.45w + 号码打码 + LLM 检测 + contents 入库接线 |

### 📋 待办（后端可继续做 / 等团队数据）

**可继续做**：
1. 语音 COS 下载接存储层收尾（worker 已用 get_object，待真实 COS 联调）
2. photo 生产 caption 真实调用已验证（Qwen3-VL 5s/张）；图片入库管线接真实 caption
3. 事件 L2/L3 落库（当前只 L1）
4. 搜索降级契约（degraded/errors 前端规范）
5. 备份体系补全（WAL 归档/PITR/Qdrant 快照异地）
6. 性能压测（100 并发 / P95 曲线）
7. OpenAPI 契约持续同步（当前 37 路径）

**等团队（数据/决策/凭证）**：
1. 50 条真实搜索查询（RAG 上线评测集，门禁硬依赖）
2. 100-200 条真实中文碎片（5 类标注，SetFit 校准）
3. 20-50 段真实录音+转写（WER 校准）
4. 50-100 张真实照片 + 3-5 天事件聚合真值
5. 10-20 条纠错样本
6. 产品部拍板：关怀文案库 / 模板骨架池 / 隐私措辞
7. 合规三申请（企微认证/ICP/软著）+ 缺的密钥（微信/企微/Sentry/高德）
8. UTS POC 需 Android 原生人力（全局 Gate）

---

## 最近会话日志

## 2026-08-24 · 远程仓库核对 + review_agent 修复 + 文档台账清理

**已交付**：
1. **远程仓库核对**（origin=zqhmy1234/YSGH-APP）：远程仅 main 分支，HEAD=3869111「MVP 后端全量交付（单提交快照）」，与本地 develop 同 commit，无他人新增修改；本地 7 个 feature/m1-* 分支与 tag v0.1.0-sprint1 均未推送
2. **review_agent research 段修复**：test_agent.py run_research_validation 仍用旧路径 `research.event_aggregation`（P2-02 迁入 backend 后残留）→ 改 `app.services.event_aggregation.run_validation`；`--only research` 实测全过（497 张基准，EXIT=0）
3. **文档台账清理**：pytest 数字统一为 215 passed（210/203/145 均为过期值）；去除 progress.md 重复标题；lessons.md 重复标题清理；feature_list/session-handoff 同步

**发现并记录**：.cowork-temp/test-report.json（8-21 00:42）显示 review_agent 上次实际 passed=false（research 段 blocking），与文档"全绿"表述不符——本次已修复根因。
## 2026-08-25 · 本地声学情绪检测完成

**已完成**：
1. **真实情绪通道**：移除过时的云端 `sensevoice-v1` WAV 降级实现，接入官方 `iic/SenseVoiceSmall-onnx` 量化模型，本地 CPU 4 线程懒加载；FunASR 云端转写成功后独立执行情绪增强，云端失败时 SenseVoice 仍可作为本地转写降级。
2. **常见格式统一输入**：使用随依赖安装的 FFmpeg，将 M4A/MP3/AAC/WAV 等统一解码为 16kHz 单声道 float32 PCM，不再只有 WAV 能进入情绪检测。
3. **可信置信度与落库**：从 SenseVoice 富转写第二个情绪查询位的 7 类 logits 计算情绪置信度；独立保存 `emotion_confidence/source/model/actionable`，不再把 ASR 文本置信度误存为情绪置信度；低于 0.7 只记录、不标记为可触发。
4. **降级边界**：情绪模型失败会留下 `sensevoice_emotion:*` 审计错误，但不会抹掉已成功的云端真实转写；数字静音仍直接返回 `no_speech`，不产生情绪。
5. **生产 mock 护栏**：生产环境即使误开全局 mock，也会返回 `MOCK_DISABLED`，不会生成或保存假转写。

**真实验证**：同一条 5 秒 M4A 已分别完成 FunASR 云端转写和 SenseVoiceSmall 本地 CPU 推理；本地判定为“平静”，情绪置信度约 `0.8741`。个人 Key 仅通过临时进程环境使用，未写入工作区或持久环境。

**回归**：ASR/API + 语音入库 + 内容接口定向测试 `40 passed`；相关文件 ruff、py_compile 全绿。模型缓存与兼容分词资产已登记到 `backend/models/README.md`。

**提交状态**：音频范围验证与提交准备已完成；提交状态以 `git log/status` 为准，尚未 push。

## 2026-08-24 · ASR 多格式与入库状态收口

**已完成**：
1. **主通道升级**：接入 Fun-ASR Flash（`fun-asr-flash-2026-06-15`）Data URI 调用，支持 AAC/AMR/FLAC/M4A/MP3/OGG/OPUS/WAV/WebM/WMA；保留 WAV 的 SenseVoice 情绪降级通道。
2. **输入与长录音**：API 上传保留 8MB 上限；内部对象存储的长 WAV 允许进入 VAD 分段，单段最长 4 分钟；超过 8MB 的压缩音频明确要求切分或转 WAV，不再误走长录音逻辑。
3. **状态语义修复**：正常文本为 `succeeded`；数字静音或供应商明确空文本为 `no_speech`；缺 Key、网络/限流/供应商异常为 `failed_retryable` 或 `failed_final`。失败会写入 `content.status=failed` 和审计信息，不再出现“无转写文本但 done”的假完成。
4. **可审计与安全**：保留模型、通道、供应商 request id、音频格式、源文件 SHA-256、usage/segments/errors；生产模式不再降级为 mock 假文本；API Key 仍只从环境读取，本次未填写或落盘。

**验证**：ASR 服务/API 23 项通过；语音入库管线 5 项通过；内容 API 8 项通过；相关文件 ruff + py_compile 全绿。所有供应商交互均使用 monkeypatch，**本次未配置 DASHSCOPE_API_KEY，未执行真实线上转写回归**。

**待验收**：配置临时 Key 后，用真实 M4A/WAV/MP3、空白录音、限流/断网场景做线上验收；真实 WER 仍需团队提供 20-50 段标注录音校准。

**提交状态**：尚未 commit、尚未 push。仓库强制全量门禁的本次报告为 15 failed / 1 error（非 ASR 基线：缺 BGE-M3/SetFit 本地模型、PG 缺 pgvector、research 旧导入入口、同步测试清理残留等），且 review_agent 的缺 pytest 判断把真实失败误标为 skip。为避免绕过门禁，本次 ASR 改动保留在独立分支，需先单独修复团队门禁或补齐其数 GB 模型环境后再提交。

## 2026-08-20 02:5x · 教训强制 Hook + 生产兜底待办开发

**已交付**：
1. **教训登记程序化强制**（用户要求非提示词级）：scripts/lessons.py（add/recent/check_lessons，epoch 时间戳防时区漂移）+ review_agent 集成——检查失败 → 写 last-failure.json；下次通过前未登记教训 → 阻断 commit（pre-commit 无法绕过）；docs/lessons.md 已登记 6 条实战教训；闭环验证通过
2. **外部 API 统一重试封装**（AGENTS.md #13 教训落地）：services/external/retry.py with_retry（3 次指数退避 + 线程池超时 + 可重试异常判定 5xx/10053/网络类）；应用到 dashscope（_chat_text/image_caption）+ tencent_ci（打标/审核）；test_retry 10 项
3. **事件用户手动操作接线**（B3-5/AGG-013）：merge（成员转移+源软删+confirmed）/ split（拆出建新事件）/ confirm（转正改标题）+ EventEditLog 记录 + API 三端点（原 501）；用户操作优先——操作后 confirmed 不再被算法改动；test_event_ops 7 项
4. **环境修复**：Docker 引擎未起（redis/qdrant 容器重建）+ PG 伪死（残留进程+postmaster.pid）→ 全量测试大面积失败排查（教训已登记）

**验证**：pytest 190 全过 + ruff 全绿 + review_agent 全绿。
**待办**：①语音 COS 下载接存储层（worker 已留接口）②photo 生产 caption 真实调用验证 ③corpus-A 61 张 caption 补齐 ④上线评测集（50 条真实查询，等团队）⑤RAG 门禁调优（hit_rate 0.6667→0.70）

## 2026-08-20 02:00 · AI 管线接线 + 模型清理 + 生产兜底审计

**已交付（用户拍板：P0 文本/语音 → P1 图片 → P2 事件聚合一起接，用户无感知失败）**：
1. **process_content 全类型管线**（worker.py）：text→SetFit 分类；voice→ASR 转写+分类；photo→caption+CI 打标；全部→事件聚合。每步独立 try/except 静默失败（status 仍 done，明细入 extra.error）——替代原占位实现
2. **事件聚合落库**（services/events.py + Event/EventItem ORM）：调 research pipeline（L0+L1）写 L1 日卡片 + event_items；同日去重合并（增量触发不拆事件，E2E 实测 3 条→1 事件）；events API timeline 返回真实数据（原 501）；merge/split/confirm 仍 501
3. **E2E 实测**：text 9.6s（分类 5s+索引 4s）、photo 0.8s；3 条同天内容 → 1 个 L1 事件 items=3
4. **关键 bug 修复**：classifier.py 漏设 HF_HUB_OFFLINE=1 → worker 里先于 embedding 加载时联网卡死（huggingface.co 10s×5 重试×多文件 = 2min+）；SetFit 单条冷启动 27s（warmup），批量 5 条 27.6s 摊薄——已加 classify_batch
5. **C 盘清理**：HF 缓存 8.6GB→2.2GB（删 bge-m3 旧 snapshot 2.2GB + incomplete 1.65GB + grounding-dino 1.8GB + bert-base 841MB + 3 空壳）——用户确认删除
6. **模型资产清单**：backend/models/README.md（在用/可删/下载源/加载位置）；AGENTS.md 环境教训 18-22 条（HF 缓存机制/进程管理/下载纪律）
7. **生产兜底审计**：docs/生产兜底审计与交付差距盘点_20260820.md（已有兜底 5 项 + 缺口：管线已补、外部 API 统一重试/超时未做、降级契约未定）

**验证**：pytest 173 全过 + ruff 全绿 + review_agent 全绿。
**待办**：①外部 API 统一重试封装（dashscope/CI/OCR）②events merge/split/confirm 用户手动操作 ③语音 COS 下载接存储层 ④photo 生产 caption 真实调用（现 mock）⑤corpus-A 61 张 caption 补齐


## 2026-08-25 · RAG 指标提升落地（4 PR）+ LLM 改写调研修复 + 测评报告

**状态**：commit 4d6fca3（前一轮）+ 本轮待提交｜门禁 PASS｜17 项相关测试 + ruff 全绿

### RAG 提升落地（commit 4d6fca3）
1. **P1-A 类目路由**（规则词表 → content_class 过滤 + 空结果回退）：descriptive 层 hit_rate@3 0.5→1.0
2. **P0-B 显式相关口径**：evaluate_retrieval_explicit + 6 条查询补显式 id → 产品口径 recall@3=0.9167
3. **P0-D 命中梯度**：≥50% 词元 ×1.3 / 全命中 ×1.8 → keyword precision@3 0.44→0.78
4. **P1-B2 外部测试集**：T2Ranking 抽取 70 查询/88 段（≥60 达标），run_eval --external
5. **指标**：B+C hit_rate@3 0.7273→0.9091、mrr 0.66→0.85、ndcg 0.42→0.84；EXT recall@3=0.8857、hit_rate@3=0.9143

### LLM 改写调研与修复（本轮，待提交）
- **现象**：替换式改写伤害（EXT recall 0.886→0.75，9/10 短查询被无谓改写）
- **调研结论**：改写应是【有门控 + 加性】不是替代；双路召回首个实现有接线 bug（原查询路误用回退前 filters → 恒空）
- **修复**：①prompt v2 门控（短关键词原样返回，只改错字/口语/描述性）②双路用 eff_filters（最后一次成功搜索的过滤器）③类目路由跑原始查询④路由固定规则版（LLM 路由误判"灯"为 image）⑤llm_rewrite_enabled 默认开
- **验证（探针，未跑全量）**：EXT 8 查询 LLM 模式 7/8 = 规则基线；合成集 6 条全 HIT；买牛乃→买牛奶纠错生效
- **附带修复**：dashscope 403（User 环境变量残留旧 key 覆盖 .env + SDK 不读 settings → _ensure_api_key）

### 交付
- docs/RAG测评报告_20260825.md（测评全流程/数据构成/结果/发现）
- docs/README.md 文档索引（第一轮整理）；refactor-plan/review-report 归位 docs/

## Wave 1 集成完成（2026-08-26 03:20）

- 两个并行 worktree（wave1-agentA / wave1-agentC）开发完成并经 review_agent 门禁（各自域内 62/68 passed）
- 集成 Agent merge：786f134（A B2 搜索）+ 23b55f4（C B5b 护栏，lessons 冲突保留两边）
- 集成接线：main.py 注册 profile_sensitive_router；photo 首入库 payload 后补刷新（vector_store.update_payload + pipeline_ext.payload.build_payload + pipeline.py 逆地理后一行）；搜索/以图搜图规则级敏感过滤（filter_sensitive_rule，B5b-1 🟢 转交项闭环）
- 集成后全量：312 passed（基线 281 + 新增 31）+ 14/17 deselected（rag 重测试）+ api_smoke 6/6 + review_agent 全绿
- 真实 bug 修复（A 发现）：检索阶段用户隔离缺失（_to_filter 无 user_id → 全库召回跨用户污染）——已修复并加回归测试
- 待 key/环境：corpus-A 2 张 0 字节/审查拒绝；Qwen3-VL-Embedding 图片塔待开通（现 caption 路径 + 缓存）；NER LLM 兜底默认关

## Wave 2 集成完成（2026-08-26 06:30）

- 两个并行 worktree（wave2-agentD B3 云侧 / wave2-agentE B3 端侧+UI）开发完成并验收；Agent F（M1 补遗）仍在开发中（wave2-agentF worktree，基线 ab11507，与 D/E 文件域零重叠，不阻塞本次集成）
- 集成 Agent merge：7e8c142（D：L2 地点域连续 5km/12hr + LLM 归并裁决（qwen 真实通道验证通过）+ L3 7 天窗/生命周期 + 封面选择（人脸→质量→时间）+ GPS 漂移完善 + confirmed 保护 + 增量先匹配后分裂 + OCR 内容维 + 15 新测试）+ ae801a7（E：30min 保守开关 + 预处理去重 + L2 待确认区 UI + 封面/反向入口 + 30s 验收埋点 + AGG 双跑 14 用例 + 8 API 测试；lessons 冲突保留两边三条记录）
- 集成接线（fe1b376）：云侧 AGG_CONFIG 对齐端侧 30min 保守开关（conservative_mode → l0_eps_t_sec()，显式传参不受影响）；main.py 已含 event_items_router（E 在分支内注册，merge 保留）；修复 D 合并代码 B905 zip strict= lint（ruff 版本漂移，登记教训）
- 集成后全量：341 passed 基线（见 fullgate-wave2.log）
- 待办：Content.extra quality_score/face_count 无写入方（内容管线未接腾讯 CI 人脸标签，封面选择回退时间居中）——记录待后续；DASHSCOPE key 已配置，L2 归并真实 qwen 通道验证通过；托管护栏 llm_ops/guard_managed 待 Wave 2 F 实现

## Wave 3 集成完成（2026-08-26 14:30）——下一步 Wave 4

- 四个并行 worktree 全部完成并集成：F（M1 补遗，690596b）+ G（B4 后端，feb3a09）+ H（B4 客户端，0899ba6）+ I（B1 画像，1f958fe）；lessons.md 冲突 3 次均保留两边记录
- 集成接线（f85a393）：main.py 注册 thumbnails_router（G）+ index.uvue 接 UploadStatusBanner 一行（H）+ 新迁移 c7d8e9f0a1b2（I 遗留项：profile_l2_evidence FK 补 ON DELETE CASCADE，dev 实删用户验证级联生效）+ schema.sql 同步
- 顺手修复（用户授权）：lessons.py 台账日期固定 Asia/Shanghai（原 Python 运行时 localtime=UTC 慢 8 小时，标题日期混乱）+ 新增 docs/项目API密钥清单与获取.md（用户要求：必须写明白项目所需 API key 怎么获取、有哪些——config.py 为准全清单+获取途径+状态+别名，总纲挂链接）+ 登记对应教训
- 全量门禁：**420 passed**（350 基线 + F 29 + G 23 + I 25）+ 19 deselected + 覆盖率 78.46% + api_smoke 6/6 + research 18 场景，review_agent --full 全绿
- F 关键成果：LLM 精排第二层（仅真实判定换序，mock 原序）+ 托管护栏 qwen_response_check（moderate 托管优先 chat 兜底）+ 50 条真值评测集（hit_rate@3=0.8571；负样本误召回率 0.5714 真实缺口，待采集语料重校）+ 改写层 11/11
- G 关键成果：缩略图管线（PIL→thumbnail_key→GET 端点懒生成）+ upload_mode/on_wifi 流量约束 + 微信媒体下载→COS + 30 天清理 job + COS 开通验证文档
- H 关键成果：sync_client 字段级同步（六字段队列/op_id 幂等/增量拉取/reconcile/2h 定时）+ 流量约束（WiFi 原图/蜂窝暂缓）+ 指数退避 + 批量暂停/一键继续 + UploadStatusBanner（真机待补项归 Wave 4）
- I 关键成果：枚举集 JSON 收尾入 git（L0 51 维全补 values_detail + L1 193 维 phrase/disclosure）+ profile_schema 加载器 + annotate 真实/mock 同构 + profile_annotator（双门槛/池/节流/查重/历史裁剪/证据锚点）+ 钩子接线 + 冷启动兴趣稀疏 5-10 维
- 待 key/环境：COS/微信企微/Sentry 未配（代码先行 mock 测）；托管护栏实网验证待 key；B/C/D 采集语料落地后重跑评测基线；纠错测量需真实 correction_log 数据；真机 nova 11 补验（H 的 WiFi 原图/蜂窝暂缓完整相册链路 + 后台 2h 定时归 Wave 4 K）
- 下一步：Wave 4（Agent J B5a 客户端 / Agent K B5d Android / Agent L M3 微信），任务卡 docs/parallel-dev/10/11/12
