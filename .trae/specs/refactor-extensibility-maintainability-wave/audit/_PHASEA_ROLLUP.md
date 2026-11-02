# Phase A 逐功能域审计 · 统一汇总（ROLLUP）

> 波次：易扩展/易维护重构波 · Phase A 只读筛查。本文件是 14 份 `domain-*.md` 的**唯一汇总读数**，不产生新结论、不改任何业务代码或域报告。
> 生成日：2026-09-24 ｜ 汇总口径源自 `audit/domain-01..14-*.md` 各自表格 + 定点复核（PowerShell `Select-String`）。
> 基线：分支 `develop`；14 份报告一致记录 `git status --short` 仅 `?? .codebuddy/` + `?? .trae/.../audit/`（**在途业务文件 = 0**；AGENTS 所述「第四窗媒体票据 8 文件在途」在本时点**均已入库**）。

## 0. 读数总览

| 指标 | 值 |
|---|---|
| 域数 | 14（①认证设备 ②上传媒体 ③内容记录 ④事件聚合 ⑤检索RAG ⑥回响胶囊消息 ⑦画像访谈纠错 ⑧同步离线 ⑨微信 ⑩护栏安全 ⑪观测错误 ⑫客户端壳UI ⑬脚本部署 ⑭harness工具） |
| **条目总数** | **249** |
| 结构类条目 | **185** |
| 功能与安全缺陷 | **64** |
| 严重度分布 | P0 **17** ｜ P1 **111** ｜ P2 **121** |
| 状态分布 | 确认（有直接代码证据）绝大多数；候审（启发式待人工确认）**30** 条 |
| 优先处置清单（P0+P1） | **128** 条 |

**分类规则（本汇总自定义，可复现）**
- **结构类**＝巨文件 / 重复 / 死码 / 边界依赖 / 声明漂移 / 门禁缺口 / 注册表与扩展成本——即"不改行为即可处置"的结构债（含扩展风险型 P0，如 D05-3/D02-2）。
- **功能与安全缺陷**＝有**运行时正确性/安全性后果**者（数据丢失 / 错值 / 500 / 丢操作 / 跨用户 / 伪造 / 污染 / fail-open 放行 / 幂等失效 / 软删口径导致可见性错误 / 未接线导致功能不可达）。本波**只登记不修**。
- 位置列**省略公共前缀** `backend/app/`（`services/...` 即 `backend/app/services/...`）；`client/`、`scripts/`、`deploy/`、`backend/scripts/`、`backend/tests/` 保留。**来源列指向 domain-NN 报告**。

---

## 1. 结构类条目（185 条）

| 编号 | 域 | 位置(文件:行) | 类型 | 严重度 | 状态 | 一句话证据 | 来源 |
|---|---|---|---|---|---|---|---|
| D01-1 | ① | `services/auth/auth.py:300-309`/`:368-377` | 重复 | P1 | 确认 | TokenPair 构造 10 行逐字重复两处 | domain-01 |
| D01-2 | ① | `services/auth/auth.py:54-58`/`:61-71`/`:74-81` | 重复 | P1 | 确认 | 三渠道登录函数同构（复制粘贴扩展范式） | domain-01 |
| D01-3 | ① | `services/auth/providers.py:242-244`/`:256-258` | 重复 | P2 | 确认 | phone/sms-mock resolve_identity 同语义（有意图重复） | domain-01 |
| D01-4 | ① | `services/auth/otp_store.py:61-68`/`core/ratelimit.py:97-104` | 重复 | P1 | 确认 | 滑窗限流内存双实现、Redis 侧分歧 | domain-01 |
| D01-5 | ① | `services/auth/auth.py:398-400`/`providers.py:159-167` | 重复 | P2 | 确认 | 无盐 SHA-256 双实现 | domain-01 |
| D01-8 | ① | `client/utils/contract.uts:18,22,23`/`auth.uts:221,269` | 声明漂移/死码 | P1 | 确认 | 3 个 PATH_AUTH_* 零消费、登录封装硬编码绕过常量 | domain-01 |
| D01-9 | ① | `client/utils/api.uts:57,322,400,450`+`play.uts:542,605`+`audio_player.uts:317`+`upload_pipeline.uts:88`+`upload_protocol.uts:74` | 重复 | P1 | 确认 | Authorization 注入 9 处复制体，统一 header 抽象未收口 | domain-01 |
| D01-10 | ① | `schemas/auth.py:55-56`/`core/config.py:44-45` | 声明漂移 | P1 | 确认 | 令牌有效期硬编码(7200/2592000)与 settings 无联动 | domain-01 |
| D01-11 | ① | `services/auth/__init__.py:8,42` | 死码 | P2 | 确认 | `_issue_tokens` 零调用导出 | domain-01 |
| D01-12 | ① | `core/errors.py:50,136` | 死码 | P2 | 确认 | AUTH_099 登记但全仓零 raise | domain-01 |
| D01-13 | ① | `api/auth.py:86,92`+`services/auth/auth.py:152,164,192` | 声明漂移 | P2 | 确认 | AUTH_006 幽灵码被 5 处 docstring 引用（未登记/未 raise） | domain-01 |
| D01-14 | ① | `schemas/auth.py:64` | 死字段 | P2 | 确认 | `is_new_user` 恒 False、客户端零命中 | domain-01 |
| D01-15 | ① | `services/auth/providers.py:387-395` | 可扩展性 | P2 | 确认 | `get_login_provider` 为 if 链非注册表 | domain-01 |
| D01-18 | ① | `api/auth.py:36-38` | 边界 | P2 | 确认 | api 层为测试 re-export 服务层私有函数（`# noqa`） | domain-01 |
| D01-19 | ① | `client/utils/play.uts:585`→`security.uvue:74` | 边界 | P2 | 确认 | 账号页"当前用户"能力住在播放模块 | domain-01 |
| D01-20 | ① | `services/auth/otp_store.py:193` | 死码 | P2 | 确认 | `is_degraded` 仅测试消费 | domain-01 |
| D01-21 | ① | `schemas/auth.py:21`+`providers.py:372` | 可扩展性 | P2 | **候审** | platform 无取值域约束 | domain-01 |
| D02-1 | ② | `client/utils/voice.uts:391-493` vs `upload_pipeline.uts:147-189` | 重复/声明漂移 | P1 | 确认 | 上传状态机双实现（voice 无断点/无 4xx 分类） | domain-02 |
| D02-2 | ② | `api/media.py:51`/`schemas/content.py:9`/`api/contents.py:130`/`services/external/storage.py:406`(+`services/wechat/service.py:251`) | 注册表覆盖度 | **P0** | 确认 | 前缀白名单 4 套 + wechat/ 命名空间漏网 | domain-02 |
| D02-3 | ② | `services/external/storage.py:428,440-441,448,466-483`+`core/config.py:89`+`deploy/.env.production.template:84`+`api/upload.py:264` | 注册表/端口覆盖度 | P1 | 确认 | 新增存储后端需改 7 处（含 API 层硬编码 COS） | domain-02 |
| D02-7 | ② | `services/upload/protocol.py:76-79` vs `services/photo_content.py:249` | 声明漂移 | P2 | 确认 | `photos/` 键布局两套（带/不带 {yyyymm}/） | domain-02 |
| D02-8 | ② | `services/upload/register.py:143`+`backend/scripts/seed_echo_today.py:96` | 重复 | P2 | 确认 | 媒体键构造第 4/5 处独立实现 | domain-02 |
| D02-9 | ② | `services/external/media_url.py:53-64,144`/`upload_meta.py:26`/`file_magic.py:15-38` | 硬编码枚举漂移 | P2 | 确认 | 扩展名表双向不一致（.heif/.gif） | domain-02 |
| D02-10 | ② | `api/contents.py:257`/`services/upload/register.py:76` vs `vector_store.py:37-38` | 硬编码枚举 | P2 | 确认 | content_type 白名单 2 处内联、已有常量表未复用 | domain-02 |
| D02-11 | ② | `services/upload/protocol.py:53-55` | 死码 | P2 | 确认 | `staging_prefix` 零调用 + 注释指向错误使用者 | domain-02 |
| D02-12 | ② | `services/file_magic.py:21-24`/`upload/__init__.py:27-28`/`client/utils/upload_protocol.uts:41`/`uploader.uts:59,327,331` | 零调用导出 | P2 | 确认 | 导出面大于消费面（逐符号计数） | domain-02 |
| D02-13 | ② | `api/upload.py:264-273`+`services/external/tencent_ci.py:20-30` | 边界/依赖方向 | P2 | 确认 | API 层直读 COS 私有配置、第 2 处 COS 客户端 | domain-02 |
| D02-14 | ② | `services/external/media_url.py:130-135` | 边界 | P2 | **候审** | `key_belongs_to_user` 无法判定时放行 | domain-02 |
| D03-1 | ③ | `api/contents.py:85,89,570,571` | 巨文件 | P1 | 确认 | 4 router 混居 902 行 | domain-03 |
| D03-2 | ③ | `contents.py:539`/`:711`/`events.py:140`/`rag/recall.py:111`/`capsules.py:63` | 重复 | P1 | 确认 | 缩略图签票 5 份同语义实现 | domain-03 |
| D03-3 | ③ | `text_recorder.uts:78`/`voice.uts:337`/`capsule_api.uts:52,85` | 重复 | P1 | 确认 | 客户端 POST /contents 四份复刻 | domain-03 |
| D03-4 | ③ | `contents.py:574` vs `event_ops.uts:135`/`play.uts:500` | 双通路/死码 | P2 | 确认 | 详情端点零生产调用，客户端仍走旧法 | domain-03 |
| D03-5 | ③ | `pipeline_ext/payload.py:66`/`pipeline_ext/__init__.py:28`/`schemas/content.py:102`/`classifier.py:103` | 死码 | P2 | 确认 | 4 项零调用导出（各 1-2 命中） | domain-03 |
| D03-8 | ③ | `contents.py:142,268,285,310,357,367,411,700`（全仓 44 处 app 口径） | 重复 | P1 | 确认 | 软删过滤手抄（本域 8 处） | domain-03 |
| D03-9 | ③ | `pipeline_ext/sensitive.py:53-63` vs `:66-84` | 重复 | P2 | 确认 | 同谓词两查（候选集扫两遍） | domain-03 |
| D03-10 | ③ | `pipeline_photo.py:36-48` vs `ai_tagging.py:171-190` | 重复 | P2 | 确认 | COS→临时文件两份实现 | domain-03 |
| D03-11 | ③ | `client/components/RecordSheet/RecordSheet.uvue:1-2208` | 巨文件 | P1 | 确认 | 2208 行单体组件（≥6 职责） | domain-03 |
| D03-12 | ③ | `schemas/content.py:9,13`/`contents.py:130,257`/`pipeline.py:226-228`/`notify.py:83`/`search_api.uts:90-101` | 可扩展性 | P1 | 确认 | 新增类型需改 11 处+2 注释（注册表覆盖度≈1/4） | domain-03 |
| D03-13 | ③ | `schemas/content.py:114`→`contents.py:561` | 边界 | P2 | 确认 | 业务常量（保留期）落在 schema 层、与客户端文案双源 | domain-03 |
| D03-14 | ③ | `contents.py:517`/`events.py:366`/`messages.py:22` | 命名冲突 | P2 | 确认 | `_to_out` 三实现（非真重复） | domain-03 |
| D04-3 | ④ | `agg_types.py:52,66,67`/`st_dbscan.py:118-122`/`client/utils/agg/st_dbscan.uts:147` | 死码+声明漂移 | P1 | 确认 | `AGG_CONFIG["night"]` 从未被读取（装饰性配置） | domain-04 |
| D04-7 | ④ | `services/events/aggregate.py:174-185`/`edit.py:200`/`timeline.uts:470`/`time.uts:dayKey` | 重复 | P1 | 确认 | 日分桶 4 套语义 | domain-04 |
| D04-8 | ④ | `api/events.py:140-165`/`:168-221`/`:284-309`/`:333-363` | 重复 | P1 | 确认 | 图片 URL 组装三写 | domain-04 |
| D04-9 | ④ | `api/events.py:168-221`×`:333-363` | 重复 | P1 | **候审** | `_batch_event_photos` 与 `_batch_photo_ids` 同语义双实现 | domain-04 |
| D04-10 | ④ | `agg_preprocess.py:101-103` | 死码 | P2 | 确认 | `_cell_center` 恒等映射空抽象 | domain-04 |
| D04-11 | ④ | `services/events/aggregate_write.py:368-373` | 零调用导出 | P2 | 确认 | `_write_upper_events` 零调用 shim | domain-04 |
| D04-12 | ④ | `client/utils/agg/agg_config.uts:19` | 零调用导出 | P2 | 确认 | 客户端 WALK_SPEED_MS 零调用 | domain-04 |
| D04-14 | ④ | `event_aggregation/run_validation.py:126-129` | 门禁缺口 | P1 | 确认 | 断言恒真（常量比常量），双跑门禁自我欺骗 | domain-04 |
| D04-15 | ④ | `scripts/gen_agg_fixtures.py:158-165`+`client/utils/agg/fixtures.uts` | 门禁缺口 | **P0** | 确认 | 13 用例未覆盖 approx/corrected 分支 | domain-04 |
| D04-16 | ④ | `api/events.py:100` | 边界/依赖方向 | P1 | 确认 | API 层越级直连 event_aggregation.pipeline | domain-04 |
| D04-17 | ④ | `event_aggregation/st_dbscan.py:31` | 声明漂移 | P2 | 确认 | `tags: list[str] = None` 类型标注说谎 | domain-04 |
| D04-18 | ④ | `services/events/aggregate.py:44` | 声明漂移 | P2 | **候审** | `_AGG_WINDOW_DAYS=30` 与 L3_ACTIVE_DAYS 同值不同源 | domain-04 |
| D05-1 | ⑤ | `services/rag/__init__.py:135-141`,`:149-154`,`:163-168` | 死码 | P1 | 确认 | `intent=="mixed"` 三分支不可达 | domain-05 |
| D05-2 | ⑤ | `schemas/search.py:10` | 死码 | P2 | 确认 | `SearchQuery.intent` 全库零读取 | domain-05 |
| D05-3 | ⑤ | `vector_store.py:337-398`×`rag/pg_fallback.py:50-66`+`pipeline_ext/payload.py:72-77` | 重复 | **P0** | 确认 | 过滤翻译双实现 + 未知键静默丢弃（无 else） | domain-05 |
| D05-4 | ⑤ | `rag/recall.py:55`×`rag/pg_fallback.py:39` | 重复 | P2 | 确认 | 词元切分正则逐字符相同两份 | domain-05 |
| D05-6 | ⑤ | `services/rag/__init__.py:54-79` | 零调用导出 | P2 | 确认 | 8 项 `__all__` 再导出零外部消费 | domain-05 |
| D05-7 | ⑤ | `llm_ops/base.py:27-32`+`llm_ops/__init__.py:16,24` | 零调用导出 | P2 | 确认 | `rewrite_query`/`route_query` 门面转发零调用 | domain-05 |
| D05-8 | ⑤ | `services/rerank.py:49-76`,`:79-103`+`core/config.py:168,176` | 门禁缺口/死路径 | P1 | 确认 | 第一层 rerank 恒不可达且零测试（模型目录仅 README） | domain-05 |
| D05-9 | ⑤ | `core/config.py:161`×`services/rerank.py:3-4,57`×`scripts/warm_hf_models.py:23` | 声明漂移 | P2 | 确认 | 模型名三处不一致 | domain-05 |
| D05-10 | ⑤ | `services/rag/__init__.py:232`×`client/utils/search_api.uts:104-135` | 死数据 | P2 | 确认 | `llm_rerank_reason` 客户端零消费 | domain-05 |
| D05-11 | ⑤ | `client/utils/search_api.uts:151-166`×`:215-226` | 重复 | P2 | 确认 | 10 参 hit 映射手抄两份 | domain-05 |
| D05-12 | ⑤ | `scripts/build_image_index.py:77-112`等 | 硬编码魔法串 | P2 | 确认 | `yishu_benchmark` 26 处/6 文件无常量 | domain-05 |
| D05-13 | ⑤ | `rag/rewrite.py:15`/`rag/image.py:44,52`等 | 边界/依赖方向 | P1 | 确认 | rag 越级直连 external（llm_ops 门面被架空） | domain-05 |
| D05-15 | ⑤ | `schemas/search.py:37,41`+`scripts/eval_image_search.py:4`+`rag/__init__.py:6` | 声明漂移 | P2 | 确认 | 注释/契约与实现不符多处 | domain-05 |
| D05-16 | ⑤ | `services/correction.py:17-18`,`:34-50`,`:57-58` | 边界/抽象覆盖 | P1 | 确认 | 绕过 VectorStore 自建第二套 Qdrant | domain-05 |
| D06-1 | ⑥ | `api/capsules.py:45`/`api/deps.py:84`/`api/messages.py:37` | 同语义三实现 | P1 | 确认 | 归属 loader 三分裂 | domain-06 |
| D06-2 | ⑥ | `backend/tests/test_authz_gate.py:47` vs `api/capsules.py:45` | 门禁缺口 | P1 | 确认 | 门禁登记名与实际符号不匹配（哑条目） | domain-06 |
| D06-3 | ⑥ | `services/echo.py:152`（死）vs `:239-254`（活） | 死码+双实现 | P1 | 确认 | `_is_sensitive` 零调用，同语义内联 | domain-06 |
| D06-4 | ⑥ | `api/capsules.py:157-167`/`workers/capsule_scan.py:56`/`CapsuleSheet.uvue:138` | 到期判定三实现 | P1 | 确认 | 到期语义三处编码、含本地↔UTC 口径差 | domain-06 |
| D06-6 | ⑥ | `services/echo.py:30-149`+`api/contents.py:466-511` | 职责边界错位 | P1 | 确认 | 画像敏感 CRUD 住 echo 服务、由 contents 路由消费 | domain-06 |
| D06-7 | ⑥ | `api/messages.py:100-104`,`:122-128`,`:138-142` | 重复 | P2 | 确认 | 已读口径三处重复 | domain-06 |
| D06-11 | ⑥ | `api/messages.py:122-128` | 重复 | P2 | 确认 | mark_read 由 1 次往返变 2 次（SELECT 未用） | domain-06 |
| D06-12 | ⑥ | `client/pages/messages/messages.uvue:155-177`+`MessageDetailSheet.uvue:57-71` | 重复 | P2 | 确认 | 客户端 msg_type→展示映射两套 | domain-06 |
| D06-13 | ⑥ | `workers/capsule_scan.py:35`,`:83-89` | 边界 | P2 | 确认 | 模块级节流状态跨进程失效 | domain-06 |
| D07-2 | ⑦ | `services/interview.py:49-84` vs `docs/画像维度枚举集_l0.json` | 重复 | P1 | 确认 | 访谈关键词表与 JSON aliases 双实现且不一致 | domain-07 |
| D07-6 | ⑦ | `client/utils/text_recorder.uts:171-186` | 契约对齐 | P1 | 确认 | 客户端 DTO 不解析 `degraded`（当前无消费方） | domain-07 |
| D07-13 | ⑦ | `services/profile_schema.py:107-110` | 硬编码枚举 | P1 | 确认 | validate() 硬编码 l0_count!=51/l1_count!=193 | domain-07 |
| D07-14 | ⑦ | `profile_annotator.py:335-351`+`play.uts:155-172`+`export.py` | 重复 | P1 | 确认 | 三套 dimensions 读路径 | domain-07 |
| D07-15 | ⑦ | `classifier.py:27-32`+`api/corrections.py:36`+`RecordSheet.uvue:709` | 注册点分散 | P1 | 确认 | 新增标签需改 6 处（自定义标签被静默跳过） | domain-07 |
| D07-18 | ⑦ | `services/interview.py:134` | 死参数 | P2 | 确认 | `_activate_l1_interests` 的 db/user_id 零使用 | domain-07 |
| D07-19 | ⑦ | `api/corrections.py:38`+`services/correction.py:156` | 硬编码枚举重复 | P2 | 确认 | source 枚举散落 3 处 | domain-07 |
| D07-20 | ⑦ | `api/contents.py:89`+`main.py:120` | 域边界混居 | P2 | 确认 | `/api/v1/profile` 前缀路由定义在 contents 模块 | domain-07 |
| D07-21 | ⑦ | `services/correction.py:279,341` | 依赖方向 | P2 | 确认 | 函数级导入 classifier（无真实循环） | domain-07 |
| D07-22 | ⑦ | `client/pages/portrait/manage.uvue:310-329,420-454` | 硬编码枚举/魔法数字 | P2 | 确认 | 处置档位/配色/伪置信度硬编码 | domain-07 |
| D07-23 | ⑦ | `db/session.py:30-34` | 事务口径一致性 | P2 | 确认 | `get_db` 只 close 不显式 rollback | domain-07 |
| D08-4 | ⑧ | `client/utils/sync_client.uts:240-254` | 能力缺口 | P1 | 确认 | 字段级无本地载体（field/value 无人消费） | domain-08 |
| D08-10 | ⑧ | `client/utils/sync_client.uts:584`（及 `:64,82,617`） | 死码 | P2 | 确认 | `stopPeriodicSync` 真死 + 9 项导出面冗余 | domain-08 |
| D08-11 | ⑧ | `client/utils/retry.uts:26-32`×`uploader.uts:430`/`sync_client.uts:373` | 死参数 | P2 | 确认 | `isFatal` 回调恒 false、分支不可达 | domain-08 |
| D08-12 | ⑧ | `client/utils/uploader.uts:119-135,252-269`×`queue_store.uts:61-79` | 重复 | P2 | 确认 | 行分隔存储读写三套同构 | domain-08 |
| D08-16 | ⑧ | `client/utils/event_ops.uts:29` | 反向依赖 | P2 | 确认 | event_ops→uploader（仅为 getNetKind） | domain-08 |
| D08-17 | ⑧ | `services/sync.py:243`×`sync_client.uts:326-335` | 零消费字段 | P2 | 确认 | `server_version` 客户端 0 命中 | domain-08 |
| D09-6 | ⑨ | `gateway.py:97,109-116`·`service.py:123-125,255,268,311,340,357`·`models/wechat.py:19` | 可扩展性缺口 | P1 | 确认 | 新增消息类型需改 9 处、无注册表 | domain-09 |
| D09-9 | ⑨ | `services/wechat/service.py:63,67`+`ports.py:64` | 死码/零调用导出 | P2 | 确认 | 端口反转只落一半（set_media_gateway 零调用） | domain-09 |
| D09-10 | ⑨ | `backend/sql/schema.sql:402` vs `db/models/wechat.py:22` vs `service.py:247..377` | 声明漂移 | P2 | 确认 | status 枚举/默认值三方不一致 | domain-09 |
| D09-11 | ⑨ | `services/wechat/service.py:311-312` | 死码 | P2 | 确认 | HTTP 路径不可达的重复防线 | domain-09 |
| D09-13 | ⑨ | `core/ratelimit.py:41-46` | 门禁缺口 | P2 | 确认 | /wechat/callback、/find 无独立配额 | domain-09 |
| D09-14 | ⑨ | `services/external/media_url.py:53-64,138-144` | 声明漂移 | P2 | 确认 | 微信语音 .amr 无映射（与域② D02-9 同源） | domain-09 |
| D09-15 | ⑨ | `deploy/RUNBOOK.md:108`+`deploy/.env.production.template:158-161` | 门禁缺口 | P2 | 确认 | 部署文档无回调 URL/EncodingAESKey 步骤 | domain-09 |
| D10-3 | ⑩ | `api/asr.py:26`/`api/contents.py:292`/`photo_content.py:175`/`content_safety.py:125` | 注册表/端口覆盖度 | P1 | 确认 | 4 条入库主链绕过策略选择器（托管护栏未覆盖） | domain-10 |
| D10-4 | ⑩ | `llm_ops/guard_managed.py:143-159` | 死码/重复 | P1 | 确认 | `moderate_managed` 零生产调用 + 同语义双实现 | domain-10 |
| D10-11 | ⑩ | `api/asr.py:125-133`+`content_safety.py:313` | 死码/口径 | P2 | **候审** | /guard/check 零生产调用、ProviderLiteral 零引用、护栏拒绝码三形态 | domain-10 |
| D11-1 | ⑪ | `api/capsules.py:39-42,51,97,100,162,184` | 声明漂移（漏登记） | **P0** | 确认 | CAPSULE_001..004 未登记进 errors.py（4 枚） | domain-11 |
| D11-2 | ⑪ | `backend/tests/test_error_registry.py:20-47` | 门禁缺口 | P1 | 确认 | AST 门禁只看 ERR_* 常量，跨模块定义静默漏 | domain-11 |
| D11-3 | ⑪ | `api/export.py:80-93` vs `core/ratelimit.py:203-214` | 重复+魔法串 | P1 | 确认 | 429 信封双实现 + "RATE_LIMITED" 字面量；export 未登记限流域 | domain-11 |
| D11-5 | ⑪ | `core/errors.py:50,58,65,136,142,149` | 死码 | P2 | 确认 | 死登记 3 枚（AUTH_099/CONTENT_004/CONTENT_011） | domain-11 |
| D11-6 | ⑪ | `classify.py:127,130`/`corrections.py:53`/`search.py:55` | 边界（跨域复用码） | P2 | 确认 | 码域名前缀与 raise 模块不匹配 | domain-11 |
| D11-7 | ⑪ | `pipeline.py:372,469`/`pipeline_audio.py:170,182`/`external/asr/models.py:42` | 注册表覆盖度 | P2 | 确认 | 内部错误码第三命名空间未登记 | domain-11 |
| D11-8 | ⑪ | `core/errors.py:10,123`+`tests/test_error_registry.py:70-76` | 门禁缺口 | P2 | **候审** | 登记 http 与 raise http 无一致性校验 | domain-11 |
| D11-9 | ⑪ | `main.py`（无 logging 配置） | 可观测盲区 | P2 | 确认 | API 进程无 logging 初始化 | domain-11 |
| D11-10 | ⑪ | `core/middleware.py:1-14` | 可观测盲区 | P2 | 确认 | request_id 未进日志上下文（ContextVar 零命中） | domain-11 |
| D11-11 | ⑪ | `pipeline_ext/payload.py:41,60`/`external/asr/audio.py:124`/`contents.py:118`/`queue.py:88` 等 | 可观测盲区 | P2 | 确认 | 静默 except（吞掉且无留痕）清单 | domain-11 |
| D11-12 | ⑪ | `main.py:50-53` | 可观测盲区 | P2 | **候审** | 后端 Sentry 依赖自动集成、无显式上报 | domain-11 |
| D11-13 | ⑪ | `client/utils/api.uts:281,310-311` | 注册表覆盖度 | P2 | 确认 | 客户端自造码 UNKNOWN/NETWORK 不在登记表 | domain-11 |
| D11-14 | ⑪ | `main.py:104-106` | 声明漂移 | P2 | 确认 | 中间件顺序注释漏列 add_security_headers 且层级错 | domain-11 |
| D12-1 | ⑫ | `client/design_tokens.json`×`client/**/*.uvue`（763 处色字面量） | 重复/令牌零覆盖 | **P0** | 确认 | 令牌机制覆盖率 0%、660/763 抄写命中、103 非令牌 | domain-12 |
| D12-2 | ⑫ | `design_tokens.json:12-16,30-31`×`TabBar.uvue:148,206` | 声明漂移 | P1 | 确认 | 令牌值本身已漂移（连抄写都在漂） | domain-12 |
| D12-3 | ⑫ | `client/**/*.uvue`（103 非令牌色+190 rgba+336 font-family） | 硬编码未收敛 | P1 | 确认 | 真缺口（含平台强制重复） | domain-12 |
| D12-4 | ⑫ | `design_tokens.json:6,7,13,17,18,32` | 死码 | P2 | 确认 | 6 项令牌零使用 | domain-12 |
| D12-5 | ⑫ | `scripts_ardot2uvue.py:27`+`gen_design_data_v4.py:20` | 门禁缺口/生成器不可复跑 | **P0** | 确认 | ROOT_DIR/DESIGN_DIR 硬编码本机路径，生成链不可复跑 | domain-12 |
| D12-6 | ⑫ | `uvue_gen/icons/*`(132)×`client/static/icons/*`(95) | 生成物双向漂移 | P1 | 确认 | 18 个同名图标内容不同（含 TabBar 全部 8 个） | domain-12 |
| D12-7 | ⑫ | `uvue_gen/audit_report.md:3,7` | 死证据 | P2 | 确认 | 引用不存在的 scripts_verify_layout.py | domain-12 |
| D12-8 | ⑫ | `uvue_gen/*_gen.uvue`(14)×`client/**` | 生成物快照分叉无门禁 | P1 | 确认 | 行数 delta 最大 +1567、不在扫描面 | domain-12 |
| D12-9 | ⑫ | `scripts/audit_harness.py:172-180,219-250` | 门禁缺口 | P1 | 确认 | 轴2 三向缺一向、生成物不在扫描面 | domain-12 |
| D12-10 | ⑫ | `shell.uvue:15-19,30,34-37,82-100`+`TabBar.uvue:5-30,70-119` | 注册表覆盖度 | P1 | 确认 | 新增 1 tab 需改 ≥6 文件 12+ 点 | domain-12 |
| D12-11 | ⑫ | `components/TabBar/TabBar.uvue:70-119` | 重复 | P1 | 确认 | 4 段同形 goX | domain-12 |
| D12-12 | ⑫ | `TabIndex.uvue:373-387`/`TabProfile.uvue:93-102`/`TabAi.uvue:460-464`/`TabSearch.uvue:121-125` 等 | 重复 | P1 | 确认 | Tab 组件契约样板重复 | domain-12 |
| D12-13 | ⑫ | 12 个 `pages/**/*.uvue` 的 `goBack()`×`manage.uvue:393` | 重复 | P1 | 确认 | 同语义 12+1 实现、缺 util | domain-12 |
| D12-14 | ⑫ | `utils/shell_state.uts:8`+`shell.uvue:93`+`TabAi.uvue:391-392` 等 | 死码/声明漂移 | P2 | 确认 | 只写不读 + 零生产者分支 | domain-12 |
| D12-15 | ⑫ | `TabIndex.uvue`1740/`RecordSheet.uvue`2208/`detail.uvue`1165/`TabAi.uvue`974/`favorites.uvue`974/`manage.uvue`891 | 巨文件 | P1 | 确认 | 已冻结基线 6 项，本域复核无新增 | domain-12 |
| D12-16 | ⑫ | `TabIndex.uvue:951-970`×`TabSearch.uvue:112-118,227-233`×`TabProfile.uvue:113-117`×`favorites.uvue:305` | 可扩展性缺口 | P2 | 确认 | 硬编码枚举 + 近义双实现 | domain-12 |
| D13-1 | ⑬ | `.github/workflows/ci.yml:73` | 门禁缺口 | P1 | 确认 | 客户端 tsc 门禁被 UTS 迁移掏空（0 文件=恒绿） | domain-13 |
| D13-2 | ⑬ | `scripts/test_auth_singleflight.mjs:17,26,87` | 死码/门禁缺口 | P1 | 确认 | 引用已改名的 auth.ts，测试恒不可用 | domain-13 |
| D13-3 | ⑬ | `deploy/scripts/check_env_template.py`（全） | 门禁缺口 | P1 | 确认 | 模板↔config 唯一判据但无任何门禁调用 | domain-13 |
| D13-4 | ⑬ | `scripts/audit_security.py:102-118` | 声明漂移 | P1 | 确认 | 安全审计查开发件+错误目录，RPO 检查恒通过 | domain-13 |
| D13-5 | ⑬ | `ci.yml`（全） | 门禁缺口 | P2 | 确认 | 多个审计/冒烟脚本仅人工跑 | domain-13 |
| D13-6 | ⑬ | `scripts/_merge_l0_refine.py:9-11,25,68` | 死码 | P2 | 确认 | 输入片段 0 个、跑则空转+覆盖主文件 | domain-13 |
| D13-7 | ⑬ | `scripts/_merge_l1_refine.py:13-15,93-96`·`_expand_l1_and_gen_inputs.py:6-9,76-87` | 死码/幂等 | P2 | **候审** | 一次性原地覆盖、断言硬编码、无 dry-run | domain-13 |
| D13-8 | ⑬ | `backend/scripts/reinject_missing_photos.py`·`seed_echo_today.py` | 幂等 | P2 | 确认 | 每次随机抽图 INSERT 新内容、不可复跑 | domain-13 |
| D13-9 | ⑬ | `scripts/_merge_l0_refine.py:9-11` | 硬编码路径 | P2 | 确认 | `D:\GuangH-App\docs\...` 绝对路径入库 | domain-13 |
| D13-10 | ⑬ | `reinject_missing_photos.py:25`·`seed_echo_today.py:31` | 硬编码路径 | P2 | 确认 | `C:\Users\ghf\Pictures\Screenshots` 入库 | domain-13 |
| D13-11 | ⑬ | `scripts/smoke_cos_upload.py:11` | 硬编码路径 | P2 | 确认 | B7 漏网绝对路径 | domain-13 |
| D13-12 | ⑬ | `scripts/backup_pg.ps1:15` | 硬编码路径 | P2 | 确认 | 默认 `D:\GuangH-App\backups` | domain-13 |
| D13-13 | ⑬ | `scripts/setup_pg.sql:13` | 硬编码凭据 | P2 | **候审** | dev 口令明文入库（非生产密钥） | domain-13 |
| D13-14 | ⑬ | `backend/scripts/wecom_sandbox.py:29` | 声明漂移 | P2 | 确认 | 注释 `Token=***` 而代码明文 | domain-13 |
| D13-15 | ⑬ | `deploy/scripts/preflight_pack.ps1:183` | 声明漂移 | P2 | 确认 | 查 client/ci-test.jks，真证书在仓外 | domain-13 |
| D13-16 | ⑬ | `scripts/smoke_cos.py:43` | 硬编码路径 | P2 | **候审** | SCREENSHOT_DIR 占位恒不存在→静默回退 | domain-13 |
| D13-17 | ⑬ | `deploy/scripts/backup_pg.sh` vs `scripts/backup_pg.ps1` | 重复 | P1 | 确认 | 每日 PG 备份两套（轮转/校验口径不同） | domain-13 |
| D13-18 | ⑬ | `bootstrap.sh:14-21`·`deploy_one.sh:22-32`·`healthcheck.sh:16-26`·`backup_pg.sh:23-38`·`pull_models.sh:17-27` | 重复 | P1 | 确认 | 部署脚本样板五份手抄 | domain-13 |
| D13-19 | ⑬ | `_merge_l0_refine.py` vs `_merge_l1_refine.py` | 重复 | P2 | 确认 | 合并逻辑单源 | domain-13 |
| D13-20 | ⑬ | `smoke_cos*.py`/`check_dashscope*.py`/`build_image_index.py` 等 ≥10 处 | 重复 | P2 | 确认 | 脚本引导头逐份复制 | domain-13 |
| D13-21 | ⑬ | `docker-compose.yml:149-153` vs `core/config.py:34-40` | 声明漂移 | P1 | 确认 | POSTGRES_HOST 覆盖失效、config 只认 DATABASE_URL | domain-13 |
| D13-22 | ⑬ | `deploy/Dockerfile.backend:39` vs L9-10 注释 | 声明漂移 | P1 | 确认 | 无 .dockerignore，COPY backend/ 会烘入 models/.venv | domain-13 |
| D13-23 | ⑬ | `Dockerfile.backend:45` vs `docker-compose.yml:159` | 声明漂移 | P2 | 确认 | USER appuser 而挂载点指向 /root/.cache | domain-13 |
| D13-24 | ⑬ | `deploy/scripts/bootstrap.sh:124-125` vs `systemd/*.service:21` | 声明漂移 | P1 | 确认 | 迁移读 backend/.env、运行读 deploy/.env | domain-13 |
| D13-25 | ⑬ | `deploy/RUNBOOK.md:151-161` | 门禁缺口 | P1 | 确认 | 声称系统 cron 但仓内无 crontab/timer 交付件 | domain-13 |
| D13-26 | ⑬ | `deploy/README.md:16-20` | 声明漂移 | P2 | 确认 | 目录树漏列 3 个脚本 | domain-13 |
| D13-27 | ⑬ | `scripts/e2e_agent_chat.ps1:46-58,12` | 声明漂移 | P2 | **候审** | 端口 8000 与 ONE_SWAP 8010 冲突、依赖排除域 | domain-13 |
| D13-28 | ⑬ | `scripts/eval_image_search.py:4` | 声明漂移 | P2 | 确认 | docstring `[image]` vs 代码 `["photo"]` | domain-13 |
| D13-29 | ⑬ | `scripts/realdevice/r2_4b_verify.ps1:5-7,57` | 硬编码路径 | P2 | **候审** | 硬编码设备序列号/.wt/fix4b/aapt | domain-13 |
| D13-30 | ⑬ | `backend/scripts/*`(10) 与 `scripts/*` 同名目录 | 边界方向 | P2 | 确认 | 两目录同名歧义、解析由 sys.path 顺序决定 | domain-13 |
| D14-1 | ⑭ | `scripts/review_agent.py:328-330`+`.github/workflows/ci.yml:48,370` | 门禁缺口 | **P0** | 确认 | 轴1–4 未进任何自动门禁（仅轴5+6 接入） | domain-14 |
| D14-2 | ⑭ | `scripts/audit_harness.py:406` vs `audit_harness_baseline.json:10-13` | 声明漂移 | P1 | 确认 | 基线 thresholds 是死配置（从不读取） | domain-14 |
| D14-3 | ⑭ | `scripts/audit_harness.py:489-496` | 门禁假阴性 | P1 | 确认 | 重复只 gate 总数不 gate 分布（可挪移绕过） | domain-14 |
| D14-4 | ⑭ | `scripts/audit_harness.py:429-447` | 门禁假阳/假阴 | P1 | 确认 | 白名单僵尸豁免不清算 + 改名即误报 CRITICAL | domain-14 |
| D14-5 | ⑭ | `scripts/audit_harness.py:288-289,305-306` | 门禁假阴性 | P1 | 确认 | 文件含"镜像"即整文件豁免全部类 | domain-14 |
| D14-6 | ⑭ | `scripts/audit_harness.py:111-113,123-125` | 门禁缺口 | P1 | 确认 | 轴1 只比路径/方法，字段级契约漂移不覆盖 | domain-14 |
| D14-7 | ⑭ | `scripts/audit_harness.py:22-23` | 门禁缺口 | P1 | 确认 | openapi 再生无脚本、无 CI 断言 | domain-14 |
| D14-8 | ⑭ | `scripts/review_agent.py:254` | 口径不一致 | P1 | 确认 | 覆盖率阈值 50 vs 60 两口径 | domain-14 |
| D14-9 | ⑭ | `scripts/review_agent.py:53-69` vs `audit_security.py:31-35` | 重复 | P1 | 确认 | 密钥模式集双实现且覆盖分歧 | domain-14 |
| D14-10 | ⑭ | `scripts/review_agent.py:356-362,394-397` | 死码/有效性 | P1 | 确认 | lessons 提示分支不可达 | domain-14 |
| D14-11 | ⑭ | `audit_harness.py:58-62`/`review_agent.py:40-43`/`test_agent.py:26-29`/`lessons.py:29-30`/`check_schema_drift.py:47-50`/`audit_security.py:27-28` | 重复 | P2 | 确认 | 6 份同语义 UTF-8 兜底 | domain-14 |
| D14-12 | ⑭ | `review_agent.py:81-91` vs `test_agent.py:42-59` | 重复 | P2 | 确认 | subprocess 包装单一实现 | domain-14 |
| D14-13 | ⑭ | `review_agent.py:94-110` vs `audit_harness.py:172-180,409-418` | 重复 | P2 | 确认 | 排除清单三处硬编码 | domain-14 |
| D14-14 | ⑭ | `scripts/audit_harness.py:221,232-237` | 门禁假阳性 | P2 | 确认 | 深路由/块注释误判 | domain-14 |
| D14-15 | ⑭ | `scripts/audit_harness.py:253-261` | 门禁假阳性 | P2 | 确认 | 注释内 .ts 提及误红 | domain-14 |
| D14-16 | ⑭ | `scripts/audit_harness.py:333-336,354` | 覆盖盲区 | P2 | **候审** | 导出面漏 type/interface/enum/default/子目录 | domain-14 |
| D14-17 | ⑭ | `scripts/audit_harness.py:294-296` | 门禁假阳性 | P2 | **候审** | 仅测试使用的模型/导出误红 | domain-14 |
| D14-18 | ⑭ | `review_agent.py:411-413`/`test_agent.py:209-211`/`audit_harness.py:35-39`/`check_schema_drift.py:316,368` | 退出码口径不一致 | P1 | 确认 | 四工具 0/1/2 各异、"环境错误"被吞 | domain-14 |
| D14-19 | ⑭ | `scripts/review_agent.py:246-247,259-260` | 门禁静默降级 | P2 | 确认 | ruff/pytest 缺失静默转绿 | domain-14 |
| D14-20 | ⑭ | `scripts/structured_report*.py`（不存在） | 声明漂移 | P2 | 确认 | 域⑭任务书范围项为幻影（全仓 0 命中） | domain-14 |
| D14-21 | ⑭ | `refactor-ledger.md:45`/`docs/决策台账.md:111` 引 `backend/tests/test_agent_schema_alignment.py` | 声明漂移/自校验缺失 | P1 | 确认 | 该守卫文件 git 全历史不存在 | domain-14 |
| D14-22 | ⑭ | `scripts/audit_harness.py:553-558` | 报告口径未统一 | P2 | 候选 | 与 review-report.json 两套 schema | domain-14 |
| D14-23 | ⑭ | `.github/workflows/ci.yml:444-481` | 门禁缺口 | P2 | 确认 | schema-drift job 仅 schedule+continue-on-error | domain-14 |
| D14-24 | ⑭ | `scripts/audit_harness.py:406,435` | 覆盖盲区 | P2 | **候审** | client 一刀切 800、行数含注释空行 | domain-14 |

## 2. 功能与安全缺陷（64 条 · 本波不修只登记）

| 编号 | 域 | 位置(文件:行) | 类型 | 严重度 | 状态 | 一句话证据 | 来源 |
|---|---|---|---|---|---|---|---|
| D01-6 | ① | `api/auth.py:99-101` vs `core/ratelimit.py:153-159` | 边界/口径不一致 | P1 | 确认 | 登录侧取 IP 不读 XFF → 反代后全体共享配额可被打满 429 | domain-01 |
| D01-7 | ① | `services/auth/auth.py:155,201` vs `api/deps.py:66` | 异常口径不一致 | P1 | 确认 | 坏 token 异常三处三种捕获宽度，意外异常被伪装/静默吞 | domain-01 |
| D01-16 | ① | `services/auth/providers.py:244,258`→`auth.py:282` | 语义漂移 | P2 | **候审** | devices.platform 列混入 android 与 phone 两套语义 | domain-01 |
| D01-17 | ① | `services/auth/providers.py:244,258` | 魔法字符串 | P2 | **候审** | 手机渠道 device_id 恒 "phone-login" → 多端互顶会话 | domain-01 |
| D02-4 | ② | `services/upload/protocol.py:265-277` | 异常/事务口径 | P1 | 确认 | commit 失败后重试 complete 触发未捕获 KeyError → 500（注释"可重试"不成立） | domain-02 |
| D02-5 | ② | `services/upload/register.py:144-148`+`:207-212` | 异常/事务口径 | P1 | 确认 | 先删旧键后 commit → 失败时两副本全删＝音频永久丢失 | domain-02 |
| D02-6 | ② | `client/utils/uploader.uts:202-229` | 门禁缺口/未接线 | P1 | 确认 | 蜂窝流量策略未生效（thumbnail/蜂窝恒 hold，不自动上传） | domain-02 |
| D03-6 | ③ | `contents.py:627,648,667,683,768` vs `590,610,882` | 边界 | P1 | 确认 | 畸形 ID 守卫不一致（5 端点预期 500 而非 404） | domain-03 |
| D03-7 | ③ | `schemas/content.py:13`/`contents.py:257`/`pipeline.py:226-228` | 声明漂移 | P1 | 确认 | article 有声明无处理器 → 静默"成功"（无分类/无标注） | domain-03 |
| D03-15 | ③ | `contents.py:300-301` vs `photo_content.py:180-184` | 口径不一致 | P2 | 确认 | 护栏 mask 分支在照片路径被丢弃、原文入库 | domain-03 |
| D04-1 | ④ | `event_aggregation/pipeline.py:95,161`+`client/utils/agg_runner.uts:91`+`scripts/gen_agg_fixtures.py:38,84` | 声明漂移（端云契约） | **P0** | 确认 | 云侧生产 tz=0、端侧传偏移 → L1 日卡片日期错位（门禁自我欺骗） | domain-04 |
| D04-2 | ④ | `agg_preprocess.py:23-93`×`client/utils/agg/pipeline.uts:36-49`+`gen_agg_fixtures.py:57-71` | 重复+端云漂移 | **P0** | 确认 | 云侧生产无去重、端侧做（夹具用 Python 复制品垫背） | domain-04 |
| D04-4 | ④ | `api/events.py:312-330` | 软删口径不一致 | P1 | 确认 | `_batch_counts` 缺软删/归属过滤 → 卡片"5 张"照片条只 3 张 | domain-04 |
| D04-5 | ④ | `services/events/aggregate_write.py:63`×`edit.py:98,151-154` | 铁律漏洞 | P1 | 确认 | merge/不带标题 confirm 只置 status 不置 title_source → 算法仍可追加 | domain-04 |
| D04-6 | ④ | `client/utils/timeline.uts:456-460,514-524` | 边界/功能缺口 | P1 | 确认 | buildDayGroups 无 level===3 分支 → L3 事件被静默丢弃 | domain-04 |
| D04-13 | ④ | `services/events/sync.py:68-72` | 边界/异常 | P1 | **候审** | 存在任意 cid=NULL 事件即整用户 cid=None 提交被判 duplicate | domain-04 |
| D05-5 | ⑤ | `rag/pg_fallback.py:46-49,74-83`+`rag/__init__.py:146-171` | 边界/一致性 | P1 | 确认 | 降级路径缺 status=="done" → 半态行被捞后再丢、有效条数<limit | domain-05 |
| D05-14 | ⑤ | `services/rag/image.py:106-138` | 边界/功能缺口 | P1 | **候审** | 以图搜图无类目/回退/rerank/降级（Qdrant 挂即空结果） | domain-05 |
| D05-17 | ⑤ | `rag/pg_fallback.py:68-72`×`rag/__init__.py:184-189` | 边界/异常 | P2 | 确认 | PG 兜底二次失败静默 return []，与"无命中"不可区分 | domain-05 |
| D06-5 | ⑥ | `client/utils/capsule_api.uts:23/31`+`deploy/RUNBOOK.md:151-161`+`workers/capsule_scan.py:83-89` | 注册点缺失 | P1 | **候审** | 到期提醒链路无触发入口（客户端零 GET /capsules，无 cron 登记） | domain-06 |
| D06-8 | ⑥ | `api/capsules.py:55-73`(+`:85`,`:136`) | 软删口径不一致+N+1 | P2 | 确认 | `_content_brief` 无 user_id/无 deleted_at 过滤 + 列表 N+1 | domain-06 |
| D06-9 | ⑥ | `services/echo.py:305-325` vs `notify.py:257-266`+`capsule_scan.py:61-76` | 通知失败口径三处不一 | P2 | 确认 | 复盘/到期为裸调无保护 → 真实推送抛错回滚整批 | domain-06 |
| D06-10 | ⑥ | `api/echo.py:42-52`+`services/echo.py:328-338` | dismiss 幂等 TOCTOU | P2 | 确认 | 先 count 后无条件 insert，交错时落 2 行（不占名额） | domain-06 |
| D07-1 | ⑦ | `deploy/Dockerfile.backend:39`+`docker-compose.yml:156-159`+`services/profile_schema.py:21-22,115` | 边界/部署缺口 | **P0** | 确认 | 镜像无 docs/ → 容器内 get_schema() 抛 FileNotFoundError（画像/访谈首调 500） | domain-07 |
| D07-3 | ⑦ | `services/pipeline.py:124-132` | D-22 同族未修点① | P1 | 确认 | 入库主链不检查 degraded → mixed 写成权威分类 + class_source="setfit" 伪造来源 | domain-07 |
| D07-4 | ⑦ | `api/corrections.py:64-69` | D-22 同族未修点④ | P1 | 确认 | 纠错回写 content_class 但不改 class_source/不重索引 Qdrant | domain-07 |
| D07-5 | ⑦ | `schemas/correction.py:29-36`+`api/classify.py:100-109`+`services/correction.py:292` | D-22 同族未修点③ | P1 | 确认 | content_id/preferred_label 契约增量被静默丢弃 | domain-07 |
| D07-7 | ⑦ | `client/pages/portrait/manage.uvue:236-249` | 伪造数据/展示缺陷 | P1 | 确认 | 展示英文维度 id + 纯按序号编造伪置信度 | domain-07 |
| D07-8 | ⑦ | `client/pages/interview/interview.uvue:85-89` | 语义错配/画像污染 | P1 | 确认 | 兜底三问文案与后端语义错配、key 保留 → 画像污染 | domain-07 |
| D07-9 | ⑦ | `client/pages/interview/interview.uvue:117,280-282` | 死状态/功能未接线 | P1 | 确认 | confirmation 全模板零渲染（B1-7 复述确认后端已通前端未接） | domain-07 |
| D07-10 | ⑦ | `client/components/PortraitPrivacyPanel/PortraitPrivacyPanel.uvue:82-100` | 隐私安慰剂 | P1 | 确认 | "清除画像"仅清 4 个本地 storage key，服务端不动 | domain-07 |
| D07-11 | ⑦ | `services/echo.py:59-70,103-111`+`db/models/profile.py:83` | 死字段/铁律未落实 | P1 | 确认 | `locked` 只写不读且可被静默解除 | domain-07 |
| D07-12 | ⑦ | 全后端无画像写端点（`services/interview.py:14`） | 铁律无载体 | P1 | 确认 | 画像主数据无人工确认/修改/锁定端点 | domain-07 |
| D07-16 | ⑦ | `services/correction.py:74-111`+`api/corrections.py:54-69` | 异常/事务一致性 | P1 | 确认 | Qdrant 成功而 PG 失败留幽灵点 + 单请求两次 commit | domain-07 |
| D07-17 | ⑦ | `profile_annotator.py:274-282`+`backend/sql/schema.sql:247-254` | 数据完整性 | P2 | 确认 | `_write_l2_evidence` 无唯一约束/去重 → 证据锚点重复膨胀 | domain-07 |
| D08-1 | ⑧ | `services/sync.py:155-185`×`api/contents.py:620-638` | 口径分裂（软删双轨） | **P0** | 确认 | push delete 不碰 contents.deleted_at → 离线删除后条目仍可见、30 天后突然物理消失 | domain-08 |
| D08-2 | ⑧ | `api/contents.py:626-638`×`workers/cleanup_job.py:87-95` | 承诺未落地 | **P0** | 确认 | REST 删除不写 DeletedLog → 30 天清理永不选中（UI 承诺未落地） | domain-08 |
| D08-3 | ⑧ | `services/sync.py:206-220`×`client/utils/play.uts:647` | 写入不投影权威表 | **P0** | 确认 | SFV 的 value 无投影回 contents → 离线改备注上云成功但永不可见 | domain-08 |
| D08-5 | ⑧ | `services/sync.py:196-205`+`222-240` | 幂等/变更日志污染 | P1 | 确认 | 冲突分支无 continue → 败方旧值写入 OfflineQueue 并下发他端 | domain-08 |
| D08-6 | ⑧ | `client/utils/sync_client.uts:451,464-471,556-560` | 分页未消费 | P1 | 确认 | has_more 不续拉 → 单轮最多消费 200 条变更 | domain-08 |
| D08-7 | ⑧ | `client/utils/play.uts:444-448`,`:646-650` | 丢操作窗口 | P1 | 确认 | 网络可达但请求失败（4xx/5xx/超时）时既不重试也不入队 | domain-08 |
| D08-8 | ⑧ | `client/utils/event_ops.uts:381` | op_id 碰撞 | P1 | 确认 | `op_+Date.now()` 无序列 → 同毫秒连带删除另一条未发送操作 | domain-08 |
| D08-9 | ⑧ | `schemas/sync.py:17`×`services/sync.py:135-153` | 注册表覆盖度 | P1 | 确认 | 只放开 pattern 不加分支 → 新 entity_type 完全无归属校验直写墓碑 | domain-08 |
| D08-13 | ⑧ | `workers/cleanup_job.py:65-71` | 越界删除风险 | P2 | **候审** | `_clear_tombstone` 只按 entity_id 不带 entity_type | domain-08 |
| D08-14 | ⑧ | `services/sync.py:184`+`cleanup_job.py:48-62` | 语义复用 | P2 | 确认 | DeletedLog.content_id 承载任意实体 → event 墓碑被清但行未删 | domain-08 |
| D08-15 | ⑧ | `client/utils/sync_client.uts:220-236,482-487`×`schemas/sync.py:84` | 上限未对齐 | P2 | 确认 | 镜像>5000 行 → reconcile 422 → 对账静默失效 | domain-08 |
| D09-1 | ⑨ | `api/wechat.py:99`+`services/wechat/service.py:340,357` | 边界/声明漂移 | **P0** | 确认 | 回调未传 user_id → 不下载媒体/不建 Content/不产记忆（F6 主链未跑） | domain-09 |
| D09-2 | ⑨ | `services/wechat/service.py:251`+白名单 `api/media.py:51`/`schemas/content.py:9`/`api/contents.py:130`/`storage.py:406` | 声明漂移 | **P0** | 确认 | wechat/ 键不在白名单 → 微信图片原件经 /media 恒 401 | domain-09 |
| D09-3 | ⑨ | `services/wechat/service.py:365-379`+`workers/cleanup_job.py` | 边界/软删口径 | P1 | 确认 | 微信"删掉"仅改 record.status、不触关联 Content、无 30 天清理 | domain-09 |
| D09-4 | ⑨ | `api/wechat.py:35,72,94` vs `services/wechat/service.py:100` | 声明漂移/魔法配置 | P1 | 确认 | 回调 Token 当应用 Secret 用 → 生产必然 gettoken 失败 | domain-09 |
| D09-5 | ⑨ | `api/wechat.py:80-100`+`services/wechat/service.py:382-410` | 门禁缺口/未接线 | P1 | 确认 | 回调不组装被动回复 XML（F6"找"在真实微信侧不可用） | domain-09 |
| D09-7 | ⑨ | `api/wechat.py:81`→`service.py:166-170` | 边界 | P1 | 确认 | async 内同步阻塞媒体下载（最长 30s）→ 回调排队/重推放大 | domain-09 |
| D09-8 | ⑨ | `core/config.py:121-123,16-20` vs `docs/拿key后推进计划.md:42` | 声明漂移 | P1 | 确认 | 无 AliasChoices+extra=ignore → 按文档注入 WECOM_* 静默失效、回调全 503 | domain-09 |
| D09-12 | ⑨ | `services/wechat/service.py:302-304` vs `api/wechat.py:91-97` | 异常口径 | P2 | 确认 | 缺 MsgId 抛 ValueError 逃逸为 500（非 403）→ 企微持续重推 | domain-09 |
| D10-1 | ⑩ | `services/external/dashscope.py:187`↔`api/contents.py:297`/`photo_content.py:180` | 门禁缺口（fail-open） | **P0** | 确认 | fail-closed verdict 无 action 键 → 两调用点两 if 均不成立、原文入库 | domain-10 |
| D10-2 | ⑩ | `llm_ops/guard_managed.py:132-139` | 门禁缺口（fail-open） | **P0** | 确认 | 托管护栏空响应判为 pass=True（唯一漏网 fail-open） | domain-10 |
| D10-5 | ⑩ | `services/external/sensitive_words.py:140-152` vs `:113-125` | 声明漂移 | P1 | 确认 | 回流词只进软表、硬规则引擎从不读 → "自动入规则表"未落实 | domain-10 |
| D10-6 | ⑩ | `core/config.py:23`+`dashscope.py:161,185` | 门禁缺口（配置依赖 fail-open） | P1 | 确认 | fail-closed 依赖 APP_ENV=production，无启动自检 | domain-10 |
| D10-7 | ⑩ | `services/pipeline_photo.py:63-94` | 门禁缺口（覆盖盲区） | P1 | 确认 | App 内上传图片全程无审核（仅微信域图片被审）——上架合规缺口 | domain-10 |
| D10-8 | ⑩ | `ai_tagging.py:119`+`llm_ops/event_merge.py:51`+`annotate.py:73`+`api/chat.py` | 门禁缺口（覆盖盲区） | P1 | **候审** | 用户可见 LLM 生成态输出全链路无护栏 | domain-10 |
| D10-9 | ⑩ | `llm_ops/guard.py:122-124,138-145` | 门禁缺口（软敏感 fail-open） | P1 | **候审** | LLM 故障时软敏感不漏标 → 回响/关怀可主动提及敏感话题 | domain-10 |
| D10-10 | ⑩ | `client/utils/voice.uts:299` | 门禁缺口（客户端默认放行） | P2 | 确认 | guardrail 缺失/passed 取不到默认 true | domain-10 |
| D10-12 | ⑩ | `api/asr.py:87-118` | 口径一致性 | P2 | 确认 | ASR 丢弃 masked_text → 手机号/身份证以原文返回 | domain-10 |
| D11-4 | ⑪ | `main.py:82-93,107-116`+`core/errors.py:234-247` | 边界（中间件覆盖） | P1 | **候审** | 500 绕过全部用户中间件（缺安全头）、429 亦缺安全头 | domain-11 |

## 3. 优先处置清单（P0 + P1 = 128 条）

> 类别列：**结**＝结构类，**功**＝功能与安全缺陷。按严重度（P0 前置）再按域排序。

### 3.1 P0（17 条）

| # | 编号 | 类别 | 一句话 |
|---|---|---|---|
| 1 | D02-2 | 结 | 前缀白名单 4 套 + wechat/ 漏网（收口为单一常量表） |
| 2 | D04-1 | 功 | 端云 L1 日界时区未对齐（云侧 tz=0）→ 日期错位 |
| 3 | D04-2 | 功 | 端云预处理去重分叉（云侧生产无去重） |
| 4 | D04-15 | 结 | 双跑夹具未覆盖 approx/corrected 分支 |
| 5 | D05-3 | 结 | 过滤器翻译双实现 + 未知键静默丢弃（无 else） |
| 6 | D07-1 | 功 | 镜像无 docs/ → 容器内画像/访谈首调 500 |
| 7 | D08-1 | 功 | 软删双轨（push delete 不改 contents.deleted_at） |
| 8 | D08-2 | 功 | REST 删除不写 DeletedLog → 30 天清理漏轨 |
| 9 | D08-3 | 功 | SFV value 不投影 → 离线改备注永不可见 |
| 10 | D09-1 | 功 | 微信回调未传 user_id → 不产记忆（F6 主链未跑） |
| 11 | D09-2 | 功 | wechat/ 键不在白名单 → 微信图片原件恒 401 |
| 12 | D10-1 | 功 | 护栏 fail-closed verdict 无 action → 原文入库 |
| 13 | D10-2 | 功 | 托管护栏空响应 fail-open 放行 |
| 14 | D11-1 | 结 | CAPSULE_001..004 漏登记进错误码表 |
| 15 | D12-1 | 结 | 设计令牌机制覆盖率 0%（763 处色字面量） |
| 16 | D12-5 | 结 | 生成链硬编码本机路径、不可复跑 |
| 17 | D14-1 | 结 | 审计轴1–4 未进任何自动门禁 |

### 3.2 P1（111 条）

| # | 编号 | 类别 | 一句话 |
|---|---|---|---|
| 1 | D01-1 | 结 | TokenPair 构造 10 行重复两处 |
| 2 | D01-2 | 结 | 三渠道登录函数同构 |
| 3 | D01-4 | 结 | 滑窗限流内存双实现 |
| 4 | D01-6 | 功 | 登录侧取 IP 口径不一致 → 全体共享配额 |
| 5 | D01-7 | 功 | 坏 token 异常三种捕获宽度 |
| 6 | D01-8 | 结 | PATH_AUTH_* 死码 + 硬编码绕过常量 |
| 7 | D01-9 | 结 | Authorization 注入 9 处 |
| 8 | D01-10 | 结 | 令牌有效期硬编码与 settings 无联动 |
| 9 | D02-1 | 结 | 上传状态机双实现 |
| 10 | D02-3 | 结 | 新增存储后端需改 7 处 |
| 11 | D02-4 | 功 | commit 失败重试 complete → KeyError 500 |
| 12 | D02-5 | 功 | 先删旧键后 commit → 音频永久丢失 |
| 13 | D02-6 | 功 | 蜂窝流量策略未生效（恒 hold） |
| 14 | D03-1 | 结 | contents.py 4 router 混居 902 行 |
| 15 | D03-2 | 结 | 缩略图签票 5 份 |
| 16 | D03-3 | 结 | 客户端 POST /contents 四份 |
| 17 | D03-6 | 功 | 畸形 ID 守卫不一致（500） |
| 18 | D03-7 | 功 | article 有声明无处理器 → 静默"成功" |
| 19 | D03-8 | 结 | 软删过滤手抄（本域 8 处） |
| 20 | D03-11 | 结 | RecordSheet 2208 行单体 |
| 21 | D03-12 | 结 | 新增内容类型需改 11 处 |
| 22 | D04-3 | 结 | AGG_CONFIG["night"] 死配置 |
| 23 | D04-4 | 功 | 计数缺软删过滤 → 卡片数与照片条矛盾 |
| 24 | D04-5 | 功 | 合并/无标题确认不置 title_source → 铁律漏洞 |
| 25 | D04-6 | 功 | L3 事件被客户端静默丢弃 |
| 26 | D04-7 | 结 | 日分桶 4 套语义 |
| 27 | D04-8 | 结 | 图片 URL 组装三写 |
| 28 | D04-9 | 结 | 同语义双实现（候审） |
| 29 | D04-13 | 功 | cid=None 提交被历史脏数据整条拒绝（候审） |
| 30 | D04-14 | 结 | 双跑门禁恒真断言 |
| 31 | D04-16 | 结 | API 层越级直连算法包 |
| 32 | D05-1 | 结 | mixed 三分支不可达 |
| 33 | D05-5 | 功 | 降级路径缺 status=="done" |
| 34 | D05-8 | 结 | 第一层 rerank 不可达且零测试 |
| 35 | D05-13 | 结 | rag 越级直连 external |
| 36 | D05-14 | 功 | 以图搜图能力不对称（候审） |
| 37 | D05-16 | 结 | correction 自建第二套 Qdrant |
| 38 | D06-1 | 结 | 归属 loader 三分裂 |
| 39 | D06-2 | 结 | 门禁登记名不匹配（哑条目） |
| 40 | D06-3 | 结 | `_is_sensitive` 死码 + 双实现 |
| 41 | D06-4 | 结 | 到期判定三实现 |
| 42 | D06-5 | 功 | 到期提醒无触发入口（候审） |
| 43 | D06-6 | 结 | 画像敏感 CRUD 域归属错位 |
| 44 | D07-2 | 结 | 访谈关键词表与 JSON 双实现不一致 |
| 45 | D07-3 | 功 | 入库主链不检查 degraded（D-22 根因） |
| 46 | D07-4 | 功 | 纠错不回写 class_source/不重索引 |
| 47 | D07-5 | 功 | 契约增量字段静默丢弃 |
| 48 | D07-6 | 结 | 客户端 DTO 不解析 degraded |
| 49 | D07-7 | 功 | 英文维度 id + 伪造置信度 |
| 50 | D07-8 | 功 | 兜底三问语义错配 → 画像污染 |
| 51 | D07-9 | 功 | 复述确认闭环未接线 |
| 52 | D07-10 | 功 | 清除画像为本地安慰剂 |
| 53 | D07-11 | 功 | locked 只写不读、可静默解除 |
| 54 | D07-12 | 功 | 画像主数据无人工确认端点 |
| 55 | D07-13 | 结 | validate() 硬编码维度数 |
| 56 | D07-14 | 结 | 三套 dimensions 读路径 |
| 57 | D07-15 | 结 | 新增标签注册点分散 6 处 |
| 58 | D07-16 | 功 | Qdrant/DB 非原子 + 双提交窗口 |
| 59 | D08-4 | 结 | 字段级无本地载体 |
| 60 | D08-5 | 功 | 冲突败方旧值写入变更日志并下发 |
| 61 | D08-6 | 功 | 分页 has_more 不续拉（≤200 条） |
| 62 | D08-7 | 功 | 请求失败既不重试也不入队 → 丢操作 |
| 63 | D08-8 | 功 | op_id 同毫秒碰撞 → 连带删除 |
| 64 | D08-9 | 功 | 新 entity_type 无归属校验直写墓碑 |
| 65 | D09-3 | 功 | 微信"删掉"与全局软删口径不一致 |
| 66 | D09-4 | 功 | 回调 Token 当应用 Secret → gettoken 必失败 |
| 67 | D09-5 | 功 | F6"找"未组装被动回复 XML |
| 68 | D09-6 | 结 | 新增消息类型需改 9 处 |
| 69 | D09-7 | 功 | async 内同步阻塞媒体下载 |
| 70 | D09-8 | 功 | 按文档注入 WECOM_* 静默失效 |
| 71 | D10-3 | 结 | 4 条入库主链绕过策略选择器 |
| 72 | D10-4 | 结 | moderate_managed 零调用 + 双实现 |
| 73 | D10-5 | 功 | 回流词未入硬规则表 |
| 74 | D10-6 | 功 | fail-closed 依赖单一环境变量 |
| 75 | D10-7 | 功 | App 内图片无审核（合规缺口） |
| 76 | D10-8 | 功 | 生成态输出无护栏（候审） |
| 77 | D10-9 | 功 | 软敏感 fail-open（候审） |
| 78 | D11-2 | 结 | 错误码 AST 门禁盲区 |
| 79 | D11-3 | 结 | 429 信封双实现 + 限流域未登记 |
| 80 | D11-4 | 功 | 500 绕过中间件缺安全头（候审） |
| 81 | D12-2 | 结 | 令牌值本身已漂移 |
| 82 | D12-3 | 结 | 硬编码未收敛（103 非令牌色） |
| 83 | D12-6 | 结 | 生成物 vs 实装双向漂移（18 图标） |
| 84 | D12-8 | 结 | 生成物快照分叉无门禁 |
| 85 | D12-9 | 结 | 轴2 三向缺一向 |
| 86 | D12-10 | 结 | 新增 1 tab 需改 6 文件 12+ 点 |
| 87 | D12-11 | 结 | TabBar 4 段同形 goX |
| 88 | D12-12 | 结 | Tab 组件契约样板重复 |
| 89 | D12-13 | 结 | goBack 同语义 12+1 实现 |
| 90 | D12-15 | 结 | 6 个巨文件（已冻结基线） |
| 91 | D13-1 | 结 | CI 客户端 tsc 门禁恒绿 |
| 92 | D13-2 | 结 | single-flight 单测恒不可用 |
| 93 | D13-3 | 结 | check_env_template 无门禁 |
| 94 | D13-4 | 结 | 安全审计备份域指向开发件 |
| 95 | D13-17 | 结 | 备份脚本双实现 |
| 96 | D13-18 | 结 | 部署脚本样板五份手抄 |
| 97 | D13-21 | 结 | compose 环境覆盖失效 |
| 98 | D13-22 | 结 | Dockerfile COPY 范围 vs 注释 |
| 99 | D13-24 | 结 | 迁移库≠运行库 |
| 100 | D13-25 | 结 | RUNBOOK 定时任务无交付件 |
| 101 | D14-2 | 结 | 基线 thresholds 死配置 |
| 102 | D14-3 | 结 | 重复门禁可挪移绕过 |
| 103 | D14-4 | 结 | 白名单僵尸豁免/改名误报 |
| 104 | D14-5 | 结 | "镜像"整文件豁免 |
| 105 | D14-6 | 结 | 轴1 字段级契约漂移不覆盖 |
| 106 | D14-7 | 结 | openapi 再生无脚本/无断言 |
| 107 | D14-8 | 结 | 覆盖率阈值 50/60 两口径 |
| 108 | D14-9 | 结 | 密钥模式集双实现 |
| 109 | D14-10 | 结 | lessons 提示分支不可达 |
| 110 | D14-18 | 结 | 退出码口径不一致 |
| 111 | D14-21 | 结 | 镜像守卫 test_agent_schema_alignment.py 不存在 |

---

## 4. 与现有 refactor-ledger.md 旧条目的差异（专章）

> 核实手段：本机 `Grep` 工具不可用（`rg execution failed: program not found`），改用 PowerShell `Select-String`（语义等价、含行号）实测。

### 差异① 软删手抄计数：旧台账「44 处/21 文件」 vs 域③「49 处」——**口径差异，二者皆真**

实测 `Select-String 'deleted_at\.is_\(None\)'`（`backend/**/*.py`）：

| 统计范围 | 命中数 | 文件数 | 说明 |
|---|---|---|---|
| **`backend/app/`（生产代码）** | **44** | **21** | ＝**旧台账值与文件数精确吻合**（`contents.py` 8、`events.py` 6 最密，与台账表述一致） |
| `backend/tests/` | 4 | 3 | test_stats_daily_summary.py×2、test_aggregation_seed_exclude.py×1、test_ab_scenarios.py×1 |
| `backend/scripts/` | 1 | 1 | backfill_orphan_events.py |
| **`backend/`（全体，域③口径）** | **49** | **25** | 44+4+1＝49 ✓ |

**结论**：**非事实矛盾，是统计范围不同**——旧台账 `44/21` 只数 `backend/app`（生产代码，与台账"软删过滤手抄""21 文件"表述自洽）；域③的 `49` 数整个 `backend`（含 tests/scripts 5 处）。**生产口径的真实计数就是旧台账的 44 处/21 文件**，域③报告内亦已注明"任务书给的 44 为较早基线"——两数字可同时成立，建议台账统一标注口径 = **`backend/app` 44 处/21 文件（test/scripts 另计 5 处）**。

### 差异② 镜像豁免守卫：旧 L-11 称有 `test_agent_schema_alignment.py` 守卫 vs 域⑭称不存在——**域⑭正确**

实测：
- `Get-ChildItem -Recurse -Filter *alignment*` → **0 命中**（无任何 `*alignment*` 文件）。
- `git log --all --oneline -- "*test_agent_schema_alignment*"` → **空**；`git log --all --diff-filter=A --name-only` 内检索 `alignment` → **空** ⇒ **git 全历史从未存在**。
- 该名仅被 3 处引用：`backend/app/db/models/memory_agent.py:12`（docstring）、`refactor-ledger.md:45`（L-11）、`docs/决策台账.md:111`（§4.13）。

**结论**：**域⑭ 正确，旧 L-11 的"守卫依据"为幻影**。B1 镜像豁免（`audit_harness.py:288-289`）当前**无任何自校验**（且域⑭ D14-5 另指其"整文件含'镜像'即全量豁免"仍是假阴性口子）。建议：删 L-11 的守卫声明，或补一个真实的对拍测试。

### 差异③ 归属校验：旧台账 B3「验证即达标、无缺口可补」 vs 域⑥ D06-2「门禁哑条目」——**旧台账结论需修正**

旧台账 §8 记 B3（L-09 归属 helper 收敛）为「✅ 完成（验证即达标，无代码改动）」，依 `test_authz_gate.py` 6 passed 判"AST 门禁确认所有按 id 路由端点均经归属 helper、无漏网"。域⑥ 实读后指出：
- `test_authz_gate.py:47` 的 `OWNERSHIP_LOADERS` 登记名 `load_owned_capsule`（无下划线）**≠ 实际符号** `capsules.py:45 def _load_owned_capsule`（有下划线）→ `_ownership_evidence`（L136-154）精确匹配失配 ⇒ **capsules loader 实际未被门禁识别**，是靠 `user.id` 属性访问兜底通过（D06-2）。
- `test_whitelist_and_exempt_entries_still_exist`（L200-212）只校验 CREATION_WHITELIST/EXEMPT，**不校验 OWNERSHIP_LOADERS** ⇒ 该哑条目**永不会被发现**。

**结论**：B3 的"无缺口"结论建立在"门禁全绿"之上，但门禁本身存在**哑条目**（一个 loader 从未被它认出）——**B3 应降级为"门禁覆盖存在盲点，需修正登记名或改 AST 匹配"**，并纳入域⑥ D06-1/D06-2 的收敛窗口。

### 差异④（附）其他需对齐项

| 旧台账条目 | 域报告复核 | 差异性质 |
|---|---|---|
| L-12「style 7391 行/32 文件（P1 **候审**）」 | 域⑫ D12-1：口径可精确复现（7359+32=7391）、补"令牌机制覆盖率 0%"判据、**升 P0 确认** | **严重度升级**（P1 候审→P0 确认），同一条不重复立项 |
| L-02「TabIndex script 848 行」+ filesize 白名单 | 域⑫ D12-15：复核**无新增超阈文件**，与基线一致 | 一致（无差异） |
| L-11/B1「`audit_harness all` → 无 CRITICAL（原 1 条假阳性已消）」 | 域⑭ D14-5：镜像豁免**仍是假阴性口子**（整文件豁免），另 D14-1 轴1–4 仍未进自动门禁 | **结论不完整**：假阳性已消属实，但豁免机制引入了新的静默面 |
| AGENTS「第四窗媒体票据 8 文件在途＝勿碰」 | 域②③④⑤⑥⑧⑨⑪⑫⑬ 一致记录 **在途=0、该批已入库** | **已过时**（勿碰名单可解除，与域②逐文件提交核实一致） |

---

## 5. 跨域扩展成本表（各域「新增注册点 / 扩展成本」结论）

| 域 | 扩展场景 | 需改处数 | 是否有注册表/端口抽象 | 关键注册点（来源） |
|---|---|---|---|---|
| ①认证设备 | 新增一类认证方式（登录渠道） | **最少 8 处生产代码**（全注册点 12 处） | 有 ABC（LoginProvider/SmsSender）；**分发为 if 链**、编排无公共包装 | Provider 类 / `get_login_provider` if 链 / 服务层编排 / 路由 / schema / `__init__` 导出 / contract.uts / auth.uts（domain-01） |
| ②上传媒体 | 新增存储后端；新增一类媒体类型 | 后端 **7 处**；媒体类型 **≥8 处** | 三个可覆盖钩子设计良好；**无媒体类型注册表** | `_BACKENDS`/单例/config Literal/env/api `_cos_sts_configured`；媒体类型 8 处散点（domain-02） |
| ③内容记录 | 新增一类内容类型 | **≥11 处代码 + 2 契约注释** | `CONTENT_HANDLERS` 是正确范式；**契约/前缀/展示维度无注册表（覆盖度≈1/4）** | schemas pattern / contents 白名单 / 前缀清单 / pipeline 注册表 / notify 映射 / search_api if 链（domain-03） |
| ④事件聚合 | 新增一层聚合 / 换算法 | **19 处**（后端 11 + 客户端 6 + 脚本 1 + 门禁 1） | **无任何注册表/插件机制**（硬编码 4 层假设） | agg_types 常量+AGG_CONFIG / AggregateResult / pipeline / aggregate_write / schemas `le=3` / 客户端 6 件 / 夹具 / run_validation（domain-04） |
| ⑤检索 RAG | 新增一路召回；替换向量库 | 召回 **8 处**；换库 **14 处**（含 1 文件全量重写 + 3 厂商命名 DB 列 + 5 部署件） | **无 provider 接口、无单点扩展位** | vector_store 两路硬编码/RRF 权重/4 调用点/trace 无 schema/客户端白名单；换库穿透 14 处（domain-05） |
| ⑥回响胶囊消息 | 新增一种通知渠道 | 6（notify 端口）+3（短信平行端口）+2（客户端映射）+3（定时登记） | 推送端口有 Protocol，但**单槽单例、无按渠道名注册表** | notify 渠道分发 / PushChannel / 单槽全局 / message 列注释 / RUNBOOK §5 / daily_review（domain-06） |
| ⑦画像访谈纠错 | 新增画像维度；新增纠错标签；新增敏感档位 | 维度 **6-7 处**（含 2 处纯代码）；标签 **6 处**；档位 **5 处** | schema 驱动良好；但 validate() 硬编码计数、关键词表为纯增量维护点 | JSON 真值 / profile_schema 硬编码计数 / interview 关键词表 / map / fallbackQuestions / manage.uvue 截断（domain-07） |
| ⑧同步离线 | 新增一种可同步实体类型 | 后端 **6 处**（缺任一静默出错）+ 客户端 **3 处软点**（+1 并行通道） | SFV 通用路径 vs 事件专用路径**两套并行注册面** | schemas 正则 / sync 归属 if-elif / owner 预取 / DeletedLog / cleanup_job；events/sync 独立端点（domain-08） |
| ⑨微信 | 新增一类消息类型；新增一个渠道 | 消息类型 **9 处**；渠道 **≥6 处** | 验签/加解密/媒体三端口已反转，但**业务模块直连 httpx、token 未走端口、注入点未使用** | gateway 白名单+字段抽取 / service 二次白名单+两分支+扩展名+审核+三元映射 / models 注释（domain-09） |
| ⑩护栏安全 | 换护栏厂商；新增一类违规类型 | 换厂商 **5 处**；违规类型 硬 **4 处**+软 **3 处** | content_safety 适配器注册表完整；但 4 条主链绕过选择器 | `_ADAPTERS` / ProviderLiteral / config Literal / 4 调用点 / errors+ratelimit；硬规则 4 处 + 软 3 处（domain-10） |
| ⑪观测错误 | 新增错误码；新增限流域 | 错误码 **2 处**；限流域 **2 处** | 错误码有唯一登记表（但存在就地定义绕过先例） | `_ERROR_SPECS`+`ERR_*` 常量区；`_DOMAIN_SCOPES`+settings 反射（domain-11） |
| ⑫客户端壳 UI | 新增 1 个 Tab | **6 文件 / 12+ 点** | **无单一注册表**（VALID_TABS 数组 + 4 ref + 模板 + if 链 + TabBar + shell_state 自由串） | shell.uvue 30/34-37/15-18/82-100/136-146；TabBar 5-30/33-36/70-119；图标；shell_state:16（domain-12） |
| ⑬脚本部署 | 新增脚本/部署件/env/systemd | env **4 处**；systemd **3 处**；脚本类 **2-3 处** | **无注册表抽象**，靠"文档两处 + 脚本一处"人工同步；唯一自动点（env）未接门禁 | ci.yml / deploy README / RUNBOOK / deploy_one.sh / bootstrap / config.py / check_env_template（domain-13） |
| ⑭harness 工具 | 新增审计轴；新增门禁检查；新增棘轮基线 | 审计轴 **8 处**（核心 3 强制）；门禁检查 **2 处**；棘轮基线 **1 处** | **有两套并行范式**（structure_findings vs checks 字典），新增轴须二者择一无统一注册表 | audit_harness 函数/argparse/dispatch/structure_findings/docstring/baseline；review_agent checks（domain-14） |

**扩展成本最高的 3 个域**：**① 域④（19 处，且为"新增一层"结构性硬假设）** ＞ **② 域⑤（换库 14 处 / 新增召回 8 处）** ＞ **③ 域⑫（新增 1 个 Tab 需改 6 文件 12+ 点）**。次高梯队：域③（11 处）、域⑨（9 处）、域⑧（9 处）。共同根因：**契约/注册表维度普遍缺位**（仅"处理器维度"类注册表到位，校验/前缀/展示/通道维度均散落硬编码）。

---

## 6. 端云一致性结论（专章）

### 6.1 域④ 事件聚合（端云同源对照）

**共享参数数值：8 项零漂移**（L0 时间窗默认/保守 3600/1800、保守开关、L0 空间窗 500、min_pts 3、连拍折叠 5、步行/驾车速度上限 6000/120000 ÷3600）——`agg_config.uts` × `agg_types.py::AGG_CONFIG` 逐值一致。

**语义/行为漂移：7 处（B1–B7）**

| # | 语义 | 漂移性质 | 后果 |
|---|---|---|---|
| B1 | L1 日界时区 | 服务端 `l1_daily_aggregate(tz=0)` 默认 UTC；客户端传设备偏移；生产路径**未传 tz** | 沪区 00:00–07:59 照片云侧落前一日（**P0·D04-1**） |
| B2 | 预处理去重 | 服务端 `preprocess` **无去重**；客户端做（phash/id） | 端云步骤集不同（**P0·D04-2**） |
| B3 | 步行速度分支 | 服务端 `WALK<speed<=DRIVE→approx`（保留坐标）；客户端无此分支 | 分支缺失 |
| B4 | 单点漂移众数拉回 | 服务端 `mode_count>=2→corrected`；客户端直接置 null | 分支缺失 |
| B5 | 漂移点污染 | 服务端比**未修正** fold；客户端比**已修正** corrected | 漂移级联差异 |
| B6 | L2 伪簇日分桶 | 服务端 `ts.date()`（无深夜规则/无 tz）；客户端不做 L2 | 云侧第三套日界（内部不自洽） |
| B7 | 时间轴日分组 | 客户端 `dayKey` 本地自然日无深夜规则；与 L1 聚合日界（23:30–1:00 归前一天）不一致 | 展示与聚合错位 |

**未登记进契约表参数：3 项**（`GPS_MODE_GRID_DECIMALS`、`GPS_MODE_MIN_SUPPORT`、L2/L3 硬编码 `span_days<2`）。

**关键结论**：8 项共享值一致 ≠ 行为一致；**B1（L1 时区）与 B2（去重）是生产路径真实分叉**，且被 AGG-016 双跑夹具的"固定参数（tz=480）+ 夹具内 Python `_dedup` 复制品"**掩盖**——即**门禁参数 ≠ 生产参数**，"双跑全绿"不代表端云同源；B3/B4/B5 三处漂移完全落在夹具覆盖之外（D04-15）。此外 `AGG_CONFIG` 部分键（night/eps_s_m/min_pts）为**装饰性死配置**（无人读取，夜界靠字面量硬编码）——D04-3/D04-14。

### 6.2 域⑧ 同步与离线（字段级 LWW 端云对照）

| 规则项 | 服务端 | 客户端 | 一致性 |
|---|---|---|---|
| 时间戳来源 | 取客户端传入 `updated_at`（设备时钟），缺失回退服务端 | `isoNow()`＝设备时钟，**硬编码 +08:00** | ⚠️ 来源一致，但**非 +08 时区设备时间戳整体偏移** |
| tie-break | 严格大于才更新；相等→云端胜 + 写 conflicts | **无客户端 LWW 判定**：盲信服务端 `updated_at` 直接覆盖 | ❌ 服务端单向权威 |
| 字段粒度 | 每 `(entity_type, entity_id, field)` 一行独立比较；`field="*"` 为实体墓碑 | **实体级**镜像行 `entity_id\|updatedAt\|deleted`，`value` 丢弃不落本地 | ❌ 字段级/实体级错配（field/value 无人消费） |
| 软删 30 天 | **两条互不相通轨道**：① REST 删除→`contents.deleted_at`（**无清理任务消费**）② 同步 delete→SFV+deleted_logs→cleanup_job 物理清理 | 无本地 30 天逻辑，仅 UI 文案 | ❌ 端口径分裂 + 服务端内部双轨 |

**补充结论**：`push_ops` 的 LWW 比较基准是「**设备时钟 vs 设备时钟**」（上一次客户端写入落库的 `updated_at`），**服务端时钟不参与判定** ⇒ 跨设备时钟偏差无保护；`parse_ts` 统一按 UTC 解释 naive 时间，而客户端永远带 `+08:00` ⇒ 语法上不走 naive 分支，但偏移值是**硬编码而非设备真实时区**。

**关键结论**：端云在 LWW 上**并非真正对等**——客户端零判定、字段级能力无本地载体；软删存在"端口径分裂 + 服务端内部双轨"（**P0·D08-1/2/3**），用户可见路径上"30 天后彻底清除"承诺未落地，且离线删除出现"先删了还在、后突然永久消失"的错序；离线改备注/字段写入 SFV 但**不投影回权威表**，用户永不可见。

---

## 7. 方法与边界声明

- **只读保证**：本汇总未修改任何业务代码、未修改任何 `domain-NN` 报告；唯一写入＝本文件。
- **检索工具**：本机 `Grep`（rg 后端）不可用，第 4 章两处核实改用 PowerShell `Select-String`（语义等价、含行号），命令附于各结论。
- **口径提示**：各域严重度/状态以各 `domain-NN` 表格为准；域④ 报告 §元信息标注 P1×9/P2×6 与其表内实际（P1×10/P2×5）差 1，本汇总**以表内为准**（全库 P1 合计 111）。
- **未覆盖**：`agent/` 目录全波排除；`client/uni_modules/**`、`client/unpackage/**`、`scripts/realdevice/evidence/**`（二进制/证据）未深审。
- **待人工确认（候审 30 条）**：已在上表状态列标注，主要为语义判定类（如 D01-16/17、D04-13、D05-14、D06-5、D08-13、D10-8/9、D11-4/8/12、D14-16/17/24 等），落地前需人工/运行时复核。