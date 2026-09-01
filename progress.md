> 📌 **当前状态速览（2026-08-29）**：收尾 Wave 1–4 全收口（17/17），真机 7 清单全达终态+补验。终值：**用户故事 ✅46/🟡7/❌0 · A 级 32 条 · 性能门禁 10/2/6 · 30s ✅6.0s**。
> 数字唯一现行口径 = `AGENTS.md`「当前状态」节；术语/决策/待拍板 = `docs/决策台账.md`；缺陷台账（19 单：D-01~D-16、D-18、D-19、D-21）与环境事件（O-1/O-2）= `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4/§5。
> **下一步 = 4b 修复批次（执行计划已定：`docs/4b修复批次执行计划_20260829.md`，P-0 拍板五项全落、P-1 隔离工作区已建）**：批次1 D-18/D-19（重打包复验）→ 批次2 D-16/D-07/D-08＋散单 D-05/D-10/D-14/D-21（08-29 拍板并批）→ 批次3 S2 校准（卡真值）+D-06（**等价复现验收，无需第二设备**，台账 §1.8）。
> 本文件为**时间线日志**（新旧混排属历史演进），新条目追加在**末尾**；历史条目只读保留，仅加 [勘误] 注记。与速览冲突的旧数字以速览为准。

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

## 2026-08-27 17:40 · 重构批次 H 集成（H1-H5 全并行 5 分支）

- **merge 顺序**（均从 18078e5 切出，`--no-ff`）：h1（models 拆包/event_aggregation 脚本迁移/pipeline 注册表/wechat 反转/PushChannel/upload 拆包）→ h2（CI 增强）→ h5（客户端收口）→ h3（测试杂项）→ h4（API 契约收口）
- **共享工作树事故处理**：H3 首个 commit ddc8e29 误落 h4 分支（与 h3 的 232632d 等价）——两版仅 test_auth.py 不同（h4 含 R4#11 断言改造=超集）；已按 h4 版处理 test_auth（3 处 AUTH_099→010/011 取 h4，手机号 uuid 修复保持），test_error_registry/test_upload 取 h3 版（含 unit marker / M1 DoS 用例）
- **merge 冲突处理**：test_queue.py 双 add 被 git 拼接成整块重复（F811 重复 fixture）→ 取 ruff 修正版（h3 133 行）；lessons.md 三处（h1/h3/h4）保留双方；test_error_registry add/add 取 h3
- **openapi 重导**：H4 API 变更后按 docs/OpenAPI契约.md 命令重导，46 路径（arbitrate 已迁 /classify，契约文档已对齐）；errors 纯增 AUTH_010-013/MSG_003 零删除
- **门禁**：全量非 rag **661 passed / 20 deselected**（基线 632→661，+29）＋ client tsc --noEmit EXIT=0 ＋ 快速门禁 EXIT=0；client 域 H5 无 pytest 靠 tsc+grep
- **教训登记**：merge 双 add test 文件被拼接成整块重复——merge 后必跑全量 lint；写含中文文件一律用 git checkout 而非 PowerShell Set-Content（编码破坏教训复现）
- **遗留**：HBuilderX 编译真机冒烟（H5 客户端行为等价，集成后补跑）；pip-audit-weekly 定时触发待 CI 观察；client tsc 非阻断试点
- 推送：develop @ HEAD（openapi.json + progress + lessons 集成提交），CI 复验中

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

## 📌 历史快照 · 当前状态（2026-08-20）——已被 2026-08-28 收尾终版取代（见文件头速览）

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

## 收尾 Wave 1 集成完成（2026-08-27 21:45）——下一步：A3/D1 补做 + 性能 P0 + 真机补验

- 基线编译修复（39734fe）：UTS 5.15 全量编译 7 处存量错误（upload_protocol/uploader/event_ops/play/event_sync/sync_client/record.uvue）——resolve 模式 + 具名函数 + 显式类型锚；此前被增量编译 warm cache 掩盖，全量重编译即暴露（用户疑问根因）
- 八分支集成：B3（list_objects/HMAC/COS 直连验证）→ B1（孤儿扫描 12 测试）→ B2（time_suspect/export/copy_library，678 passed）→ C1（压测报告）→ C2（文案库 40 条池）→ C3（隐私/归档/harness）→ A1（画像管理页）→ A2（设置页/导出/契约 hub）；lessons.md 冲突 5 次均保留双方
- 集成接线（617028c）：main.py 注册 export router（B2 契约需求）；OpenAPI 重导 47 路径（46 零消失 + /api/v1/export + time_suspect 入 schema）
- 集成后门禁：pytest 707 passed / 20 deselected（B1 fail-safe 用例适配 B3 已实现 list_objects 的现实）；客户端 9 页面全量编译通过（ready 148s）
- 真机 nova 11（21:43）：应用启动成功，onLaunch 3.5s、ensureLogin true（adb reverse 隧道）、首页 onReady 453ms；画像管理页可达（635ms）、设置页可达（244ms，group-title view→text 样式修复）
- 遗留：A3（时间存疑+纠错提示 UI）与 D1（真机 7 清单）未开工；压测 P0 未修复（DB 池 15 耗尽进程崩溃/SEARCH_CONCURRENCY=4 硬顶/BGE-M3 加载健壮性）；旧队列照片 upload/init 422（旧测试数据，新上传 api_smoke 验证正常）；设置页截图与导出闭环待用户目检
## 收尾 Wave1 A3/D1 补集成完成（2026-08-27 23:59）——下一步：Wave 3 真机补验

- 补集成 2 分支：wrap1-agentA3（merge d45898d：SuspectBadge 角标组件 + index.uvue 日卡片/待确认卡两处接入 + record.uvue 连续纠错弹层接线 + text_recorder.ts 本地计数 + timeline.ts 最小承接）+ wrap1-agentD1（merge 6aea242：真机补验 7 清单 + README 前置总表 P1–P11 + adb_helpers.ps1 证据脚本，新增 930 行）；merge-base 在主轮之后，文件域零重叠，双 merge 零冲突、无 lessons 冲突
- A3 必要偏差裁决（INT）：timeline.ts 属总表"全员只读"，A3 需承接后端 time_suspect 字段——类字段默认 false 不改构造函数签名、字段名走 contract.ts 常量 FIELD_TIME_SUSPECT 只读引用、L1 分组拷贝保留标记，向后兼容零回归 → **采信**
- 集成后门禁：G1 pytest 712 passed / 20 deselected（基线持平）；G2 review_agent --full 全绿（syntax 258 文件 / lint / secrets / tests+api_smoke）；G3 客户端 --cleanCache 全量编译通过（198 class，SuspectBadge easycom 自动注册解析、onCorrectionSuccess 编进产物，cli exit 0）；G4 OpenAPI 静态核对 47 路径、基线 46 零消失、/api/v1/export + time_suspect 在（本轮零后端改动不重导）；G5 涉改域回归由 G1 覆盖（events/notify/storage 全绿）
- 治理文档入库：docs/parallel-dev-收尾/ 19 份任务卡/规则/总表（此前长期 untracked）
- 环境事故（已恢复）：C 盘 0 空闲 → ENOSPC 连锁故障（所有工具调用失败 + pytest 假死 exit 1）——pip cache purge + npm cache clean + 清 C:\WINDOWS\TEMP 过期项释放 9GB+ 后重跑全绿；教训已登记 lessons
- A3 自验补记（audit 全文披露）：模拟器 E2E 已过——角标两态（true 显示/false 不渲染）+ D2 弹层文案 + 3 次纠错→D4 弹层→确认清零全链路（logcat+UI 树双证据，a3-evidence/ 存档）、状态机镜像测试 9/9；遗留：nova 11 真机冒烟归 Wave 3（checklist_06）；D4 弹层"确认"后无自定义分类管理页（18 号契约只定义弹层）→ 当前 toast"即将上线"，页面需求待产品拍板；dismissCorrectionPrompt()（取消=不再提示）hook 已就绪未接线，待产品决策

## 收尾 Wave 4a 收口登记（2026-08-28）——代码侧记录闭环；真机证据归 Wave 3/4b

- 交付物：`忆述光华_交付文档/MVP完成度评估_20260827/08_收尾波次完成汇报_20260828.md`——4a 骨架版（◉§0/§1.1/§1.3/§1.4 完整版；○§1.2/§1.5 真机占位 [待 4b]；铁律：无 nova 11 实测不宣称 A 级）
- 代码侧成果概览（全量与逐项溯源见 08 §1.1）：编译修复 39734fe（UTS 7 处存量错误）→ 十分支集成（主轮 8：5e1463b B3 / 794deae B1 / d119444 B2 / 6bf945f C1 / 5396b1e C2 / 98857df C3 / d97999b A1 / 6e2bbd0 A2；补轮 2：d45898d A3 / 6aea242 D1）→ 接线 617028c → P0-2 性能修复 6e7c6d7（DB 池耗尽转 503 / 搜索并发转 429+Retry-After / 模型加载健壮性；效果复验=远期待办 F1，本条不宣称达标）
- 新页面收口：画像管理（US-40/41）/ 设置页+导出（US-42）/ 时间存疑角标（US-12）/ 纠错提示弹层（US-25）；后端契约：/api/v1/export + time_suspect + copy_library 加载机制（OpenAPI 47 路径零消失）
- 文档/脚本资产：孤儿扫描（backend/app/workers/orphan_scan.py，12 测试）/ 文案库 40 条默认+骨架池（docs/copy_library/）/ 隐私政策定稿+部署就绪包（docs/隐私政策_定稿_20260828.md、docs/部署就绪包_20260828.md，待审阅）/ 负样本重校 harness（scripts/eval_negative_samples.py）/ 残留归档（backups/20260827_残留归档/）/ 压测报告（docs/压测报告_20260828.md）/ 内测包构建配置清单（docs/parallel-dev-收尾/20，新增交付物：三层根因+M1–M7+注入方案）
- 门禁快照：pytest 712 passed / 20 deselected；review_agent --full 全绿（syntax 258 文件）；client --cleanCache 198 class 编译通过
- feature_list 登记：代码侧 evidence 追加 6 条（F1 孤儿扫描 / F5 压测+P0-2 / F7 导出闭环 / P2-ECHO 文案库 / VERIFY 编译修复+门禁 / OPS-SECRETS B3 密钥-COS）；画像 UI/US-12/US-25/D1 清单已由 Wave2 INT 登记（76a4dbf），不重复
- 状态列收口：任务卡 01–14→已集成；15→待用户执行（Wave 3）；16/21→进行中；19→已执行（00/17/18/20/22 不在清单内不动）
- 红线自检：本波零代码改动（models.py/migrations/client/backend 未触碰）；15 号内容未读写结果；真机记录段未写；与 Wave 3 协调者登记以"不同条目/不同 key"天然隔离
- 4b 待填空位（22 号）：§1.2 证据升级表 / §1.5 快照真机数字 / feature_list 真机 A 级 evidence / 30s 计时门禁判定
- commit 偏好（用户已确认）：4a 不 commit 不 push，工作区保持与 Wave 3 并行可合并

## 收尾 Wave 3 真机补验——清单 01 通过（2026-08-28 01:06-02:53）

- **清单 01（蜂窝链路+同步横幅，US-48/46/47）✅ 通过**：四步全过——①WiFi 10 张全链 done（contents+10、taken_at=EXIF 真值 08-22）②蜂窝 20 张注入 contents 增量 0（`蜂窝网络：只传缩略图+元数据`/`蜂窝/离线暂缓`日志在场，held 累积）③横幅手动"立即上传原图" +20（双次点击幂等无重复，队列清零横幅消失）④断网 1 张暂缓→WiFi 恢复钩子零点击自动补传（`WiFi 恢复，自动补传暂缓原图（held=1 failed=0）`），终态 34/34 done、0 永久 failed（强于预设 21 条口径——基线含 3 条 wave 前遗留）
- **当场修复三缺陷（commit 00a9b08，含 4 条 lessons + 新回归测试）**：D-01 客户端 upload init 422（幂等键含设备路径非法字符→sanitizeKeyId；证伪 Wave2"旧测试数据"误诊，影响全量照片+语音上传）/ D-02 enqueue_unique 迁移丢任务实参（9f0b2f4 回归，8 调用点，管线自 08-27 静默断链）/ D-03 分片上传路径缺 EXIF 权威回填（下沉 services/exif.py 子 IFD 优先 + 管线单点回填）
- **新遗留缺陷登记**：D-04 端侧 L1 卡日期不随服务端 EXIF 回填（清单 04 定性）/ D-05 横幅 emits 无宿主监听→手动补传照片不补端聚合（云侧 L2 兜底实证：1409c71a start_time=2026-08-22 08:00 EXIF 真值出卡；drain 失败路径 held/failed 双登记无去重）——修复移交 Wave4
- **证据**：`scripts/realdevice/evidence/ck01_step1_*.log` + `ck01_step2_*.log` + `ck01_step34_*.log` + 截图×6（1a/2a/3a/3b/4a/4b）+ 注入 manifest；清单记录表已回填（checklist_01 §7）；跟踪表 19 §4 缺陷单/§5 环境事件同步登记
- **环境/通道事实**（跟踪表 §5 全文）：RQ worker 此前从未运行（D-02 静默一天的土壤）+ Windows embeddable python ._pth 忽略 PYTHONPATH/cwd + with_scheduler Windows 崩（dev 用无 scheduler work()）+ [yishu] 日志只进 HBuilderX 会话不进 logcat（GrabAppLog 通道废弃，改会话落盘）+ MediaStore 同路径行复用扩到"历史用过目录名"（w3a-d 全新前缀规避）+ 每轮 launch 循环必丢 adb reverse（协调者固定补）
- 07 报告升级影响：§8.1④ 蜂窝/横幅/断网项 待真机 → **A 级（nova 11 实测）**；30s 门禁（§7.1）不动，归清单 03
- 下一清单：02 录音中断恢复（需第二设备呼入或闹钟抢占，P6 用户协同）

## 收尾 Wave 3 真机补验·全波终局（2026-08-28 18:00 · 7 清单全达终态）

- **记分板**：01 ✅ 34/34 全链（凌晨，详见上节）｜ 02 ❌ 中断恢复（闹钟/相机两路抢麦均不触发 onInterruptionBegin——D-06；来电待补 P6）｜ 03 ✅ **30s 门禁真机过线：首批 6.0s ≤30s**（50 张全量 61.2s）｜ 04 ✅ L2 真实 qwen 双独立轮铁证（标题非确定性+窗口真值+50/50 无错并），L3 数据不满足待补 ｜ 05 🟡 转写 **A 级**（4/4 语义命中，同音错=真 ASR）+ 情绪**实况 A**（真基座录 UI 直显「难过」conf 0.840/sensevoice_local；S1 平静 0.830 ✓、S2 开心漏报 0.496 待 C 批校准）+ guardrail 真通道 ｜ 06 🟡 四度编译+装包启动全成（onLaunch 3.5-5.3s）、FATAL 0 命中；①②中文 IME adb 注入不可=工装限制转人工 ｜ 07 🟡 **云打包链路 A 级全通**（真 AppID `__UNI__2650A2A`→pack 18.2s+排队 9min→APK 23.6MB/SHA256 存证→纯净模式拦装[环境]→`--playground custom` 真基座 17:43:40 启动+**sync pull 30 changes 端云直连**）
- **本波挖出的产品级新缺陷（移交 4b，按修复批次）**：
  - **批次1 原生能力对（07 判死，正式包同样中招）**：D-18 WorkManager 探测恒 false（`getResource('.class')` 在 Android 永 null——dex 尸检证明类全在包里，B5d 后台唤醒/周期/退避全废只靠 setInterval 假活）；D-19 DataSyncService 无 manifest 注册 + FOREGROUND_SERVICE 权限缺失（FGS 保活任何基座必死，"标准基座自动回退"注释掩盖全基座失效）
  - **批次2 语音链**：D-16 情绪三层默认值把"未测出"伪造为"平静"（notify 门控永不触发，老人场景高危）；D-07 短录音音频不落 COS→管线 AUDIO_NOT_FOUND 全判死（05 轮 5 连复现）；D-08 转写失败即弃段（02）
  - **批次3 模型/数据**：S2 开心类声学漏报校准（样本已留 ck05_emotion_replay jsonl）；D-06 中断回调机型适配；D-12/D-13 已关单，D-14（离线丢传永久丢失）D-15（L1 title_source 语义）在列
  - 完整 19 单台账见 tracker 19 §4（D-01~D-16、D-18、D-19、D-21；D-17 未启用，D-20 系补验修复单 f726942 不入表）
- **环境事故与修复（诚实披露）**：本轮曾为补装 SenseVoice 依赖搞挂全局 python（pip 文件锁半卸载态+resolver 回溯死循环 ~25min），已完整恢复（numpy2.5.2/scipy1.18/librosa/funasr-onnx/modelscope 全链，SenseVoice 模型 D 盘缓存），过程 3 条 lessons 登记；服务中断 15:05→15:38 期间的设备上传靠端侧队列自愈（B4 韧性意外加分）。HBuilderX 每次 GUI/打包操作互杀 adb reverse（本日 11 次），固定由协调者重建
- **4a 收口动作**：auth.ts 已还原 dev-client（编译复验中）；tracker 19/lessons/本条随收口 commit 落库；progress/feature_list 真机 A 级登记完成即 4b 解锁；设备测试目录 4a 清理（云端测试数据留 4b 复验用，随后再清）


## 收尾 Wave 完成（2026-08-28 4b 终版）——全部收口，宣布收尾波次完成

- **快照终值**：功能代码 ~90%（代码侧缺口清零）/ 内测可达度 60–70%（三座大山不变）/ 用户故事 53 条 **✅41/🟡12/❌0**（A 级真机 **27 条**，+8）/ 性能门禁 18 项 **达标 10/部分 2/未达标 6** / **30s 计时门禁 🟡→✅（实测 6.0s≤30s）**
- **真机 7 清单**：01✅（US-46/47/48→A 级）02❌（D-06 中断回调）03✅（30s 门禁过线 6.0s≤30s）04✅（US-06/07→A 级真实 qwen）05🟡（US-17/18/19→A 级转写/情绪实况，S2 待校准）06🟡（编译冒烟 A，①中文 IME 转人工）07🟡（云打包链路 A + 原生能力 D-18/D-19）；证据 `scripts/realdevice/evidence/` 50 文件
- **Wave 3 新缺陷移交 4b 修复批次**：批次1=D-18（WorkManager 探测恒 false·正式包后台永不启用）/D-19（FGS manifest 未注册·保活全基座必死）；批次2=D-16（情绪"平静"伪造默认）/D-07 五连复现（短录音不落 COS 判死）/D-08（转写失败即弃段）；批次3=S2 开心类漏报校准 + D-06 机型适配；全 19 条台账见 tracker 19 §4
- **交付物**：08 收尾波次完成汇报（终版：§1.2 证据升级表 + §1.5 快照终版 + §0 一页结论 + 附录 4 空位销项）；feature_list F1/F3/VERIFY 真机 A 级 evidence（f5c0c59）；状态列全目录收口（15/16/21/22 → 已完成）
- **遗留（等待团队，映射 07 §8.1）**：① 产品部 B/C/D 真值 + A 批负样本 + E/F/G 排期 + 正式文案库 ② 运营/合规 三申请 + 隐私政策签字 ③ 负责人 企微/微信/短信/uni-push + 内测包 M1/M4/M6 ④ AI 远期待办 00 §7 F1–F7（F1 性能复测优先）

> **[2026-08-29 整饬勘误]** 上条（收尾 Wave 完成 4b 终版）为补验前时点快照（✅41/🟡12、A 级 27）：随后补验 US-42（D-20 修复 f726942）与 US-12/25/40/41（6a7f0f9，cbc1751 同步 08/AGENTS）🟡→✅、A 级→**32**。现行唯一口径 = AGENTS.md「当前状态」节。

## 2026-08-29 · harness 台账整饬（进度/说法/决策统一 + 错误经验汇编）

- **动机（债务清单）**：多窗口追加致台账漂移——①同一数字五个台账三个值（✅41 vs ✅46、A27 vs A32、"D-01~D-19" vs 实表含 D-21）②"Wave 4"双义（开发期 J/K/L vs 收尾归档 4a/4b）③session-handoff 头部滞留 08-25 ASR 会话"当前状态"④设计决策散在 handoff/progress 无登记簿。
- **交付**：新建 `docs/决策台账.md`（§0 术语消歧：开发 Wave/收尾 Wave/4b 修复批次、F 前缀三族、D/O/等级/门禁三族；§1-3 基线后拍板 30+；§5 待拍板 6 项）；新建 `docs/lessons-主题索引.md`（131 条台账归 10 个根因族，族1 门禁卫生 ~19%、族7 数字口径债=本次直接起因）；`AGENTS.md` 状态节刷新+五处同步纪律+Windows 归二期标注；`docs/lessons.md` 环境陷阱区补录 24–33（真机期）；`session-handoff.md` 重写为现行交接（历史压缩，原文在 git）；`feature_list.json` VERIFY 终值口径+F6 过时说法补注；`init.sh` 检查清单挂台账。
- **验证**：快速门禁 `python scripts/review_agent.py` EXIT=0（纯文档波零代码改动；全量门禁随 4b 修复批次落地后统一复跑）。
- **事故与自纠（诚实记录）**：本会话 `lessons.py add` 因我给表头加指针行触发其「旧文件无表头→重建」分支，将台账 1199 行毁至 28 行并随 a6bb5a9 提交（diff stat -1191 暴露）→ HEAD~1 恢复+重放三处增补；add() 改**不销毁三分支**（本会话新教训 01:30 即其回归用例）；另 edit 工具落盘 CRLF 曾毁 init.sh 可执行性→二进制还原 LF+新建 `.gitattributes`（`*.sh/*.py eol=lf`）。主题索引因此新增族11（工具自毁）、§4.8 入决策台账。
- **授权销项（同日续）**：用户拍板「授权你同步。过时的文档，空的旧的文件夹一律删」→ ①08 三处+02 全表同步终值（顺带修正 6a7f0f9 漏回填 02 的 US-12/25/40/41/42/48 六行+统计行+半通清单，02 顶部加同步注记）；②删 4 份孤立执行类文档（PR1_审查报告/RAG管线审查/RAG指标提升/生产兜底审计，先做引用核查：代码/测试/交付文档 live 引用的 46+ 处候选全部保留——refactor-plan·技术债三件套·批次F-H 提示词等）；③拔除 .wt 13 个旧 worktree+backend/.wt，清空目录 464 个（uploads 测试残留 300+/APK 尸检根树/truth-data 占位）；④backups/20260827_残留归档按 D8 拍板保留；⑤docs/README 索引二轮更新+旧「不删除」规改为「live 引用保留/授权删」；台账 §4.9/§5.6/AGENTS/handoff 同步。


## 2026-08-29 04:3x · 4b 执行计划制定 + P-0 拍板闭环 + P-1 隔离工作区建成

- **产出**：`docs/4b修复批次执行计划_20260829.md`——tracker19 移交单中 **12 张"你+我"可修**（D-18/19/16/07/08/10/14/05/21/12/09 遗留/D-15）+ O-1/O-2 只读诊断，波次=P-0 拍板→P-1 worktree→P-2 诊断→R1 纯代码（11 项，队列域固定序 D-14→05→08）→R2 云打包+真机复验（2 轮打包预留）→R3 收口；关键路径=D-18/D-19→R2（正式包判死项解除）；S2/凭证/合规/文案库本体=团队阻塞不排。
- **P-0 拍板（用户答复）**：①D-15=**改标 'device'** ②取消弹窗=**永久不再提示**（接 dismissCorrectionPrompt hook）③散单=**并批次2** ⑤O-1=**只读诊断先行，动环境等通知**。
- **④D-06 调研定案（用户指定方向）**：无需第二设备——等价复现三级：相机抢麦脚本（`evidence/ck02_grab_trigger` 08-28 彩排过，FIRE/TAP/RELEASE 全自动）+ `am start -a android.intent.action.CALL -d tel:<短号>` 去电模拟（nova 11 有卡，Telecom 抢占与来电同源）+ 看门狗单测；定性=Android《共享音频输入》平台政策（ck02_external_corroboration 官方文档摘录），watchdog+显式标注即正解；真来电降为可选加测。
- **回填**：决策台账 §1.6 ✅并批 / 新增 §1.8 D-06 等价路径 / §5.3·§5.4 销项（待拍板 5→3）；tracker19 D-15 行→"已拍板待修"；计划文档五处 ✅。
- **P-1**：`.wt/fix4b` worktree（分支 fix/4b @ e80176f）+ backend/.env 拷贝 + models 就位（junction）——R1 全程在 worktree 内，主工作区（他窗 164 文件）零触碰；R2 云打包亦在 worktree 出包，防他窗未提交改动进包。
- **文案库基调拍板（用户连答三项）**：①基调维持「温和克制」②禁止主动提及 10 项照单通过 ③现有默认库升格**内测正式基线 v1** → 台账 §5.5 销项（待拍板 3→2，仅剩部署就绪包+目标页）、§3.3 刷新、AGENTS US-21 卡点改「仅 D-16」；validate_copy_library 无需改（拍板即现状定稿）。

## 2026-08-29 14:0x · R1-a/R1-c 落地 + P-2 诊断收编（第三窗发现与编译门挂起）

- **R1-a（D-18/D-19）代码落地 fix/4b `8cb15b4`**：探测改 nativeResources 标记资产（AssetManager.list 免异常）；FGS service+权限迁**工程根 client/AndroidManifest.xml**（真根因：云打包不合并 UTS 插件内 manifest，ask.dcloud 214927 实证+插件内双 manifest 加教训注释）。**伴生抓获**：`.gitignore:121` 通配 `AndroidManifest.xml` 吞工程根真源（`!!` 实测；疑即 08-28 Agent K 走位之谜成因）→ 豁免三连+check-ignore 复验。修复过程自伤一次：dedupe 脚本把 .gitignore 提交成 9 行（.env 忽略全丢）——commit stat 自审拦截，母版重建+amend，lessons×4 登记在案。
- **R1-c（D-16）代码落地 fix/4b `f1f8a3c`**：三层默认值拆穿（未测得=None 贯穿 models/backends/schema；EMO_UNKNOWN→None；mock 不伪造；笑声提升兼容 None；端侧 null→'' chip 自然隐藏+主导情绪空提示守卫；契约口径 emotion 可空）。**验证**：test_asr 47 + emotion_consume + notify + pipeline 合计 **89 全绿**（pytest，worktree 内）。lint E501 一拦一修（ids 折行）过闸。
- **基线编译真相（lesson 已录）**：`--cleanCache` 冷编译暴露 **develop 干净基线在 HBuilderX 5.24 下编不过**（upload_protocol/uploader 等 `.then` 返回类型推断错误群发）——此前所有「编译通过」皆跑在含修复的脏工作区。他窗旧脏（08-27/28，132 文件）正是这批迁移修复；R1-a 连带做了两枚**同形热修**（merge 时以他窗最终版为准）。**客户端编译门自此挂起，等他窗 5.24 迁移提交**——已列入协调请求。
- **第三活跃窗发现**：`.wt/wrap1-agentA2-ui-restore`（分支 18 分钟前仍提交，`fix(uts) 移除 java.lang.Class 导入`）——与我修 **D-18 同一插件文件**、且其旧版仍含恒 false 探测与 Class.forName 群（5.24 迁移未决）。两窗并撞一单，归谁待用户裁定；我侧 marker 重写在其旧基线之上，merge 冲突可控但须表态。
- **P-2 收编**：O-1 改判（漂移证伪→疑外部终止/内存压力，1.6GB 空闲实测在案）；O-2 坐实→**tracker19 D-22 新单**（缺陷台账 19→20 单，AGENTS/台账 §5.7 并批待拍板同步）；tracker19 §5 补上缺失的 O-2 行（原「唯一来源」竟无此行，记账债+1）。US-25 现 ✅A 与 D-22 现象矛盾——R2 须重断（已写入 §5.7）。
- **纪律执行**：全程 pathspec 提交零触碰他窗脏文件；主仓脏文档=本批记账+前批拍板文案（一并入库）；无 push；scratch 暂存 .cowork-temp 波尾清理。

## 2026-08-29 15:0x · 拍板三连 + R1-d 后端半收官 + 企微可信 IP/凭证事件

- **拍板三连（用户，ask_user_question 全选推荐）**：①**客户端 UTS 5.24 迁移归第三窗**——fix/4b 让位客户端域，全部客户端半冻结待 rebase；②**D-22 并批批次2** 确认（§5.7 销项，待拍板回到 2 项）；③**D-09 遗留单腿暴露=登录态 GET /api/v1/asr/channels**（G2 最小 healthz 纪律不破）。登记台账 §1.9。
- **R1-d 落地三枚（fix/4b）**：`81424fb` D-22 后端半（degraded 标记+active 回写 content_class+ArbitrateRequest 契约字段+测试常量向量盲区→hash 派生+degraded/回写双新用例）；`6f43e87` D-15（sync 改标 'device'，schemas/sql 注释同步）；`9922e4a` D-09 遗留 channels 端点（401 反证默认登录态+泄漏负断言）。**91 测试全绿**（correction/classify/asr/event_sync/event_ops）。门禁插曲：UP012 一拦一修+lessons 登记。
- **企微事件（负责人→用户→我）**：可信 IP=调用方出口 IP——实测本机 `61.171.241.17`（手机电信流量 CGNAT，会漂；校园网必变，换网即 `curl ip.sb` 报新 IP 可多配）。凭证 5 项：CORP_ID/TOKEN/AES_KEY 就绪，APPID/SECRET 待打包——US-31/32/33 卡点性质缩为「等打包+M1 服务器」（台账 §6/handoff 已登记 614a4df）。
- **协作通知**：用户另派一 agent 做 DASHSCOPE 后端补充任务（独立分支）——已提醒避开 `api/asr.py`/`services/external/asr/`/`docs/openapi.json`（asr 域是我 R1 主战场；契约再生成归 R3 统一）。若其提交与 9922e4a channels 冲突，以契约增量合并为准。
- **R1 剩余 = 全部冻结项**，等待第三窗迁移提交 → rebase → 统一编译 → R2 真机波。
## 2026-08-29 14:3x · 百炼真实链路加固 + 验证矩阵（独立分支 feat/dashscope-backend-hardening，worktree `.wt/dashscope`）

- **背景**：用户通知 DASHSCOPE 双 Key 已在 Infisical 就绪（会话中恢复登录，token 9d+），要求完成需要这两个 key 的后端补充开发。域协调：全程避开他窗 in-flight 的 `api/asr.py`/`services/external/asr/`/`docs/openapi.json`。
- **两处加固**（08-28 真实评测报告实证反推）：①`llm_ops/rerank.py` 解析三级兜底——标准解析失败→逐块正则打捞（截断尾块/全角标点，ans 未知前缀宁不判）→`_norm_ans` 中英文布尔归一，根治 `bool("false")==True` 静默错误换序 + 「解析失败回退原序」精排空转；②`rag/image.py` VL 重试耗尽→**过期缓存兜底**（08-28 评测以图搜图 2/10 miss=连接重置空结果），空 caption 不覆写缓存位。新增单测 7 项（rerank 形态 4 + caption 兜底 3）全绿。
- **真实验证矩阵**（新 `scripts/check_dashscope_matrix.py`，9 链路）：**双通道各 9/9 pass**——Infisical 注入（14:24）与 .env 直读（14:23）：rewrite 1.1-1.3s / route 224-239ms / rerank 判定 4/4 置顶正确 0.73-2.25s（追加样本共 4 次调用解析零失败）/ guard chat+managed 双路径 / fail_closed 拒发实证 / **Qwen3-VL 真实照片 3.2-4.2s** / caption 缓存+过期兜底 / event_merge real conf 0.85。**零 403 workspace 复发**（lessons 08-25 旧病）。OPS-SECRETS「拿 Key 零代码切换」百炼域达成——全部由既有生产代码路径直出。
- **验证与登记**：默认套件 **664 passed / 4 skipped**（EXIT=0；首轮 1 例 test_amap 偶发=与前台子集并发共跑测试库时序污染，复跑不复现，纪律入报告 §6）；ruff 涉改文件全绿；lessons 登记 1 条（LLM 输出契约漂移静默退化族）；证据 `docs/百炼真实链路验证与加固_20260829.md` + `.cowork-temp/dashscope_matrix_*.json`；feature_list F5/OPS-SECRETS evidence 追加。
- **遗留登记（不擅改默认值）**：rerank 默认开 + 真实档 0.7-2.3s/查询对 P95<3s 的张力→GPU/异步策略评审再拍板；fun-asr 模型开通态+WER 基线属他窗 asr 域；answer_quality「真实生成答案」接线为下一候选。

## 2026-08-29 15:5x · DASHSCOPE 补充任务验收合入 + wrap1 迁移窗侦察

- **feat/dashscope-backend-hardening 验收并 merge 进 develop（`5ed7c5c`）**：F5 百炼真实链路两处加固（精排解析三级兜底 + VL 过期缓存兜底）+ `scripts/check_dashscope_matrix.py` 9 链路真实矩阵（**Infisical 注入与 .env 直读双路径 9/9**；LLM 精排 4/4 judged_n 正确、0.73–2.25s）+ 默认套件 664 绿；我方独立复跑 test_image_search+test_rag 34 passed/15 deselected ✓。**文件域纪律良好**（未碰 asr 域/openapi.json，报告 §4 自证）。append 区冲突两处（lessons/progress）双保留解冲；merge 后代码域与源分支 parity 零差异；被 merge 阻挡的 `rag/image.py` 他窗旧脏已抢救至 `.cowork-temp/salvage/image_py_main_dirty_20260829.patch`（1KB，可回放）。
- **wrap1-agentA2-ui-restore 侦察（`efb183d`，未合）**：**5.24 迁移实质在该分支落地**（photo-watch 21 处 android 导入改 any / background-tasks SharedPreferences 修复 / CSS 选择器迁移 / Vapor main.uts 入口 /「编译成功+全页面截屏正常」）；且**顺带做了 D-21 四页 scroll-view 修复**。碰撞面：两插件 index.uts（撞我 R1-a D-18 重写）、record.uvue（撞 R1-c 守卫+D-22 客户端半）、schemas/event.py（不同区域，轻）、.gitignore（追加区）。**卫生问题**：~90MB 二进制入库（SarasaGothic 字体 ×3=69MB + 截图 13 张 + hero 原图 4.5MB）+ deploy_log ×4 + design_data/13k 行——且基线停在 677ea68（缺我全部 R1/记账/此 merge）。**结论：不可按现状 merge**，需先整备（二进制/日志/design_data 去留拍板）。
- **fix/4b 策略**：暂不再 rebase（等 wrap1 处置定局后一次到位：迁移版上传链 + D-18/D-21/D-22 客户端半在统一基线上重放，同形热修两枚让位）。内存仍 ~1GB，编译窗口未到。

## 2026-08-29 16:3x · 暗物质审计：两单「修了没入库」补落 + 主区旧脏三重备份清空

- **触发**：合入 dashscope 分支时主区唯一脏文件挡路 → 按拍板③做全量清空（salvage 快照分支 2bad295 三父含 untracked + patch + 112 文件直拷；PS 把 `stash@{0}` 花括号吞了致 stash 假 drop，SHA 从 drop 日志复活——教训已登记）。
- **审出**：①**D-02/D-03 修复从未提交**——committed develop 里 8 个 enqueue key-only 调用点带活雷（净基线照片管线 worker 必 TypeError），D-03 的 exif.py 全新文件压根不在 git，当年「真机复验 ✅」全跑在脏树上；②**文案库 v1**（08-29 拍板引用的 docs/copy_library/ + service + notify 接线）同样零入库。
- **补落 fix/4b 三枚**：8a0b27a（exif.py+pipeline+photo_content+register+test_pipeline，24 绿）→ 7589775（文案库五件+validate 校验器+notify 纯回退接线，7/7 绿）→ acce7ef（contents+wechat 收全 8/8 调用点+真值源合一，26 绿）。分支现 @ acce7ef。
- **其他**：dashscope 分支验收并 merge（5ed7c5c，双路径 9/9+复跑 34 绿）；evidence/「证据留本地」铁律 .gitignore 恢复入库；主区 `import app.main` 冒烟 55 路由 OK；内存告警 0.65GB（暂停重活）。


## 2026-09-01 · W10 峰宝九条验机反馈全落地（AI mock 数据 + 附件入口 + 画像页四改 + security 页冲突清理）

- **背景**：峰宝人肉验机九条暴击（网络异常复发/AI 页无数据/画像页无数据/两页 SVG 丑/旧玻璃 TabBar/页头 44px/功能冲突/附件无入口），全部认领当日落地；详细对照表与差异降级登记 `_diff_ledger.md` §W10.4。
- **网络异常（skill 修正）**：双根因实锤——多版本 adb 互杀（server 重启灭全部 reverse）+ monkey 拉活≠重启（网络栈失败态不复位）；`uvue-deploy-device-ops` skill 禁忌表旧「1.0.41」口径清除 + deploy_one.sh step2/3 顺序反转（reverse 先于启动 → force-stop 冷启动）+ FAIL 分支补全量日志输出（tail -4 吞编译错误描述盲区）。
- **AI 对话页 mock（contract-first）**：业界调研五篇共识落成 mock 契约（`POST /api/v1/chat/messages → {reply}`，kind=bubble/plain+chips/cards/confirm/typing，B2 接线时 mock 层整体可删）；ai.uvue 全量重写 ~640 行——seed 8 条覆盖五形态族、onSend 本地闭环（typing 1.8s→四形态轮换）、附件面板浮层+入口钮；`USE_MOCK_CHAT=true` 显式登记（对齐 USE_MOCK_TIMELINE/USE_MOCK_DATA 先例）。
- **画像管理页**：seed 三条敏感话题（id904 forbid/id905 mention/id906 review）GET 复核通过；TabBar 组件接入（删自绘玻璃 tabbar+emoji FAB）；页头 44→60px（padding-top 115.4rpx）；emoji→SVG 三枚。
- **账号与安全页**：删「数据导出」「存储空间」两行+方法（功能冲突，归宿=未来存储与备份页）；三 emoji→SVG。
- **uvue 编译失败一轮（实锤两枚新坑）**：type 字面量构造 `MemCard(title:...)` 与函数默认参数 `= []` 全项目零先例编译器不认 → class+constructor+new / 全参显式；顺带修 seed plain 调用参数错位（bullets 塞 cards 位）+ CSS `.a.b`/后代+`:first-child` 三处。教训沉淀 ardot-to-uvue-css-restore「UTS 编译器坑」节。
- **画布同步三板 + 终审**：AI 输入条附件钮（clip SVG，发送钮右位）、画像页页头下移+新 TabBar 按 TabBar.uvue 规格重建（SVG 图标族+「我的」锈红 active）、security 页删两行（后续组 y-88 间距恒定）+三 SVG；截图终审通过；uvue 附件钮初版序写反已对照画布纠正。画布坑三枚沉淀 ardot-canvas-pitfalls（BACKGROUND_BLUR 写入拒绝且清空全字段/SVG rect+A 命令不支持/纯水平线段零面积 degenerate）。
- **推包三轮收口（2026-09-01）**：ZS8sLT 编译失败（已修，见上）→ hqSTjw 编译同步实际成功但 `OUT=$(cli ...)` 命令替换被 HBuilderX.exe 继承 stdout 管道 EOF 永不来僵死 23 分钟（TaskStop 终止；deploy_one.sh step1 改后台+mktemp 重定向+10s×150 轮询，坑沉淀 uvue-deploy-device-ops 禁忌表）→ **fOeJLH 轮询版首战 1m57s 三步全绿**（同步成功/reverse 非空/force-stop 冷启动）。设备已跑最终版产物；真机复核清单交峰宝人肉验机（AI 页五形态+附件面板在发送钮右侧/画像页新 TabBar+seed 三条+60px 头/security 页单行+SVG）。遗留：四份画布快照（ai/ai_reply/portrait_manage/account_security）下会话开工前按 W9 重导。

