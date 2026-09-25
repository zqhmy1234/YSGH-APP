> 🧹 **2026-09-04 整饬**：已完成/已闭环条目已压缩为「✅ 速查卡」（分支/SHA｜文件｜方法｜设计来源）；未实现/半通/待复验/待拍板条目保持原叙述。未闭环事项现行权威清单 = `_diff_ledger.md` 与 `_execution_plan_20260904.md` §1。
> 📌 **当前状态速览（2026-08-29）**：收尾 Wave 1–4 全收口（17/17），真机 7 清单全达终态+补验。终值：**用户故事 ✅46/🟡7/❌0 · A 级 32 条 · 性能门禁 10/2/6 · 30s ✅6.0s**。
> 数字唯一现行口径 = `AGENTS.md`「当前状态」节；术语/决策/待拍板 = `docs/决策台账.md`；缺陷台账（19 单：D-01~D-16、D-18、D-19、D-21）与环境事件（O-1/O-2）= `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4/§5。
> **下一步 = 4b 修复批次（执行计划已定：`docs/4b修复批次执行计划_20260829.md`，P-0 拍板五项全落、P-1 隔离工作区已建）**：批次1 D-18/D-19（重打包复验）→ 批次2 D-16/D-07/D-08＋散单 D-05/D-10/D-14/D-21（08-29 拍板并批）→ 批次3 S2 校准（卡真值）+D-06（**等价复现验收，无需第二设备**，台账 §1.8）。
> 本文件为**时间线日志**（新旧混排属历史演进），新条目追加在**末尾**；历史条目只读保留，仅加 [勘误] 注记。与速览冲突的旧数字以速览为准。

## ✅ 速查卡 · 2026-09-10 · 缺失页面战役全收口（波 A-E + 安全深扫 + 波 D 重构 + P2-2）

✅ 全战役并入 develop 且集成双门绿：远程链 feature=`1924073`（已删本地，远程镜像保留）→ merge `cb52d22`（波E 手工构造）→ P2-2 `ad5d64f` → §II/§JJ/§KK 台账 → `0c5bc3d`（gitignore 豁免+B905 清零）｜验证=后端全量 **832 passed 4 skipped 零失败** + 客户端编译门「编译成功」+ **云打包出包成功**（android_debug_vapor.apk 32.2MB）｜安全=深扫 0 P0 七项修复全落地（P1-1 回调 XML/P2-1 sync 投毒/P2-3 胶囊并发/P2-4 音频软删/P2-5 归属纵深/P2-6 日志/MEDIA_003）+ AST 归属门禁与错误码门禁双焊死｜波 D=归属 loader 收敛+三巨文件拆分（AST 逐函数比对零漂移）｜分支终态=develop+main 两枚，fix/4b 归档 origin/archive/fix-4b｜⚠️ 新发现（进行波）：三枚 yishu UTS 插件类未进云包 dex（旧包同病=非 merge 回归），D-18/D-19 原生波根因调研中｜台账权威=_diff_ledger.md §GG/§HH/§II/§JJ/§KK + docs/后端重构与性能优化计划_20260910.md

## ✅ 速查卡 · 2026-08-27 · 重构批次 G1（认证安全）

✅ 认证安全四件套+限流中间件：techdebt/g1 @ 67f50f1（G 集成波并入 develop）｜client/utils/auth.ts + backend/app/core/ratelimit.py + auth 服务层 + 迁移 f1a2b3c4d5e6｜refresh single-flight 共享 in-flight（并发 401 只一次 refresh）+ /auth/logout 吊销（AUTH-006）+ refresh_token HMAC-SHA256 独立密钥（hmac$ 前缀+存量兼容）+ SMS 验证码加盐 + Redis 固定窗口限流（auth/ASR/搜索三域、白名单、降级不 500、429 带 X-Request-ID）｜设计来源：重构批次 F 提示词 R6（AUTH-006/R6#2/#3/#6-#9）

## ✅ 速查卡 · 2026-08-27 07:20 · 重构批次 F 第一波集成（6 Agent）+ 遗留 bug 修复

✅ F 六 Agent 合入 develop：F-Auth d47684c / F-Rag 284fd2b / F-Asr 1a60fe2 / F-ClientA 0f64c73 / F-ClientB / F-Content 9f0b2f4｜main.py + openapi 45 路径零 diff｜10 commit 文件域复核零跨域，受影响域 227 passed；O6 queue_store.ts 单 key（yishu_offline_queue）六字段契约落地；enqueue job_id 净化器 `_safe_job_id_part` 修复 RQ 2.x ValueError；CI #41 全绿（develop @ a64f60a）｜设计来源：docs/重构批次F提示词_20260827.md

## ✅ 速查卡 · 2026-08-27 08:30 · 重构批次 F-Events（F3 聚合独立 per-user 任务 + F5 events.py 拆包）

✅ events.py 拆包 + 聚合独立任务：develop @ c0b74d1+28ba960｜backend/app/services/events/ 子包（aggregate/sync/timeline/edit，`__init__` 重导出）+ core/queue.py + workers/worker.py｜process_content 主提交后经 enqueue_unique 按 user:<uid> SETNX 去重入队 run_user_aggregation（同用户并发只跑一次聚合、失败静默、幂等可重投）；受影响域 144 passed + 契约快照零 diff｜设计来源：重构批次 F 提示词（R1#5/R5-3）

## ✅ 速查卡 · 2026-08-27 06:50 · 重构批次 F-Content（F1 照片双轨收口 + F4 job 级去重）

✅ 照片注册双轨收口 + job 级去重：techdebt/f-content @ 9f0b2f4（F 波并入 develop）｜backend/app/services/photo_content.py（新）+ core/queue.py + pipeline.py｜抽唯一注册编排（dedup_key 409/cos_key 幂等、moderate、original/thumbnail_meta/update 模式参数化）；enqueue_unique Redis SETNX 原子预占位收敛四域入队点；pipeline 尾段先入队后提交（消除 commit→enqueue 间隙丢任务）；受影响域 149 passed｜设计来源：重构批次 F 提示词（P0-6/R5-4#5/F4）
- 遗留登记：`enqueue_idempotent`（R4#4，classify/corrections 域）冒号 job_id 潜在不兼容，登记待归口（已由后续 `_safe_job_id_part` 收口）

## ✅ 速查卡 · 2026-08-27 04:40 · 重构批次 CDE 三批集成 + 遗留项处理

✅ C 客户端收敛 + D 测试基建 + E 契约与输入：31 commit 逐条文件域复核后 push develop @ 12f1006（CI run 33011027116）｜client api.ts·time.ts·search.uvue 等 + backend schemas/api/services/tests/migrations｜C1 网络层统一（rawRequest 401 重放+5xx Sentry）、C2 死代码收口、D1 存量测试迁移+D2 覆盖提速、E1 契约一致性（22 处裸码→ERR_*）、E2 client_generated_id 幂等键+部分唯一索引；精准域 439 passed；TD-P3 schema.sql 漂移闭环 57a9af1｜设计来源：docs/重构批次F提示词（R3/R8/R4/R6）

## ✅ 速查卡 · 2026-08-26 23:10 · 技术债 TD-P1B 性能与索引批次完成

✅ S6-1~S6-10 十项性能/索引优化：7324e19｜backend/app/services/events/aggregate.py·sync.py + core/storage.py + schema.sql + 迁移 a7b8c9d0e1f2 等｜聚合增量游标化（30 天窗口，O(N²)→近线性）、N+1 上提、sync.push_ops 批量预取、4 个缺失索引、reconcile O(N)、echo/画像/correction/企微 token/存储后端单例化｜设计来源：docs/技术债清理计划_20260826.md（P1B）
- 遗留登记（明确不做）：upload.py 流式合并（P2 批次或另排）；pipeline.py patch_extra 样板收敛（P2B，文件域与 P1B 冲突，P1B 合入后再做）
## ✅ 速查卡 · 2026-08-26 22:20 · Wave4 AgentK（B5d 后台域）集成 + J/H 代劳 + 决策落地

✅ B5d 后台域集成：merge wave4-agentK｜client/uni_modules/yishu-background-tasks/（新插件 19 文件）+ yishu-photo-watch 3 文件｜WorkManager 单队列 P0-P4 + dataSync 前台服务短命化 + attribution tag + 标准基座降级（nova 11 真机验证）；代劳 J 修 test_notify 时钟依赖（14 passed）、H 补 res.data object 守卫；4 决策项落地（FinetuneJob 删 ORM/presign 删/短信 501 冻结/依赖升版）；基线 502 passed + review_agent --full 全绿｜设计来源：docs/parallel-dev/ Agent K 任务卡（B5d）
- K 遗留待验：自定义基座云打包验证 FGS/WorkManager 真实执行/attribution panel（→ 后续 D-18/D-19 揭示全基座失效，见收尾 Wave 3 条）；K-2 后端 asr.py 每通道 max_duration 注入 + _CHANNELS 适配器工厂（排期）

## ✅ 速查卡 · 2026-08-26 21:40 · 技术债清理 P0 批次完成（安全/正确性 8 项）

✅ P0-1~P0-8 八项安全/正确性修复：502 passed + review_agent --full 全绿｜backend sms/STS 白名单/file_magic.py/pipeline/storage/errors.py/queue 等域｜短信 mock 生产 501+验证码哈希、COS STS 路径级白名单防前缀逃逸、上传魔数嗅探+40MP 炸弹防护、voice 失败回写、complete 幂等对齐、StorageError 包装+孤儿扫描登记、错误码登记表（40+3 码唯一真源）、RQ job_timeout+Retry(3)｜设计来源：docs/技术债审查报告_20260826.md + docs/技术债清理计划_20260826.md
- 遗留登记：STS root ARN 降级待子账号 role、thumbnail_meta 移 worker、超龄 processing 重扫、孤儿对象扫描、分队列 worker 部署（均入代码注释）

## ✅ 速查卡 · 2026-08-26 20:20 · Wave 4 集成（J/L，K 未完成）

✅ ASR 消费域 J-1~J-8 + M3 微信域合入：wave4-agentJ @ ab6d447 + wave4-agentL @ a0fe630 + 集成接线 deb6e24｜upload/complete voice 分支 + /contents voice cos_key 幂等 + pipeline enrich_content_emotion 补 consume_emotion + OpenAPI 重导出｜uploadVoicePersistent 优先 content_id；22:00 复盘登记部署侧 cron（backend/scripts/daily_review.py 幂等）；pytest 467 passed 全绿｜设计来源：docs/parallel-dev/10、12（Agent J/L 任务卡）
- 遗留：WECHAT key 待申请（code2session 已接真实链路，未配保持 mock/501）

## ✅ 速查卡 · 2026-08-26 19:00 · CI 全链路修复完成（#8-#21）——CI #21 首次双绿

✅ CI #21 Fast + Full Gate 全绿：本地 419 passed + api_smoke 6/6 + research 18 全过｜.github/workflows + scripts/warm_hf_models.py + schema.sql/setup_pg.sql + CI 镜像｜7 根因链修复（PG 就绪重试循环/迁移链不自包含回退 schema.sql 建库/步骤级 env 密码内联/profile_annotation_pool 补齐/pgvector 扩展+镜像/qdrant 升 v1.19.0/HF 模型预热步骤），全部登记 docs/lessons.md（族4/6）｜设计来源：CI 失败日志逐条根因分析；建库源=schema.sql 决策（#6/#21 验证）
- 决策留档：alembic 仅用于本地/生产增量；漂移检测另行设计（issue #2 修正）

## ✅ 速查卡 · 2026-08-25 · PR 评论 5 项修复并更新现有 PR

✅ PR#1（codex/asr-pipeline-hardening，后并入 develop）评论修复：rebase 对齐 origin/develop + numpy>=1.26 显式依赖 + SenseVoice 部署预置脚本与资产校验（缺 SENSEVOICE_MODEL_DIR 显式失败）+ DASHSCOPE_REGION Host 拼接（保留 BASE_URL 覆盖）+ 主转写/本地情绪拆两个 RQ 阶段（情绪失败不影响转写）；音频范围 49 passed｜设计来源：PR 团队审查评论
- 待办：生产发布时执行 SenseVoice 模型预置步骤

## ✅ 速查卡 · 2026-08-25 · 第二波遗留全清 + 真机/模拟器验证 + RAG 管线审查

✅ 八项遗留收尾（nova11 + 模拟器双端验证，全量 pytest 254 passed）：S-ST-1 分片上传真机链路（MediaStore SIZE 列注入绕 getFileInfo 沙箱限制）｜S-MO-1 菜单真机验证（z-index+移除 itemList 双"取消"）｜split 全链路（GET /events/{id}/items + 选片面板 + POST split）｜autoflush=False 时间窗 bug（merge/split 前加 db.flush()+2 回归测试）｜EXIF 排查实锤（scan_file 丢 GPSInfo，真实相机照片可用）｜AMAP 后端全链路 E2E（东方明珠真实逆地理）｜FilesystemStorageBackend 跨进程存储后端（.env STORAGE_BACKEND=fs）｜Android 35 模拟器 AVD yishu_test
✅ RAG 管线系统性审查修复 3 真 bug：①rerank 自 8-24 从未生效（CrossEncoder model_kwargs 在 ST 3.4.1 已移除→automodel_args）②时间正则误伤改仅句首触发 ③关键词精确命中稀释→_boost_exact_matches（词元全命中 ×1.8）；rerank 默认关（CPU ~850ms/对超 P95<3s 门禁）；修复后 hit_rate@3=0.7273 门禁 PASS｜设计来源：docs/RAG管线审查报告_20260825.md

---

## ✅ 速查卡 · 2026-08-25 · RAG 测试体系核实 + AMAP 逆地理 + Sentry 接线 + 真值数据规格 v1 + S-ST-1/S-MO-1 + review_agent 内存优化

✅ RAG 测试体系核实：research/rag_benchmark/metrics.py 指标体系全貌（recall/hit_rate/precision/mrr/ndcg 分层+行为层）；默认套件 `-m "not rag"` 排除重测试、`pytest -m rag` 需 Docker Qdrant+BGE-M3；实测回归修复——test_rag 改独立 collection yishu_test_rag（与生产 yishu_contents 隔离），修复后 14/14 过；F5 缺口核实（Qwen3-VL 已接线/corpus-A 基准已存在/LLM 精排=真实缺口/以图搜图 P95=7629ms 超门禁）
✅ AMAP 逆地理：backend/app/services/external/amap.py（geohash 精度 6 纯函数+regeo with_retry 3 次退避+get_place 缓存≤30 天合规）+ GeoCache 模型迁移 4d00dfec7b46 + pipeline._process_photo 接线 contents.place + amap_api_key AliasChoices；test_amap 9 项全过+真实调用验证（外滩→南京东路街道）
✅ Sentry 客户端接线：client/utils/sentry.ts 轻量 Envelope 协议上报（uni.request POST，零三方依赖——@sentry/vue App 端无 DOM 不可用）+ App.uvue onLaunch initSentry/onError + api.ts 5xx+网络失败上报
✅ 真值数据规格 v1（峰宝 grill-me 拍板）：docs/真值数据规格标准_v1.md（5 批字段级规格+人工采集操作流程）+ research/truth-data/ 模板 + scripts/validate_truth_data.py 校验器；定位=给产品部的人工采集手册，不做自动采集管道
✅ S-ST-1 后端分片集成：/upload/complete 接 meta→register_photo_content 建 contents+enqueue_high（语义对齐 /contents/upload）+ /upload/chunk POST 别名（uni.uploadFile 不支持 PUT）+ client/utils/uploader.ts v2（init→chunk→complete+断点续传 uni storage+GPS 入 meta+urlencoded 表单）+ chunk_size=单块（UTS 无可靠 ArrayBuffer 切片）；test_upload 13 项全过+OpenAPI 重导 41 路径
✅ S-MO-1 手动操作 UI：client/utils/event_ops.ts（confirmEvent/mergeEvent）+ 时间轴 L1/L2 卡片 ⋯ 菜单（确认/合并，target=相邻上一张 L1）；split 后置等 GET /events/{id}/items（本轮已补）
✅ review_agent 内存优化：SetFit/reranker 改 fp16（2.2GB→596MB，峰值 5.5-6GB→~1.75GB）+ smoke 跳过 reranker + OOM 友好提示；教训=commit 前确保可用内存 ≥4GB
设计来源：docs/parallel-dev/ 帧卡（S-ST-1/S-MO-1）+ 峰宝拍板记录（真值规格四决策）+ RAG 评测口径核实

### 遗留/待办
- 以图搜图延迟优化（P95 7.6s→<3s）——F5 真实缺口
- 双层 Rerank 第二层 qwen-flash 精排——F5 真实缺口
- S-MO-1 split 拆分 UI（需后端 GET /events/{id}/items）
- S-XV XView（等自定义基座波次）、S-EM-1 模拟器、离线 op_log、EXIF 兼容性排查
- 真值数据技术侧可选辅助：export 初稿脚本 / 人脸打码脚本 / 评测 --real 模式

---

## ✅ 速查卡 · 客户端第三波（2026-08-24 晚 · T-NA/T-TX/T-AU/T-SR/T-PL 多入口+玩法层）

✅ 15 任务 10 项真机验证 PASS（nova 11，提交 e6398cc；pytest 238 passed）：components/yishu-tabbar 四宫格导航（reLaunch+锈红选中态）｜pages/record+utils/text_recorder.ts 文字入口（分类 job 轮询+点标签三层裁决纠错）｜utils/voice.ts 语音入口（uni.getRecorderManager→/asr/transcribe→可编辑+情绪标签）｜pages/search+utils/search_api.ts 搜索（混合结果+trace 溯源+uni.chooseMedia 以图搜图+degraded 黄条）｜首页回响卡片（GET /echo/today+dismiss）｜pages/interview 冷启动访谈（三层披露→三问→复述确认）｜pages/messages 消息中心（未读过滤+已读）｜设计来源：docs/parallel-dev/ 帧卡（T-NA~T-PL）
- 本波 lessons +5（uni.chooseMedia/UTS setTimeout 自引用/接口结构/uiautomator 不可靠/HBuilderX CLI launch）已登记 docs/lessons.md

---

## ✅ 速查卡 · 客户端第二波 · 第二批（2026-08-24 晚 · S-AG-3/S-SY-4 客户端闭环）

✅ 端侧聚合+事件上云闭环（真机 E2E 2026-08-24 21:27 nova 11 全链路通过）：PhotoItem 增加 GPS（interface.uts+app-android/index.uts，MediaStore LATITUDE/LONGITUDE）｜uploader.ts 返回 content_id｜S-AG-3 端侧聚合运行器 client/utils/agg_runner.uts（UTS ST-DBSCAN 同 AGG-016 同参→L1 日卡片，client_event_id 幂等）｜S-SY-4 client/utils/event_sync.ts（POST /events/sync+指数退避 2/4/8s+4xx 停批）｜index 页接线（监听攒批→上传→聚合→上云→刷新）｜真机证据：10+ 批注入→multipart 200→端侧聚合 1 个 L1→上云 accepted=1→DB ev-1787578023265-857538→时间轴渲染+S-SY-5 前台自动恢复监听｜设计来源：docs/parallel-dev/ 帧卡（S-AG-3/S-SY-4/B3-6）
- 本批环境坑 lessons 已登记 docs/lessons.md（uni.uploadFile res.data 是 string/华为增强纯净模式拦安装/端侧 EXIF 兜底对 PIL 写入不生效/设备时钟错位/HBuilderX 弹窗阻塞/防火墙→adb reverse）

### 遗留（后续波次）
- S-XV XView（SQLCipher 随自定义基座波次，标准基座无三方依赖）；S-SY-4 离线 op_log 队列；S-EM-1 模拟器；S-ST-1 STS；S-MO-1 手动操作 UI
- 端侧 EXIF 兼容性（PIL 写入格式）待查
# Session Progress Log — 忆述光华

## ✅ 速查卡 · 2026-08-27 17:40 · 重构批次 H 集成（H1-H5 全并行 5 分支）

✅ 五分支 --no-ff 合入 develop（h1 models 拆包+event_aggregation 脚本迁移+pipeline 注册表+wechat 反转+upload 拆包 / h2 CI 增强 / h5 客户端收口 / h3 测试杂项 / h4 API 契约收口）+ openapi 重导 46 路径（arbitrate 迁 /classify）+ errors 增 AUTH_010-013/MSG_003；共享工作树事故（H3 ddc8e29 误落 h4 分支、test_queue 双 add 拼接）按规则处置；全量非 rag 661 passed + tsc EXIT=0｜设计来源：docs/重构批次H 提示词
- 遗留：HBuilderX 编译真机冒烟（H5 客户端行为等价，集成后补跑）；pip-audit-weekly 定时触发待 CI 观察；client tsc 非阻断试点

## ✅ 速查卡 · 2026-08-27 14:50 · 重构批次 G 集成（G1 认证安全 + G2 越权纵深）

✅ techdebt/g1（67f50f1，22 文件）+ techdebt/g2（1563cde，7 文件）合入 develop：G1 = refresh single-flight//auth/logout/HMAC 哈希/加盐/限流中间件（详见 G1 速查卡）；G2 = wechat 回调 timestamp ±300s 防重放（GET/POST 双入口）+ 安全响应头+生产关 /docs（create_app）+ /healthz 收敛 {status:ok} + sync_pull limit 422 SYNC_001；受影响域精准 113 passed + single-flight node 4/4；契约只增不减（openapi 45→46 仅 +logout）｜设计来源：重构批次 G 提示词（R6/R7）
- 遗留登记：REFRESH_TOKEN_HMAC_KEY 生产部署需在 Infisical/.env 配独立强随机密钥；限流阈值/白名单按部署环境复核

## ✅ 速查卡 · 客户端第二波 · 首批交付（2026-08-24 晚 · W5 起）

✅ 后端 S-SY-1 /api/v1/events/sync（client_event_id 幂等+部分唯一索引兜底+越权校验+L1 落库 generated_by=device+offline_queue 变更日志+云侧补 L2/L3 候选）+ S-SY-2 aggregate_user 重构（默认 mode="l2l3" 云侧只跑 L2/L3）+ 迁移 a1b2c3d4e5f6；test_event_sync 7 项+test_agg_reference 4 项（pytest 234 passed）
✅ 客户端 S-AG-1 UTS ST-DBSCAN 算法层（client/utils/agg/：agg_config.uts 参数单一来源/st_dbscan.uts/pipeline.uts 连拍折叠+GPS 漂移置空）+ S-AG-2 AGG-016 一致性（scripts/gen_agg_fixtures.py Python 同参双跑→fixtures.uts 10 用例 57 照片 + pages/debug/agg-check 自检页），nova 11 真机自检 10/10 PASS｜设计来源：docs/parallel-dev/ 帧卡（B3-6/S-AG-1/2）+ AGG-016 参考实现

## ✅ 速查卡 · 真机 E2E 全链路验收（2026-08-24 下午 · nova 11 FOA-AL00）

✅ 全链路通（B-UT/B-UP/B-F8/B-VA）：相册监听（ContentObserver）→ 游标去重 → 4s 静默窗口攒批 → multipart 上传 → 后端 EXIF → 云侧聚合 → F8 时间轴渲染｜当场修复四问题：首扫全量上传 9319 张存量相册隐私红线（游标初始化到 max(id)）、scan_file 不提取 EXIF（后端 PIL 权威解析）、并发双 ensureLogin 撞 devices 唯一约束（IntegrityError 兜底+客户端单飞）、fake 存储 512MB 上限误伤｜空状态/时间轴渲染截屏验证符合视觉规范；后端 EXIF 真值 taken_at=08-22(40)/08-23(10)｜设计来源：验收清单 B 系列 + 视觉规范 v1
- 遗留说明（保留原叙述）：手机时钟/时区错乱→日标签偏移（设备时间正常后自愈，非代码缺陷）；L2 语义归并待真实数据（P2-07 已知）；30s 门禁服务端链路 4.2s+5.6s 单进程验证，设备侧受 WiFi/扫描节奏影响

## ✅ 速查卡 · 客户端第一波（2026-08-24 · W3）

✅ 工程骨架+首链路：client/ uni-app x 工程（manifest/pages/main.uts/App.uvue + pages/index/index.uvue F8 时间轴）｜utils 五件：config.ts（baseURL 开关）/auth.ts（mock 登录+EncryptedSharedPreferences+401 refresh）/api.ts（统一请求+错误映射+全局 toast）/uploader.ts（并发≤3+重试2+进度）/timeline.ts（ISO 解析+日期分组）｜uni_modules/yishu-photo-watch UTS 插件（PhotoObserver.kt ContentObserver+游标去重+4s 攒批、SecurePrefs.kt EncryptedSharedPreferences）｜视觉规范 v1（相纸白 #F6F1E7/墨褐 #3A2E25/锈红 #B05A3A、衬线标题、撕边卡片、空状态 SVG）｜scripts/generate_test_photos.py（50 张带 EXIF，3 天 4 片段 L1/L2 真值已知）｜后端 POST /api/v1/contents/upload（multipart+meta→cos_key→contents→enqueue_high；9 测试+curl 冒烟）+ 50 张单进程全链验证（上传 4.2s+管线 5.6s）｜Harness：ruff.toml 排除 client/、review_agent _skip_path、feature_list F1/F8 in-progress｜设计来源：docs/parallel-dev/ 帧卡（B-BE/B-CL/B-UT/B-UP/B-F8）+ 视觉规范 v1

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

✅ 三方审查修复 51 项 checklist（2026-08-20）：P0 安全+数据正确性 7/7（上传 IDOR 归属/护栏 URL 早退/wechat 鉴权/搜索 epoch 秒 payload/caption 先下载/mock 转写生产拒绝/护栏未配 key 默认拒发[用户拍板]）+ P1 技术债 17/17（时间 500/分片校验/CORS/纠错噪音闸门/错误契约/N+1/长录音 VAD 分段等）+ P2 架构重构 7/7（推理移 worker/worker 拆分 services/pipeline.py/单例收敛/Alembic 落地/asr 域游标统一/L2L3 候选落库+以图搜图接线）+ P3 顺手清理（死依赖/死代码/conftest.py/fake 容量上限）+ 设计文档同步（OpenAPI 契约异步化节/MVP方案_v3 变更记录）｜设计来源：review-report.md/refactor-plan.md（已归位 docs/）
> ⚠️ P2-07 L2/L3 为候选级 draft 落库（generated_by=cloud-proto），LLM 语义归并待真实数据到位（**仍未闭环**）

### ✅ 已完成功能（对照 MVP F1-F9，2026-08-20 时点，后端域）

✅ F2 文字碎片/SetFit 分类｜F3 FunASR+SenseVoice 语音｜F4 三层裁决纠错｜F5 BGE-M3+Qdrant 描述性搜索（hit_rate@3=0.8182）｜F7 冷启动访谈｜P2 回响｜B4 LWW 同步/分片续传/对账｜消息中心+22:00 复盘｜微信"找"（沙箱）｜事件聚合 L1+手动 merge/split/confirm｜护栏（词库+网址黑名单+打码+LLM）——均为后端域 ✅，文件与方法细节见同期 git 历史与 review-report.md

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

## ✅ 速查卡 · 2026-08-24 · 远程仓库核对 + review_agent 修复 + 文档台账清理

✅ 远程核对：origin=zqhmy1234/YSGH-APP 仅 main @ 3869111「MVP 后端全量交付」与本地 develop 同 commit，无他人改动｜review_agent research 段修复：test_agent.py run_research_validation 旧路径 `research.event_aggregation` → `app.services.event_aggregation.run_validation`（--only research 全过）｜台账清理：pytest 数字统一 215 passed/重复标题清理/feature_list·session-handoff 同步｜设计来源：.cowork-temp/test-report.json 证据核查
## ✅ 速查卡 · 2026-08-25 · 本地声学情绪检测完成

✅ SenseVoiceSmall-onnx 本地 CPU 情绪通道（替代过时云端 sensevoice-v1 WAV 降级）：FFmpeg 统一解码 M4A/MP3/AAC/WAV→16kHz 单声道 float32 PCM｜7 类 logits 计算情绪置信度（<0.7 只记录不触发），独立保存 emotion_confidence/source/model/actionable（不再误存 ASR 置信度）｜降级边界：情绪失败留 sensevoice_emotion:* 审计但不抹真实转写，数字静音返回 no_speech｜生产 mock 护栏 MOCK_DISABLED｜真实验证：5 秒 M4A FunASR 转写+本地推理「平静」conf≈0.8741；定向测试 40 passed｜设计来源：B5a 语音设计；资产登记 backend/models/README.md

## ✅ 速查卡 · 2026-08-24 · ASR 多格式与入库状态收口

✅ Fun-ASR Flash 主通道（fun-asr-flash-2026-06-15 Data URI，AAC/AMR/FLAC/M4A/MP3/OGG/OPUS/WAV/WebM/WMA 十格式）+ 四态状态语义（succeeded/no_speech/failed_retryable/failed_final，失败写 content.status=failed+审计，消灭"无文本但 done"假完成）+ VAD 分段（内部长 WAV 单段≤4 分钟，>8MB 压缩音频要求切分/转 WAV）+ 可审计字段（模型/通道/供应商 request id/SHA-256/usage/segments/errors）+ 生产不降级 mock｜验证：ASR 服务/API 23 项+语音入库 5 项+内容 API 8 项（monkeypatch，未做真实线上转写回归）｜设计来源：B5a 语音设计 + 状态语义四态口径
- 待验收（未闭环）：配置临时 Key 后用真实多格式/空白录音/限流断网做线上验收；真实 WER 需团队 20-50 段标注录音校准

## ✅ 速查卡 · 2026-08-20 02:5x · 教训强制 Hook + 生产兜底待办开发

✅ scripts/lessons.py（add/recent/check_lessons，epoch 时间戳防时区漂移）+ review_agent 集成——失败后未登记教训阻断 commit（pre-commit 不可绕）｜services/external/retry.py with_retry（3 次指数退避+线程池超时+可重试异常判定）应用到 dashscope（_chat_text/image_caption）+ tencent_ci（test_retry 10 项）｜事件用户手动操作接线（B3-5/AGG-013）：merge/split/confirm 三端点（原 501）+ EventEditLog + confirmed 不再被算法改动（test_event_ops 7 项）｜pytest 190 全过｜设计来源：AGENTS.md #13 教训 + B3 设计 + 用户「程序化强制」要求
**待办**：①语音 COS 下载接存储层（worker 已留接口）②photo 生产 caption 真实调用验证 ③corpus-A 61 张 caption 补齐 ④上线评测集（50 条真实查询，等团队）⑤RAG 门禁调优（hit_rate 0.6667→0.70）

## ✅ 速查卡 · 2026-08-20 02:00 · AI 管线接线 + 模型清理 + 生产兜底审计

✅ process_content 全类型管线（worker.py，替代占位实现）：text→SetFit 分类 / voice→ASR 转写+分类 / photo→caption+CI 打标，全部→事件聚合；每步独立 try/except 静默失败（status 仍 done，明细入 extra.error）｜事件聚合落库（services/events.py + Event/EventItem ORM，L0+L1 写 L1 日卡片+event_items，同日去重合并，timeline 返回真实数据）｜关键修复：classifier.py 漏设 HF_HUB_OFFLINE=1 联网卡死 + SetFit classify_batch｜C 盘 HF 缓存清理 8.6GB→2.2GB + backend/models/README.md 资产清单｜pytest 173 全过｜设计来源：用户拍板（P0 文本/语音→P1 图片→P2 事件聚合，用户无感知失败）+ docs/生产兜底审计与交付差距盘点_20260820.md
**待办**：①外部 API 统一重试封装（dashscope/CI/OCR）②events merge/split/confirm 用户手动操作 ③语音 COS 下载接存储层 ④photo 生产 caption 真实调用（现 mock）⑤corpus-A 61 张 caption 补齐


## ✅ 速查卡 · 2026-08-25 · RAG 指标提升落地（4 PR）+ LLM 改写调研修复 + 测评报告

✅ P1-A 类目路由（规则词表→content_class 过滤+空结果回退，descriptive hit_rate@3 0.5→1.0）+ P0-B 显式相关口径（evaluate_retrieval_explicit，产品口径 recall@3=0.9167）+ P0-D 命中梯度（≥50% 词元 ×1.3/全命中 ×1.8，keyword precision@3 0.44→0.78）+ P1-B2 外部测试集（T2Ranking 70 查询/88 段，run_eval --external）——commit 4d6fca3｜LLM 改写门控修复：prompt v2（短关键词原样返回）+ 双路 eff_filters + 类目路由跑原始查询 + 路由固定规则版 + llm_rewrite_enabled 默认开｜指标：B+C hit_rate@3 0.7273→0.9091、mrr 0.85；EXT recall@3=0.8857｜交付 docs/RAG测评报告_20260825.md + docs/README 索引｜设计来源：RAG 测评报告（review 委员口径）

## ✅ 速查卡 · Wave 1 集成完成（2026-08-26 03:20）

✅ wave1-agentA（B2 搜索 786f134）+ wave1-agentC（B5b 护栏 23b55f4）合入 + 集成接线（main.py 注册 profile_sensitive_router / photo 首入库 payload 后补刷新 vector_store.update_payload+build_payload / 搜索·以图搜图规则级敏感过滤 filter_sensitive_rule）｜真实 bug 修复：检索阶段 _to_filter 缺 user_id 全库召回跨用户污染（+回归测试）｜312 passed + api_smoke 6/6 + review_agent 全绿｜设计来源：docs/parallel-dev/00-02 任务卡（B2/B5b）
- 待 key/环境：corpus-A 2 张 0 字节/审查拒绝；Qwen3-VL-Embedding 图片塔待开通（现 caption 路径+缓存）；NER LLM 兜底默认关

## ✅ 速查卡 · Wave 2 集成完成（2026-08-26 06:30）

✅ wave2-agentD（B3 云侧 7e8c142：L2 地点域连续 5km/12hr + LLM 归并裁决 qwen 真实通道验证 + L3 7 天窗/生命周期 + 封面选择人脸→质量→时间 + GPS 漂移 + confirmed 保护 + 增量先匹配后分裂 + OCR 内容维 + 15 新测试）+ wave2-agentE（B3 端侧+UI ae801a7：30min 保守开关 + 预处理去重 + L2 待确认区 UI + 封面/反向入口 + 30s 验收埋点 + AGG 双跑 14 用例 + 8 API 测试）+ 接线 fe1b376（云侧 AGG_CONFIG 对齐端侧 conservative_mode）｜341 passed｜设计来源：docs/parallel-dev/03-05 任务卡（B3）
- 待办：Content.extra quality_score/face_count 无写入方（内容管线未接腾讯 CI 人脸标签，封面选择回退时间居中）——记录待后续

## ✅ 速查卡 · Wave 3 集成完成（2026-08-26 14:30）

✅ 四 worktree 集成：F（M1 补遗 690596b：LLM 精排第二层仅真实判定换序 + 托管护栏 qwen_response_check + 50 条真值评测集 hit_rate@3=0.8571 + 改写层 11/11）+ G（B4 后端 feb3a09：缩略图管线 PIL→thumbnail_key→GET 懒生成 + upload_mode/on_wifi 流量约束 + 微信媒体下载→COS + 30 天清理 job）+ H（B4 客户端 0899ba6：sync_client 字段级同步六字段队列/op_id 幂等/增量拉取/reconcile/2h 定时 + 批量暂停/一键继续 + UploadStatusBanner）+ I（B1 画像 1f958fe：枚举集 JSON L0 51 维+L1 193 维 + profile_schema 加载器 + profile_annotator 双门槛/池/节流/查重 + 冷启动兴趣稀疏 5-10 维）+ 接线 f85a393 + 迁移 c7d8e9f0a1b2（profile_l2_evidence FK ON DELETE CASCADE）｜420 passed + 覆盖率 78.46% + api_smoke 6/6 + research 18｜设计来源：docs/parallel-dev/06-09 任务卡（B1/B4/M1）
- 待 key/环境：COS/微信企微/Sentry 未配（代码先行 mock 测）；托管护栏实网验证待 key；B/C/D 采集语料落地后重跑评测基线；纠错测量需真实 correction_log 数据；真机 nova 11 补验（H 的 WiFi 原图/蜂窝暂缓完整相册链路 + 后台 2h 定时归 Wave 4 K）

## ✅ 速查卡 · 收尾 Wave 1 集成完成（2026-08-27 21:45）

✅ 基线编译修复 39734fe（UTS 5.15 全量编译七处存量错误：upload_protocol/uploader/event_ops/play/event_sync/sync_client/record.uvue——增量编译 warm cache 掩盖教训）+ 八分支集成（B3 list_objects·HMAC·COS 直连 / B1 孤儿扫描 12 测试 / B2 time_suspect·export·copy_library / C1 压测报告 / C2 文案库 40 条池 / C3 隐私·归档·harness / A1 画像管理页 / A2 设置页·导出·契约 hub）+ 接线 617028c（export router，OpenAPI 47 路径）｜pytest 707 passed + 9 页面全量编译；真机 nova 11 启动/画像管理页/设置页可达｜设计来源：docs/parallel-dev-收尾/01-12 任务卡
- 遗留：A3（时间存疑+纠错提示 UI）与 D1（真机 7 清单）未开工（次日补集成，见下条）；压测 P0 未修复（后续 6e7c6d7 修复）；旧队列照片 upload/init 422（旧测试数据，新上传 api_smoke 验证正常）；设置页截图与导出闭环待用户目检
## ✅ 速查卡 · 收尾 Wave1 A3/D1 补集成完成（2026-08-27 23:59）

✅ wrap1-agentA3（merge d45898d：SuspectBadge 角标组件 + index.uvue 日卡片/待确认卡接入 + record.uvue 连续纠错弹层接线 + text_recorder.ts 本地计数 + timeline.ts 最小承接 time_suspect——A3 偏差经 INT 裁决采信：类字段默认 false 不改构造签名/FIELD_TIME_SUSPECT 常量只读引用）+ wrap1-agentD1（merge 6aea242：真机补验 7 清单 + README 前置总表 P1–P11 + adb_helpers.ps1，+930 行）｜门禁 G1-G5 全过（pytest 712 passed / --cleanCache 198 class 编译 / OpenAPI 47 路径零消失）；治理文档 19 份入库；A3 模拟器 E2E 全链路（角标两态+D2/D4 弹层+纠错清零，logcat+UI 树双证据，状态机镜像测试 9/9）｜设计来源：docs/parallel-dev-收尾/（A3/D1 任务卡 + 18 号契约）
- 遗留：nova 11 真机冒烟归 Wave 3（checklist_06）；D4 弹层"确认"后无自定义分类管理页（18 号契约只定义弹层）→ 当前 toast"即将上线"，页面需求待产品拍板；dismissCorrectionPrompt()（取消=不再提示）hook 已就绪未接线，待产品决策（09-01 定性 U3 悬置待重拍）

## ✅ 速查卡 · 收尾 Wave 4a 收口登记（2026-08-28）——代码侧记录闭环

✅ 交付 08_收尾波次完成汇报（4a 骨架版，◉ 完整 §0/§1.1/§1.3/§1.4 + ○ 真机占位待 4b；铁律：无 nova 11 实测不宣称 A 级）｜代码侧链条：编译修复 39734fe → 十分支集成（5e1463b B3/794deae B1/d119444 B2/6bf945f C1/5396b1e C2/98857df C3/d97999b A1/6e2bbd0 A2 + 补轮 d45898d A3/6aea242 D1）→ 接线 617028c → P0-2 性能修复 6e7c6d7（DB 池耗尽转 503/搜索并发转 429+Retry-After/模型加载健壮性）｜新页面收口：画像管理 US-40/41、设置页+导出 US-42、时间存疑 US-12、纠错弹层 US-25；后端契约 /api/v1/export+time_suspect（47 路径零消失）｜资产：orphan_scan.py/文案库 docs/copy_library//隐私政策+部署就绪包/负样本重校 harness/压测报告/内测包构建配置清单 20 号｜门禁 712 passed+全绿；任务卡状态列收口；按用户确认 4a 不 commit 不 push（后随收口落库）｜设计来源：docs/parallel-dev-收尾/16/21/22（4a/4b 归档卡）
- 4b 待填空位（22 号）：§1.2 证据升级表 / §1.5 快照真机数字 / feature_list 真机 A 级 evidence / 30s 计时门禁判定（已由 08 终版填写）

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

✅ 收官快照（补验前时点值）：功能代码 ~90%（代码侧缺口清零）/ 内测可达度 60–70% / 用户故事 ✅41/🟡12/❌0（A 级 27）/ 性能门禁 10/2/6 / 30s 门禁 🟡→✅（实测 6.0s≤30s）；真机 7 清单 01✅（US-46/47/48→A）03✅ 04✅（US-06/07→A）、02❌（D-06）、05🟡（US-17/18/19→A，S2 待校准）、06🟡、07🟡（云打包链路 A+D-18/D-19）；交付 08 终版 + feature_list 真机 A 级 evidence（f5c0c59）+ 状态列全目录收口｜设计来源：docs/parallel-dev-收尾/22 + 08 文档
- 缺陷移交 4b 修复批次（批次1 D-18/D-19 / 批次2 D-16/D-07/D-08 / 批次3 S2 校准+D-06 机型适配）——详见上节「收尾 Wave 3 真机补验·全波终局」与 tracker 19 §4（**多数至今未进 develop，见执行计划 §1 悬账**）
- 遗留（等待团队，映射 07 §8.1）：① 产品部 B/C/D 真值 + A 批负样本 + E/F/G 排期 + 正式文案库 ② 运营/合规 三申请 + 隐私政策签字 ③ 负责人 企微/微信/短信/uni-push + 内测包 M1/M4/M6 ④ AI 远期待办 00 §7 F1–F7（F1 性能复测优先）

> **[2026-08-29 整饬勘误]** 上条（收尾 Wave 完成 4b 终版）为补验前时点快照（✅41/🟡12、A 级 27）：随后补验 US-42（D-20 修复 f726942）与 US-12/25/40/41（6a7f0f9，cbc1751 同步 08/AGENTS）🟡→✅、A 级→**32**。现行唯一口径 = AGENTS.md「当前状态」节。

## ✅ 速查卡 · 2026-08-29 · harness 台账整饬（进度/说法/决策统一 + 错误经验汇编）

✅ 新建 docs/决策台账.md（§0 术语消歧 + §1-3 基线后拍板 30+ + §5 待拍板登记簿）+ docs/lessons-主题索引.md（131 条归 10 根因族）+ AGENTS.md 状态节刷新+五处同步纪律 + lessons 环境陷阱区补录 24-33 + session-handoff.md 重写 + feature_list.json/init.sh 口径统一；快速门禁 EXIT=0｜设计来源：用户整饬指令（五项债务清单：数字漂移/Wave 4 双义/handoff 滞留/决策无登记簿）
- 事故与自纠（诚实记录，lessons 族11）：lessons.py add 表头指针行触发「重建」分支毁台账 1199→28 行（HEAD~1 恢复+add() 改不销毁三分支）；edit 工具 CRLF 毁 init.sh 可执行性（二进制还原 LF+新建 .gitattributes）
- 授权销项（用户拍板「过时文档/空旧文件夹一律删」）：08/02 全表同步终值、删 4 份孤立执行类文档（引用核查后）、拔除 .wt 13 个旧 worktree+清空目录 464 个、docs/README 索引二轮更新


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
## ✅ 速查卡 · 2026-08-29 14:3x · 百炼真实链路加固 + 验证矩阵（feat/dashscope-backend-hardening，已 merge develop @ 5ed7c5c）

✅ 两处加固（08-28 真实评测报告实证反推）：llm_ops/rerank.py 解析三级兜底（标准解析→逐块正则打捞→_norm_ans 中英文布尔归一，根治 bool("false")==True 静默换序）+ rag/image.py VL 重试耗尽→过期缓存兜底（空 caption 不覆写缓存位）｜scripts/check_dashscope_matrix.py 9 链路真实矩阵：双通道（Infisical 注入/.env 直读）各 9/9 pass——rewrite/route/rerank 4/4 置顶 0.73-2.25s/guard chat+managed/fail_closed 拒发/Qwen3-VL 3.2-4.2s/caption 缓存兜底/event_merge conf 0.85；零 403 workspace 复发｜默认套件 664 passed｜设计来源：OPS-SECRETS「拿 Key 零代码切换」链路（证据 docs/百炼真实链路验证与加固_20260829.md）
- 遗留登记（不擅改默认值）：rerank 默认开与真实档 P95<3s 的张力→GPU/异步策略评审再拍板；fun-asr 模型开通态+WER 基线属他窗 asr 域；answer_quality「真实生成答案」接线为下一候选

## ✅ 速查卡 · 2026-08-29 15:5x · DASHSCOPE 补充任务验收合入 + wrap1 迁移窗侦察

✅ feat/dashscope-backend-hardening 验收并 merge develop @ 5ed7c5c：F5 百炼两处加固 + 9 链路真实矩阵（双路径 9/9）+ 默认套件 664 绿；独立复跑 test_image_search+test_rag 34 passed；文件域纪律良好（未碰 asr 域/openapi.json）；append 冲突双保留解冲；rag/image.py 旧脏抢救至 .cowork-temp/salvage patch｜wrap1-agentA2-ui-restore 侦察（efb183d，未合）：5.24 迁移实质落地（photo-watch 21 处 android 导入改 any/CSS 选择器迁移/Vapor 入口）+ 顺带 D-21 四页 scroll 修复；但 ~90MB 二进制入库+基线停在 677ea68 缺全部 R1——结论不可按现状 merge（后经整备于 09-01 合流 cea5025）｜设计来源：验收复跑证据 + 侦察报告
- fix/4b 策略：暂不再 rebase，等 wrap1 处置定局后一次到位（已执行，见 09-01 rebase 条）

## 2026-08-29 16:3x · 暗物质审计：两单「修了没入库」补落 + 主区旧脏三重备份清空

- **触发**：合入 dashscope 分支时主区唯一脏文件挡路 → 按拍板③做全量清空（salvage 快照分支 2bad295 三父含 untracked + patch + 112 文件直拷；PS 把 `stash@{0}` 花括号吞了致 stash 假 drop，SHA 从 drop 日志复活——教训已登记）。
- **审出**：①**D-02/D-03 修复从未提交**——committed develop 里 8 个 enqueue key-only 调用点带活雷（净基线照片管线 worker 必 TypeError），D-03 的 exif.py 全新文件压根不在 git，当年「真机复验 ✅」全跑在脏树上；②**文案库 v1**（08-29 拍板引用的 docs/copy_library/ + service + notify 接线）同样零入库。
- **补落 fix/4b 三枚**：8a0b27a（exif.py+pipeline+photo_content+register+test_pipeline，24 绿）→ 7589775（文案库五件+validate 校验器+notify 纯回退接线，7/7 绿）→ acce7ef（contents+wechat 收全 8/8 调用点+真值源合一，26 绿）。分支现 @ acce7ef。
- **其他**：dashscope 分支验收并 merge（5ed7c5c，双路径 9/9+复跑 34 绿）；evidence/「证据留本地」铁律 .gitignore 恢复入库；主区 `import app.main` 冒烟 55 路由 OK；内存告警 0.65GB（暂停重活）。


## ✅ 速查卡 · 2026-09-01 · W10 峰宝九条验机反馈全落地（合流 cea5025 入 develop）

✅ 九条全落地（对照表与差异降级登记 _diff_ledger.md §W10.4）：网络异常双根因修正（多版本 adb 互杀灭 reverse + monkey 拉活≠重启网络栈不复位；deploy_one.sh step2/3 顺序反转 reverse 先于启动）｜ai.uvue 全量重写 ~640 行（contract-first mock 契约 POST /api/v1/chat/messages→{reply}，kind=bubble/plain+chips/cards/confirm/typing 五形态族 seed 8 条+onSend 本地闭环+附件面板浮层；USE_MOCK_CHAT=true 显式登记）｜画像管理页（TabBar 组件接入/页头 44→60px/emoji→SVG 三枚/seed 三条敏感话题 GET 复核）｜账号与安全页删「数据导出」「存储空间」两行+SVG｜uvue 编译两枚新坑沉淀（type 字面量构造 MemCard/函数默认参数=[]，沉淀 ardot-to-uvue-css-restore）｜画布同步三板+终审通过（画布坑三枚沉淀 ardot-canvas-pitfalls）｜推包三轮收口：fOeJLH 轮询版三步全绿 1m57s｜设计来源：uvue_gen/*_canvas.json（ai/ai_reply/portrait_manage/account_security）+ 峰宝九条验机记录 + 拍板 A（复盘语音贴底 MessageDetailSheet）
- 遗留：四份画布快照（ai/ai_reply/portrait_manage/account_security）下会话开工前按 W9 重导；**ai.uvue 整页 mock 未接真链路（W5 mock 治理批，见执行计划 §1.1）**

## ✅ 速查卡 · 2026-09-01 15:5x · 重估与放行：wrap1 合流波主账追平（A 批）+ fix/4b rebase（B 批）

✅ A 批主账追平（用户放行）：全量侦察确认 08-30~09-01 跨窗活动（08-31 全页面系统审查 33 gap / 媒体票据 Valet Key 主区 8 文件 / 峰宝九条+W10.4 全量入库），**17 枚经 cea5025 跨日 merge 进 develop——5.24 迁移正式落地**（utils 21 文件 .ts→.uts/Vapor 启用/10 页零错零警+峰宝九条验机验收），origin 已同步；旧「客户端冻结待迁移」前提解除；待拍板 2→3 项（§5.8 AI mock 归宿）；审计报告抢救入库 docs/audit_20260831_*
✅ B 批（同日 17:26）：fix/4b rebase 8/8 成功（热修两枚按拍板让位丢弃零残留/record.uvue 守卫 graft 进 RecordSheet/.gitignore 豁免与工程根 manifest 幸存 check-ignore 实测；备份 fix4b-pre-rebase@acce7ef）+ 受影响后端 9 套件 138 全绿 + 统一冷编译 12 页一次过 ready 105s（唯一 WARNING backdrop-filter=wrap1 画布保留项）；fix/4b 现 @ d09f639｜设计来源：用户放行 A+B 拍板（⚠️ fix/4b 修复至今未进 develop，见执行计划 §1 悬账）

## 2026-09-01 19:0x · R1 尾段落地五枚 + 云打包发车 + 暗物质第三例（U3）

## 2026-09-01 15:5x · 重估与放行：wrap1 合流波主账追平（A 批）

- **触发**：用户令「重读 git/代码/台账，重判形势」→ 全量侦察发现 08-30~09-01 跨窗活动井喷：08-31 全页面系统审查（33 gap 静默吞/detail overlay 缺层）、媒体票据 Valet Key 新域（主区 8 文件在途）、09-01 峰宝九条验机+W10.4 全量入库，**17 枚经 cea5025 跨日 merge 进 develop——5.24 迁移正式落地**（utils 21 .ts→.uts/Vapor/零错零警），origin 已同步。
- **重估结论**：旧「客户端冻结待迁移」前提解除；主账（AGENTS/台账/handoff/tracker19）滞后 3 天空窗，本批追平（§1.11/O-4/D-21 入库状态/待拍板 2→3 项 §5.8 AI mock 归宿）；审计报告抢救入库 `docs/audit_20260831_*`；「20 单」数不变（AI mock 非缺陷入待拍板）。
- **用户放行 A+B**：A=本批；B=fix/4b rebase + 统一冷编译复验。
- **B 批战果（同日 17:26）**：rebase **8/8 成功**（热修两枚按拍板让位丢弃零残留/record.uvue 守卫随其拆迁 graft 进 RecordSheet 新结构/.gitignore 豁免与工程根 manifest 幸存 check-ignore 实测/contents.py 双方改动 auto-heal；备份 fix4b-pre-rebase@acce7ef）。受影响后端 9 套件 **138 全绿**（中途 12 败=机器重启致 Docker Desktop 未跑、yishu-redis/yishu-qdrant 随眠——拉起容器即愈，非代码问题）。**统一冷编译一次通过：12 页面编译成功 ready 105s**（唯一 WARNING backdrop-filter=wrap1 画布保留项）。fix/4b 现 @ d09f639，R1 冻结清单全线解冻，R2 真机波就绪。

## 2026-09-01 19:0x · R1 尾段落地五枚 + 云打包发车 + 暗物质第三例（U3）

- **rebase 后解冻清单实施完毕（fix/4b 新增五枚）**：`23e8604` D-22 客户端半（意图先流+仲裁纯参考，16 绿+编译过）→ `97eb795` D-07/D-08 语音链（短录音带音频入库+250s 分流黑洞修复+超时伸缩+失败段持久队列，test_pipeline 25 绿）→ `2a0194b` D-10/D-12（parseBody 安全解析+回退链 warn）→ `381a210` D-05/D-14（聚合共口+四路同源+双队列去重）。tracker §4 七行状态同步。
- **worktree 事故与自愈**：主仓 `.git/worktrees/` 被清理 Agent 误注销（三 worktree 全掉注册，分支/文件零损失）——fix4b 重建注册+`.env` 回迁+**无缓存全量冷编译两次通过**；wrap1 worktree 旧目录原样保留（其窗在途 uvue_gen 清理现场），dashscope worktree 已合流无需重建。禁碰清单已重申。
- **🌑 暗物质第三例（O-5）**：`dismissCorrectionPrompt` 及 D4 弹窗整条特性从未提交（只活在 stash 树），台账 5.3「hook 已就绪」前提崩塌——**U3 悬置待重拍（A 回炉移植/B 二期/C R2 后评）**。
- **R2 云打包已发车**（后台，Vapor 自定义基座，参数含 `--project` 绝对路径新规）；包出→aapt 尸检（D-19 service 注册）→真机装基座（先查纯净模式）→D-18/19 日志复验→纠错/语音/断网重传真机电池。内存 4GB 档，HBuilderX 独占已协调。

## ✅ 速查卡 · 2026-09-11 02:47 · 工单 B 段收口：两插件接入原生管线 + bg-tasks 依赖切 maven（commit 698881d）

✅ **工单 B 段（`docs/工单_UTS插件修复_20260911.md`）代码侧与出包侧全绿**：develop @ `698881d`（23 files changed, 204+/71-，12 jar deleted）｜云打包**一次过**（02:38:41→02:42:55，4 分 14 秒 → `client/unpackage/debug/android_debug_vapor.apk` 32,585,173 B）｜**B3 六探针 APK 全 dex 6/6 全 True**（yishuPhotoWatch / DataSyncService / uni-UNIYISHU001 / BgTaskManager / yishuRecorder / RecorderController）｜**运行期闭包全绿**：WorkManagerImpl + WorkManagerInitializer + `androidx/room` + `androidx/sqlite` + `androidx/startup` 全在包（jar 通路缺的就是后三项）｜**合并后 manifest 实证**：`androidx.startup.InitializationProvider` + `androidx.work.WorkManagerInitializer` + `/uts.sdk.modules.yishuPhotoWatch.DataSyncService`（与 dex 实包逐字一致）+ authority `com.yishu.guanghua.androidx-startup`（provider 真被合并）+ FGS 权限 + work 五组件 + Room 服务｜**根因消除式通过**：切 maven 前 `:app:checkReleaseDuplicateClasses` 报 2688 条重复类直接失败，删 12 手工 jar 后零重复类一次过（非参数绕过）
- **转正三件**：① 手工 `libs/*.jar` 堆被判定为**结构性错误方案**——`*-classes.jar` 无 AndroidManifest ⇒ startup 组件不参与合并、WorkManager 永不自动初始化；无传递闭包 ⇒ 零 Room 而 WorkDatabase 引用其 5 处 ⇒ 运行期必崩；而 `Class.forName` 探针仍返回 true ⇒ **探针假通过**。现已全删并改 `config.json` 声明 `androidx.work:work-runtime:2.9.1`。② UTS 剪枝规则实锤：未 `export` 且无引用的 class 整体不编译（`DataSyncService` 连 Kotlin 中间产物都不生成）。③ 插件根代理不转发 `interface.uts` 再导出符号 ⇒ 定「行为走根 import、数据类走声明家」分配法。
- **环境修复两件（工作区外，已备份可逆）**：`~/.gradle` 发行版 `gradle-base-ide-plugins-8.13.jar` 原为 **0 字节**（本地 maven 快验跑不起来的真因），从官方 zip 抽出补齐（0→15,200 B）｜`~/.gradle/gradle.properties` 代理指向 **127.0.0.1:7890 死端口**（Gradle 把它报成 `Plugin com.android.application was not found`，症状与真因不同层），改直连后本地「三方依赖更新完成 + 编译成功」
- **教训登记三条**（`docs/lessons.md`，commit 698881d）：手工 classes-jar 探针假通过｜uni_modules 禁用 libs jar 堆叠（重复类 + 缺 manifest 合并）｜gradle 报「插件找不到」先测代理端口
- **遗留（未闭环）**：**B4 关单 ❌** —— D-18/D-19 待真机行为验（照片拍完杀 App 后台同步是否续跑、`isWorkManagerAvailable()` 真值）。⚠️ 工单 B4「销 `docs/远期待办总账.md` 条目」前提不成立，D-18/D-19 实际登记在 tracker 19 §4 + `docs/4b_R2_出包卡点与D19根因诊断_20260901.md` + lessons 第 29 条，关单时改销这三处。台账权威 = `_diff_ledger.md` §PP/§PP-处置/§PP-终验。

## 2026-09-21 · 内测下发准备（阶段0-3 + E1 + C3 可完成部分）

- **结论：只差换址**。换址仅 2 处（客户端 config.uts 的 PROD_BASE_URL、服务器 deploy/.env 的 BASE_URL），清单见 deploy/ONE_SWAP.md；换完跑 deploy/scripts/preflight_pack.ps1，全绿（退出码 0）才允许打包。
- **P0 补齐（暗物质族）**：usesCleartextTraffic 在五处文档被写成"已生效"，代码里从未实现 → 已补（client/AndroidManifest.xml，含 tools:replace）。调试基座自带放行，故只在出包后暴露（现象=所有页面全空）。
- **出包前置门可执行化**：三条手敲 grep → deploy/scripts/preflight_pack.ps1（7 段检查，退出码即判据）。实测 1 FAIL，且唯一 FAIL 就是地址占位。
- C3 可完成部分落成：图标 4 档 + 启动图 3 档 + Android12 Logo 3 档（10 PNG / 441KB；schema 取证过程见 docs/图标与签名证书_20260921.md）；自有签名证书已生成（仓库外存放），证书 MD5 48:D7:6D:BA:80:AC:66:CB:5E:CD:2B:8C:BE:99:25:FC。
- 顺带修复：security.uvue 微信行「已绑定」失实 → 「未绑定」；auth.uts 畸形 200 响应由 null 强转崩溃改为优雅失败；config.uts 占位守卫泛化（特征词）。
- 更正：logout() 并非零调用，settings.uvue → security.uvue 链路早已存在。
- ⚠️ **本批全部改动仍在工作树未提交**（含 S1/S2/S3/C1/C2/C3/E1，30+ 项）。

## ✅ 速查卡 · 2026-09-24 · 易扩展/易维护重构波（逐功能域只读审计 + 结构棘轮 + 脚本清理）

✅ **先审计后执行**（用户拍板节奏）｜**B1 结构棘轮**（`6acd826`）：`scripts/audit_harness.py` 新增**轴 5 文件体积**（backend/app `*.py` ≤600 / client `*.uts|*.uvue` ≤800——存量 7 个基线冻结、**新增超阈即 CRITICAL**、存量增长 WARN）+ **轴 6 重复模式**（`deleted_at.is_(None)` 手抄总数不得上升，基线 44 处/21 文件）+ **轴 3 修镜像表假阳性**（`models/memory_agent.py` 镜像模型 0 引用属**预期**）；`scripts/review_agent.py` 新增 `structure` 检查（接提交门禁，快/全量均跑、秒级）；基线 `scripts/audit_harness_baseline.json` **只允许收缩**（每条带 reason/target）｜**B3 归属校验＝验证即达标**（`pytest backend/tests/test_authz_gate.py` → 6 passed，AST 门禁确认按 id 路由端点无漏网）｜**B6 声明漂移＝验证即达标**（`audit_harness client` → 无注释内未注册页面引用；§3.1 三则 09-23 已修 + 残余「对齐原 …」被轴 2 抑制）｜**B7 脚本卫生**（`09708d7`）：`check_schema_drift.py` 移除内联默认凭据 `postgres:admin` → `--admin-url`/`DRIFT_ADMIN_URL`（缺则 exit 2）、`backup_pg.ps1` PGBin 探测+显式报错、个人绝对路径清理｜**B4 AG8 漂移＝仅登记**（实跑 `alembic check` 取证：漂移含**破坏性** `remove_fk`/`remove_index` 与大量 TEXT↔String → 自动生成迁移会破坏约束，**待拍板 §5.9**）｜**本会话顺延（均给理由，非偷懒）**：B2 后端巨文件拆分（`api/contents.py` 902 行实为 **4 router 混居**，天然拆法＝转 `api/contents/` 子包；部分抽取不解除棘轮故收益为零；须专用验证窗口）、B5 客户端拆分（编译门 HBuilderX GUI + Docker 不可用，用户拍板顺延）｜**门禁纪律（本次新落地）**：新增门禁**必须先做负向探针自校验**——清空基线应全数命中（本次实测体积 7 条 / dup 1 条 CRITICAL），证明非空转；先例＝`audit_harness` 曾误报 44 条假死路由、本次又实测 1 条镜像假 CRITICAL，**假阳性比缺陷更危险**（会污染台账）｜**验证**：基线 `pytest -q` **848 passed 4 skipped**（82s）；`review_agent` 快/全量全绿、hook 覆盖、**未用 `--no-verify`**｜台账权威＝`docs/决策台账.md` §4.13/§5.9 + `.trae/specs/refactor-extensibility-maintainability-wave/refactor-ledger.md` §8。

## ✅ 速查卡 · 2026-09-24（第二轮）· 重构波 Phase A 深审重做 + Phase B B2/B3′/B10/B11/B12

✅ **抗断连重启 Phase A**（上轮 7 并行 subagent 全挂、仅浅层统计）→ 改**串行小批量**（每批 2 域、单域一 agent、独立报告、失败即续跑）**14 域全部完成**：产出 `audit/domain-01..14-*.md` + `audit/_PHASEA_ROLLUP.md`（**249 条 = 结构类 185 + 功能与安全缺陷 64**；P0 17/P1 111/P2 121；候审 30）；结论并入 `refactor-ledger.md` **§9**（含 §9.1 P0 清单 / §9.2 功能缺陷移交清单 / §9.3 旧条目修正 / §9.4 B2 方案 / §9.5 扩展成本 / §9.6 端云一致性）｜**旧台账 4 处被推翻**：L-11 守卫 `test_agent_schema_alignment.py` 是**幻影**（全仓+git 全历史 0 命中）、B3「验证即达标」**证伪**（`test_authz_gate.py:47` 登记名 `load_owned_capsule` ≠ 真符号 `_load_owned_capsule`＝**门禁哑条目**）、L-12 **升 P0**（令牌机制覆盖率 0%）、在途名单解除｜**Phase B 第二轮**：**B2** `contents.py` 902→`api/contents/` 子包（`__init__` **524 行**，轴5 棘轮销项 7→6；AST 比对 31/31 顶层语句一致）；**B2′** 修 B2 引发的门禁覆盖回归（`glob("*.py")`→**`rglob`**，候选 20+→**29**，新增 `test_scan_covers_subpackages`）；**B3′** `deps.load_owned_entity` 泛化 + 三 helper 委托 + 新增 `test_ownership_loaders_all_exist`；**B10** `review_agent` 新增 **`audit_axes`**（轴 1–4 此前**零自动门禁**）+ CI `audit_harness all` 阻断式 + 重复模式 per_file 双 gate + 镜像豁免收窄 + 体积阈值由基线驱动（负向探针证明非空转）；**B12** **D13-1 恒绿假门禁退役**（CI client tsc 步骤 `continue-on-error` + 末尾无条件 `exit 0`、`client/tsconfig*.json` 不存在、tsc 不认 `.uts`）→ 阻断式 `audit_harness client`；**B11** 安全子集（D04-17/D13-28/D13-14/D14-2）｜**验证**：全量 `pytest backend/tests` → **850 passed / 4 skipped**（较基线 848 = 新增 2 条门禁自检，零回归）；`audit_harness all` 无 CRITICAL；`openapi` 67/67 对齐｜**未完成（显式登记，未以未验证冒充完成）**：**B9** 端口/边界收口、**B10-b** 门禁缺口补强（D04-15 夹具无鉴别力 / D11-2 / D12-9）、**B11 残余** 7 项、**B12 剩余**（D13-3 实跑即红→先补模板）、**B5**（编译门不可用顺延）｜**待拍板**：§5.10 三项（64 条功能缺陷归属波次 / B9 排期 / schema.sql vs ORM 权威）｜教训登记 2 条（幻影守卫、门禁哑条目）｜台账权威＝`docs/决策台账.md` §4.14/§5.10 + `refactor-ledger.md` §8.1/§8.2/§9.7。

## ⚠️ 环境事件 · 2026-09-24 · Docker Desktop 未运行 ⇒ Qdrant 类测试不可用

- 现象：`review_agent --full` 的 **tests 段失败**：`backend/tests` 全量 = **841 passed / 13 failed / 4 skipped**（耗时 246s，正常约 77s）。13 项失败**全部**为 `qdrant_client.http.exceptions.ResponseHandlingException: timed out` + 搜索类断言未命中（`test_correction.py`×9、`test_pipeline.py`×2、`test_search.py`×1、`test_data_chains_12.py`×1）。
- 根因：`docker ps` → `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`；`Get-Process 'Docker Desktop'` 未运行。即 **AGENTS「已知环境项」所述：测试依赖 Docker Desktop（yishu-redis / yishu-qdrant 容器，机器重启即眠）**。
- 处置：已尝试 `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"` 并等待 90s×2 → 引擎管道仍未就绪（一度返回 500 Initializing）。**非代码问题**；下一会话须先起 Docker Desktop 再跑全量。
- 影响面隔离：本次改动面＝`core/errors.py`（补登记 4 枚）+ `tests/test_error_registry.py`（门禁扩展）+ `scripts/audit_harness.py`（轴 2 反向对拍），**不触碰任何 Qdrant/检索代码**；改动面定向验证 `pytest test_error_registry + test_ba2_capsule` → **14 passed**；`audit_harness all` → 无 CRITICAL。
- 备注：同一日内**首次** `pytest backend/tests` 曾 **850 passed / 4 skipped 全绿**（77s），随后 Docker 熄火 → 证明失败非本次改动引入。

## ✅ 速查卡 · 2026-09-24（第三轮）· 门禁正确性收口（B10-b / B10-c / B10-d / D13-3）

✅ 均为**纯本地可验证**的门禁巩固（Docker/Qdrant 不可用期间的可推进面）：
- **B10-b**（`5690f99`）：**D11-2** 错误码 AST 门禁跨模块盲区修复（旧实现 `capsules.py → []` 静默丢弃，新实现 `→ CAPSULE_001..004`）＋ **D11-1（P0）闭环**（`core/errors.py` 补登记 4 枚，http 与 raise 逐条一致 422/404/409/409）＋ **D12-9** 轴 2 补 `pages.json → 物理文件` 反向对拍（`uvue_gen/` 判定不纳入，理由入 docstring）。
- **D13-3**（`8555351`）：`check_env_template.py` 从「唯一判据但零门禁调用」变成**真门禁**——模板 §7 补齐 `AGENT_SERVICE_*`（TOKEN 守"必填不留空值"纪律保持注释），`review_agent` 新增 `env_template` 阻断式检查 + CI 独立步骤；反向探针证红。
- **B10-c**（`b980ac0`）：**D11-5 反向棘轮**——`registered − raised = 恰好 3 枚`（67/64），新增 `test_no_new_dead_error_codes`（**双向**：新增死登记 → 红；基线项销项未移除 → 也红）＋ `DEAD_CODE_BASELINE`；内存级负向探针证明非空转（未改任何业务文件）。另 **D07-13 改判为非缺陷**（`validate()` 仅测试调用 ⇒ 测试期契约断言，非硬编码陷阱）。
- **B10-d**（`cd712e0`）：**轴 4 `EXPORT_RE` 假阴性**（深审 249 条未涵盖的新发现）——`^\s*export` 的 `\s` **含换行** ⇒ 匹配点落前导空行 ⇒ 「声明行自身不算引用」排除失配 ⇒ **每个导出把自身计 1 引用** ⇒ `zero_cap` 应为 4 却**恒为 0**（**轴 4 长期假绿**；298 导出中 56 个声明行偏移）。修为 `^[ \t]*` 后**露出 4 枚真零调用能力导出**：`AuthError`(`auth.uts:51`)、`getRecorder`/`lastTempFile`(`voice.uts:104/108`)、`stopPeriodicSync`(`sync_client.uts:584`＝深审 D08-10)；前三枚为**深审未涵盖的新发现**（249 条计数下轮应 +3）。按 B1 口径加轴 4 棘轮（基线冻结存量 + 只拦新增 + **清算僵尸豁免**）；4 枚刻意不删（客户端 `.uts`，须编译门）。双负向探针（新增导出 → CRITICAL 且行号正确；基线塞假条目 → 僵尸豁免 CRITICAL）均已还原。
｜教训新增 1 条（**未提交改动所在文件禁用 `git checkout` 还原探针**——本次曾把未提交的 baseline `exports` 段误回滚，已重新补回并提交）｜**待办不变**：B9（须 Qdrant 可验证）、B5（须编译门）、B11 残余 6 项（多需 §5.10 决策）、64 条功能缺陷归属

- 续（同轮）：**B10-e**（`a21e999`）filesize/dup 基线**僵尸豁免清算**——"已不再超阈/已归零"的基线条目报 CRITICAL（口径同轴 4），先逐条对账确认不引入误红（6 filesize + 21 dup per_file 全 OK、total 44=44），双负向探针（阈值抬高→6 条僵尸 CRITICAL；塞不存在文件→1 条）均 EXIT=1、基线按原文精确还原。
- 续：**B10-f**（`58bd68c`）轴 4 **覆盖面补全**（D14-16）——`glob→rglob`（`client/utils/agg/**` 子目录此前**整体不在扫描面**）+ `EXPORT_RE` 补 `default/type/interface/enum`；先量化（+29 导出、其中零调用 1＝`WALK_SPEED_MS`＝深审 D04-12；补 7 类 +5 导出、零调用 0）⇒ 修后导出 **298 → 332**、存量 5；子目录负向探针真红且行号正确。附教训：长正则拆相邻字面量避 E501。
- 续：**B10-g**（`6aaf6a4`）契约快照**再生脚本 + 断言**（D14-7）——新增 `scripts/gen_openapi.py`（写入/`--check`）并接入 `review_agent` 的 `openapi_snapshot`（0 放行/1 阻断/2 环境降级）；**实证现有快照与后端逐字节一致**（239518 字符，此前无任何机器能证）；负向探针（塞幽灵路径）→ 精确定位「快照有而后端无（1）」、门禁阻断，再生后与 HEAD 逐字节一致。附教训：快照单一再生入口，人工手改＝字段级漂移无人能证。
- 续：同轮另修 **B10-d**（`cd712e0`）轴 4 `EXPORT_RE` **假阴性**（`\s` 含换行 → 声明行排除失配 → `zero_cap` 应为 4 却恒为 0）＋ 轴 4 棘轮；**B10-c**（`b980ac0`）错误码**反向棘轮**；**D13-3**（`8555351`）模板补齐 + `env_template` 门禁；**B10-b**（`5690f99`）错误码门禁跨模块盲区 + D11-1 闭环 + 轴 2 反向对拍。**门禁自身可靠性成为本轮主线**（6 个"看着绿实则没覆盖"的假门禁被逐一坐实并修复）。
- 续（第四轮）：**B10-h**（`84f4d20`）**静默转绿 + 覆盖率双阈值**——缺 ruff（`code==127`）/缺 pytest 此前**静默返回 True + [skip]**（报告全绿而 lint/测试从未执行）→ 改为**默认阻断**，仅 `--allow-missing-tools` 可见放宽；覆盖率阈值收敛为单一 `COV_THRESHOLD = 60`（此前本工具 50 vs CI 60 ⇒ 本地绿≠CI 绿）。内存级探针（monkeypatch `run`）验证 argv=60、缺工具默认 `ok=False`。**待环境验证**：50→60 的实际覆盖率须 Docker 可用后跑 `--full`。
- 续：**B10-j**（`f8c407e`）轴 2 **注释判定精确化**（D14-14/15）——行级近似「本行有无 `//`」⇒ **既误报**（块注释/多行 HTML 注释内页面引用被判死路由、注释内 `.ts` 提及报 CRITICAL）**又漏报**（字符串 `http://x` 之后被判为注释 ⇒ 真死路由漏报）；改字符级 `_comment_ranges()`（跳字符串字面量）+ `_in_comment(offset)`。纯函数对照六例 + 文件级负向探针双证。
- 续：**B10-k**（`0b1ec57`）**退出码口径统一**（D14-18）——`review_agent` 落地 **0 通过 / 1 违规 / 2 环境错误**（`ENV_ERR_PREFIX` 打标 + 纯函数 `_classify_failure()` 违规优先 + 报告 `env_blocked_checks`）；此前该工具只有 0/1 ⇒ 环境问题被误报为"代码违规"。5 例纯函数探针全对。残余：`test_agent` 仍只产 0/1（仅加 docstring 交叉引用）。
- 续（第五轮）：**B10-l**（`c9b10d7`）**密钥模式集唯一来源**（D14-9）——`review_agent` 与 `audit_security` 此前**各维护一套且互有缺口**（审计认得腾讯云 `AKID…`/宽版 `sk-`/宽版私钥，提交门禁看不到）⇒ 同一份密钥可能被放行。新建 `scripts/secret_patterns.py`（并集 16 条）共用 + 新增**显式行内豁免** `pragma: allowlist secret`（刻意不用"整目录跳过 tests/"，避免放过真密钥）。探针证明：两处同源、**并集缺失 = []**、四类形态全覆盖；放宽后当场暴露 `backend/tests` 4 处合成值误报 → 改多行写法并逐行标注 → 静态全绿、`test_guard_managed` 9 passed。
- 续：**B10-m**（`c9b10d7`）**`audit_security` 崩溃 + 白名单失效**（既有缺陷）——① `db/models` 早已拆包而代码仍直读 `models.py` ⇒ `FileNotFoundError` ⇒ **该安全审计根本无法跑完**（用 `git show HEAD:` 原始版本复跑同样崩）；② `_ALLOW_PATHS` 用仓库根相对前缀 ⇒ `backend/tests/…` 不匹配、"排除测试"意图**从未生效**；③ 统一合成值抑制口径。修复后**首次真正跑通**，`blocking` 由"跑不完"变为 **1**。
- ⚠️ **重大运维发现（交运维/用户，非本波可修）**：`audit_security` 跑通后报 **最近备份距今 493.0h（≈20.5 天）**，**违反 RPO≤24h**；`key_management/transport/storage` 全 OK。此前该检查因崩溃**从未被评估**（D13-4 记的"RPO 检查恒通过"实为"压根没跑"）——建议尽快跑一次 `scripts/backup_pg.ps1` 并恢复每日备份。
- 续（第六轮）：**B10-n**（`96c39f6`）**UTF-8 兜底同语义 20 处 → 单一实现**（D14-11）——同一段"把 stdout/stderr 切 UTF-8"的代码在 `scripts/*.py` 抄了 **20 处 / 16 文件**，且形态四分五裂（成对 hasattr 守卫 / 仅 stdout / 函数内 / `__import__` 变体 / `for _stream`+suppress / 无守卫裸调用）⇒ 想加一层兜底须改 20 处且必漏。新增 `scripts/gate_io.py::force_utf8()`（安全超集）并全量迁移。证据：`scripts/` 全目录 ruff 通过、17 改动脚本 py_compile 通过、**残留 reconfigure 仅剩 gate_io 自身**；门禁工具实跑全绿。
- 续（第七轮）：**B10-o**（`e7e449b`）**退出码口径单一来源 + 修 `test_agent` 姊妹假绿**——① 口径与判定抽到 `scripts/gate_exit.py`（`ENV_ERR_PREFIX`+`classify_failure`），`review_agent`/`test_agent` 共用；② **又一个"报告全绿但测试从未跑"**：`test_agent` 在 pytest/pytest-cov 缺失时 `return True + [skip] 缺依赖` ⇒ 改为**环境错误（退出码 2）**默认阻断；③ `test_agent` 加 `env_blocked_sections` + 0/1/2 三态；④ `audit_harness` 排除集 `CLIENT_EXCLUDE_DIRS` 合并硬编码 2 处（D14-13 半）。证据：探针 4 例全对（含违规优先）+ 源码断言无旧写法；`scripts/` 全目录 ruff 通过；三工具实跑正常。

## ⛔ 第七轮结束状态 · 剩余范围全部受阻（需用户动作或外部状态变化）

- **B9 端口/边界收口**：验证测试依赖 **Qdrant**；**Docker Desktop 自本轮起连续多轮均未运行**（每轮 `docker ps` 均报引擎管道不存在，且 `Start-Process` 等待 90s×2 仍未就绪）⇒ **无法验证、不做**（遵循"未跑验证不许声称完成"）。
- **B5 客户端拆分 / 客户端侧漂移（D04-3 等）**：需 HBuilderX **编译门**，本机不可用。
- **B11 残余 6 项**（D02-7/9、D05-9、D07-14、D09-10、D04-3、D12-7）：多需 **`docs/决策台账.md §5.10` 的权威决策**（尤以 `schema.sql` vs ORM），或落在客户端 `.uts`（同样受编译门限制）。
- **64 条功能与安全缺陷**（含 P0 10 条）：待用户拍板**归属波次**（另开功能波 / 并入 4b）。
- **门禁域残余（低价值）**：`D14-12` subprocess 双实现（签名差异 + OOM 分支）、两工具**跨文件**目录排除清单、`D14-22` 报告 schema、`D14-23` CI schema-drift job 仅 schedule —— 均为外观/一致性级，已登记待后续顺手做。
- **待环境验证（如实标注）**：`COV_THRESHOLD` 50→60 后实际覆盖率是否 ≥60，须 Docker 可用后跑 `--full`。
- **待用户处置的运维事项**：**最近备份距今 ≈20.5 天（违反 RPO≤24h）**——由 `audit_security` 首次跑通后暴露。

## ✅ 第八轮 · Docker 启动后：解除阻塞 + 完成 B9（端口/边界收口）

- **环境解锁**：Docker Desktop 由 `starting` 变为就绪（重启一次后）→ `yishu-redis` / `yishu-qdrant` Up；**Qdrant HTTP 200**（`yishu_contents`/`corrections`/`yishu_test_rag` 等集合齐备）⇒ 全量测试与 B9 验证面恢复可用。
- **B10-p 修导入回归**（Docker 起后全量门禁当场抓到）：B10-n 把样板抽成同目录 `gate_io` 后，**5 个会被后端按包导入**（`from scripts.x import …`）的脚本在该路径下 `sys.path` 无 `scripts/` ⇒ `ModuleNotFoundError` ⇒ **pytest 收集中断 + research 段崩**。修：双路径导入。证据：五处按包导入冒烟全成功、受影响测试 **21 passed**、research ✅。**教训已登记**：`py_compile`/`ruff` 查不出导入期缺模块。
- **B9a**（`44a837c`）事件域门面收口：`l3_lifecycle` 由 `events/timeline.py` 再导出 ⇒ **API 层不再直连算法包**（D04-16）。证据：`test_event_sync + test_agg_reference` **23 passed**；API→`event_aggregation` 直连 = 0。
- **B9b**（`32b4a89`）rag 的 LLM 调用全走 `llm_ops` 门面：`rewrite_query` 改导入 + 新增 `llm_ops.image_caption` 转发（`prompt=None` 沿默认提示词免漂移；属性访问转发保住测试打桩面）⇒ rag→`external.dashscope` 直连归零（D05-13）。证据：5 文件 **79 passed**。
- **B9c**（`8286327`）存储后端**注册表** + COS **唯一构造点**（D02-3/D02-13）：`BackendSpec` 注册项 ⇒ **新增后端只改 1 行**（原 7 处）；三单例全局收敛为一个 `_INSTANCES`；`build_cos_raw_client()` 唯一构造点；`cos_sts_configured()` 归位存储层 ⇒ **API 不再直读 COS 私有字段**。证据：**探针 8 项断言全过**（含 fake 容量守卫仍生效、源码断言唯一构造点）；4 文件 **83 passed**。
- **D05-16 改判**：`correction._get_store()` 早已用共享 `get_qdrant_client()`（P2-04）⇒「自建第二套 Qdrant」已过时；残余"裸调用未走门面"是无收益间接层且会**变更 collection schema**（行为变更）⇒ 登记不改。
- **验收（第八轮末）**：**全量 pytest 856 passed / 4 skipped / 20 deselected（EXIT=0）**；**`review_agent --full` ✅ 全绿（EXIT=0）**，含 `tests`（pytest + api_smoke + research）与 `openapi_snapshot`；**覆盖率 84.10% ≥ 60%**（阈值 50→60 收紧已实证）。工作树干净；门禁钩子全程生效、未用 `--no-verify`。
- **B9 收口后仍待办**：B5（需 HBuilderX 编译门）；B11 残余 6 项（多需 `决策台账 §5.10` 拍板）；**64 条功能与安全缺陷**待拍板归属波次；门禁域低价值残余（D14-12/22/23）；**运维：备份超期 ≈20.5 天（RPO≤24h）**。

## ✅ 第九轮 · 编译门恢复（用户手动起 HBuilderX）：B5a/B5b/B5c 客户端拆分 + B10-q 门禁修正

- **前置（用户拍板"备份过期了就删，其余按推荐"）**：重跑 `scripts/backup_pg.ps1` 出**新 dump 并 pg_restore 验证（388 个 TOC 条目）**，删除过期备份 ⇒ `audit_security` 的 `backup` 项**阻断数归零**（此前 493h 超期项已消）。§5.10 待拍板 3 项按推荐落定（登记 `docs/决策台账.md` §5.10）。**编译门**：用户**手动启动 HBuilderX** GUIs ⇒ `cli launch app-android --compile true` 可用（B5 由"顺延"解除）；HBuilderX 5.24 迁移已在 09-01 落地，develop 基线冷编译干净。
- **B5a（`000101f`）`play.uts` 按域拆分**（L-04）：653 行 6 域 → `play_{echo,interview,messages,favorite,trash,content}.uts`（70/125/182/53/63/181），10 调用点改写。证据：逐字节等价（6 模块体 == `HEAD:play.uts` 切片；导出 28=28；2 私有 helper 未外泄）+ 残留 grep 空 + **冷编译成功（0 error）**。⚠️ **平台约束坐实**：`client/utils/*.uts` **不支持** `export {…} from './x.uts'` 再导出（Rollup 视目标为 JS ⇒ `[plugin:uts] Expression expected`）⇒ "门面兼容再导出"不可行，一律改调用点直连域模块。
- **B5b（`40fd672`）`sync_client.uts` 按职责拆分**（L-05）：705 行 → `sync_{types,queue,local,pipeline,schedule}.uts`（77/84/86/291/101），6 调用点改写（`App.uvue` 保相对路径）。证据：5 模块体按原位置拼接 == `HEAD:sync_client.uts` 63–705（非空白行零丢弃；导出 22=22）+ **冷编译成功（0 error）**；表面增量如实登记（6 常量 + 4 函数由私有改 `export`）。
- **B10-q（随 `40fd672`）轴 4 注释计数假阴性**：B5b 生成的模块 doc 头含「对外导出：…」**当场暴露** `audit_exports` 用裸 `\bname\b` 全文本计数、**注释里提到导出名也算引用**（B10-d 只修了 `\s` 含换行那层，此为其残层）⇒ 真死码被注释掩盖。修：复用轴 2 的 `_comment_ranges/_in_comment` 剔除注释 + 预读缓存。**量化**：零调用能力导出 **4 → 9**，新露出 4 枚真死码（`AGG_CHECK_ON_DEVICE`/`invalidateTimelineCache`/`isIgnoredEvent`/`parseErrorString`，逐枚 grep 证实代码零引用）按棘轮冻结进 baseline。**反向探针**（导出仅在自己 doc 注释被提及）→ **A 漏判（`zero_cap=4`）／B 捕获（`zero_cap=10`）**，门禁 `[CRITICAL] 新增` @ `:6`、EXIT=1，已删探针；常规态 `audit_harness all` **无 CRITICAL**。
- **B5c（本轮）`uploader.uts` 按分节拆分**（L-06）：628 行 → `uploader_{types,pending,net,queue,photo,batch}.uts`（58/55/59/120/93/185），8 调用点改写。证据：区间**连续覆盖 59–628**、拼接**逐行一致**（非空行 516 vs 516）；**导出面改由脚本自动推导**（13 个原私有符号自动提升 export、原 14 个 export 全保留、共 27；结构上规避 B5b 那类"漏算私有符号"坑）；**冷编译成功（51s，0 error）**。⑤ 唯一语义细节已分析登记：末尾 `onNetworkRestored` 钩子随 `maybeUploadHeldOnWifi` 落入 `uploader_batch`，**启动链 `pages/shell/shell → <TabIndex> → uploadBatch`** 仍开屏即注册 ⇒ **行为等价**。
- **同步的文档漂移清理**：`client/README.md` 的 utils 清单（26→41 个 .uts；`uploader.uts`→6 模块；`play.uts`→6 模块；`sync_client.uts`→5 模块）已随拆分对齐，避免制造新声明漂移。
- **B5d（本轮 · 金丝雀）`detail.uvue` 样式外置**（L-08）：`<style>` 591 行**纯移动**到新文件 `client/styles/detail.css`，uvue 侧只留 3 行 `@import '@/styles/detail.css';` ⇒ **1167 → 577 行（<800，轴 5 销项）**。**先做能力前置探针**（吸取 UTS 再导出不可行的教训）：实测 ① ucss **支持** `<style>` 内 `@import` 外部 `.css`——**无需 scss 插件**（本机无 scss 插件、本仓无先例）；② `.uts` 组合式函数（`import { ref } from 'vue'`）**可**被 `<script setup lang="uts">` 引入。证据：css 内容**逐行一致**（591 vs 591）+ **冷编译成功** + **产物取证**（`GenPagesDetailDetailSharedData.style.bytes` 含真实类名 `page-root`/`detail-scroll` ⇒ `@import` 被真正处理，而非静默丢弃）+ baseline 条目已删且 `audit_harness all` **无 CRITICAL**。教训已登记（"编译通过 ≠ 样式生效，须查产物 bytes 类名"）。
- **B5.2e（本轮 · 样式半）`RecordSheet`/`TabIndex` 样式外置**（L-01/L-02 的样式半）：沿用 B5d 已证机制（整块 `<style>` 纯移动 + uvue 侧 `@import`）——`RecordSheet` style 858 → `client/styles/record-sheet.css`（**2209 → 1354 行**）；`TabIndex` style 680 → `client/styles/tab-index.css`（**1742 → 1065 行**）。两者仍 >800（script 1097/851 未动）⇒ **保留 baseline 条目并下调计数**（2208→1354 / 1740→1065）。证据：两 css **逐行一致**（846 vs 846 / 655 vs 655）+ **冷编译成功** + **产物取证**（`RecordSheet.style.bytes` 含 `scrim`/`grab`、`TabIndex.style.bytes` 含 `header-title`）+ `audit_harness all` **无 CRITICAL**。
- **B10-i（本轮 · 第六轮）门禁自身可靠性 4 项收口**：① **D14-23** CI `schema-drift-weekly` 原为 `continue-on-error: true` ⇒ **该 job 永绿、漂移无任何信号**（与同文件 `pip-audit-weekly`「发现即失败」自相矛盾）→ 去掉 `continue-on-error`（仍仅 schedule/dispatch 触发、不进 push/PR，但**定时漂移即红=告警**）；② **D14-22** 新增 `scripts/gate_report.py`（`SCHEMA_VERSION`+`COMMON_KEYS`+**带兜底** `write_report`），三工具机读报告统一信封、自有键全保留；③ **D14-12** 新增 `scripts/gate_proc.py` 单一 subprocess 实现（原 `review_agent`/`test_agent` 各写一份且超时/env/OOM 分支各异）；④ **D14-6** 轴 1 字段级漂移 → **判定已被 B10-g 字节级 `openapi_snapshot` 覆盖**（用审计自己的判据做反向探针：给 `SyncPushResult` 加字段 → 轴 1 仍绿、`openapi_snapshot` FAIL 且报"差异在字段级"），**不需要新增代码**。证据：`yaml` 解析通过 + 报告公共键校验 + 不可写路径兜底探针（`[WARN]`/`EXIT=0`）+ 127/124 行为探针 + 源码断言 + 两工具实跑 ✅；`ruff scripts/` 全绿。
- **B11（本轮 · 第七轮）声明漂移残余收口**：**D05-9 ✅** rerank 模型名三方不一致 → 权威值 `bge-reranker-v2-m3`（config 默认 + 两套 env 模板 + pull_models + B2 设计文档），改 **5 处**陈旧值（含 `rerank.py:57` 删 fallback 字面量 ⇒ 单点化；行为等价：置空时两分支都 `return None`）；**D02-7 ✅** `photos/` 键布局两套 → 新增 `services/media_keys.py::photo_object_key` **单点化**（`protocol._final_key` 委托 + `photo_content` multipart 路径改走同布局；`derive_thumbnail_key` 为 prefix 级不受影响）；**D04-3 ✅** `AGG_CONFIG["night"]`/`["gps_speed"]` 写了无人读 → `night` **接线**到 `st_dbscan`（值相同 ⇒ 行为等价）+ 删冗余 `gps_speed`（消费方直用常量）；**D12-7 ✅** `uvue_gen/audit_report.md` 死证据（引用不存在的对拍器）→ 文首加**失效声明**；**D02-9 ⏸ 登记未改**（扩展名三表不一致，收敛必改 MIME/受理格式 = **行为变更** ⇒ 移交功能波；已在三处加交叉说明）。证据：`test_rag` 31 passed + 5 文件 80 passed + 聚合 3 文件 31 passed + **内存级非空转探针**（改 night 配置 → 两时间戳翻转 ⇒ 确已接线）+ ruff 全绿。**⚠️ 接线暴露新发现（登记）**：`agg_types ↔ st_dbscan` **模块级循环依赖**（既有结构问题，本批以函数内局部 import 规避）。
- **B5.2f-1（本轮 · 第八轮）波形缓存 5 份同构副本 → `useWaveform` 组合式函数**：同一段 ~17 行"`contentId → /waveform` 峰值缓存"实现**被复制 5 份**（`TabIndex`/`TabSearch`/`detail`/`favorites`/`theme-detail`）⇒ 新增 `client/composables/useWaveform.uts::WaveformCache`（唯一实现），5 组件改 `const wf = new WaveformCache()` + `wf.waveformOf/loadWaveform`。证据：**等价性证明脚本**（5 份块归一化后均为同一组 **17 条逻辑行**、composable 覆盖全部、缺失 `[]`）+ **冷编译成功**（**首轮失败被当场抓到**：我的 import 重写脚本把 `", ".join` 误写成 `" ".join` ⇒ detail 多符号 import 丢逗号 ⇒ `Unexpected token, expected ","`，已修并复编译通过）+ 行数合计 **−91**（新增 composable 40 行）+ 轴 5 baseline 按"棘轮只许收缩"**下调**（TabIndex 1065→1046 / favorites 974→957）。**⇒ B5.2f 第 1 步完成；L-01/L-02 仍 >800 未销项，"真机渲染复验"待设备。**
- **B5.2f-2a（本轮 · 第九轮）`RecordSheet` 圆点+轮盘动画 → `useRecordAnimations`**：圆点动画（176 行）+ 轮盘公转（42 行）纯移到新增 `client/composables/useRecordAnimations.uts`（`DotSpec`/`DotPt` + 模块级只读常量/预计算轨道表 + `RecordAnimations` 类；原模块级可变状态 → 实例字段；原三处模块级初始化 `buildDotTrack/buildDotPts/dotRender(0)` → **构造器**；原函数仅改名；`dotCls` 原读组件 `recording.value` → 改**入参**）。组件侧删 2 块 → `const ra = new RecordAnimations()` + **顶层绑定** `const dotPts = ra.pts`（模板自动解包）+ `dotCls` 薄包装 ⇒ **模板零改动**；轮盘 4 处调用点改写为 `ra.start/stop/pause/resume`。证据：**冷编译成功** + 残留旧调用名 **= 0** + `RecordSheet` 1354 → **1135（−219）** + baseline 下调 1354→1135。
- 🚨 **GAP-1（本轮 · 第十轮）新发现并补门禁：HBuilderX 客户端「编译成功」只覆盖语法、不覆盖符号解析**——B5.2f 实测：`RecordSheet.uvue` 里 `new RecordAnimations()` 等 **8 处符号完全没有 import**（`useRecordAnimations` 2 + `custom_labels` 6），冷编译**连续两次报「项目 client 编译成功」**（对照：import 丢逗号这类**语法**错误会被 `Unexpected token, expected ","` 抓到）⇒ **把「编译通过」当跨模块引用成立的证据是不成立的**。补位三重：① 新增 `scripts/audit_client_imports.py`（扫描 `client/composables|utils` 导出符号的"用了却没 import"；剔注释+字符串字面量误报 26→10→8；`.uvue` **只对 `<script>` 区段**做 JS 剔除，避免模板引号配对吞行造成**漏报**）；② `audit_harness` 新轴 **`client_imports`**；③ 接入 `review_agent.check_audit_axes`（**快/全量门禁均跑**）。**端到端反向探针**：删掉 1 行 import → `review_agent` 直接 **❌ 阻断** 并点名 `RecordAnimations`/`DotPt` → 字节级还原 → 复跑 ✅。教训已登记（含"反向探针会写失败状态文件、随后须再登记一条教训"的流程性知识与"禁止删状态文件绕过门禁"）。
- **B5.2f-2b（本轮 · 第十轮）`RecordSheet` 自定义标签助手 → `client/utils/custom_labels.uts`**：`readStoredCustomLabels`/`persistCustomLabels`/`customOn`+4 个 chip 类名助手（原 `isCustOn` 读组件 `manualLabel.value` → 改**入参** `manual`）；组件只留 3 个 ref + 3 个改状态函数；**刻意不与内建标签对齐选中语义**（内建=手选优先→AI 预选；自定义=只看 `manual=='custom:'+名称`，因 AI 从不预选自定义）⇒ 逐字等价。证据：`RecordSheet` 1135 → **1084（−51）**；4 个模板调用点改带参；冷编译成功；轴 6 扫描 0 违规；baseline 下调 1135→1084；顺带统一 6 个文件新增 import 行的缩进为 Tab。
- **B5.2f-2c（本轮 · 第十一轮）`RecordSheet` 模板取值封装 → `client/utils/record_ui.uts`**：4 个**无状态纯函数**（`fmtRecTime`/`recSecFromMs`/`cellCls`/`imgCls`，原设计注释与峰宝拍板记录**逐字保留**）纯移动；`setInfoTime`（写组件 ref）留在组件内；模板按名调用不受影响。证据：`RecordSheet` **1089 → 1054（−35）**（顺带**消除**上一轮我新增 import 造成的 +5 增长 WARN）；**冷编译成功**；轴 6 扫描 0 违规；baseline 按棘轮**下调** 1084→1054。
- 🧭 **B5.2f 剩余部分需「设计决策 + 真机」同批（本轮如实提出，未硬做）**：`RecordSheet` 1054 / `TabIndex` 1046 距 800 尚差 **−254 / −247**，但**剩余 script 全是表单编排逻辑**（照片/文字/语音三段、L2/L3 归并），其依赖组件状态（`photos`/`recording`/`transcript`/`days` 等 10+ 个 ref）与 `emit`/动画实例 ⇒ 继续抽**必须重新分配状态归属**（把整块 view-model 移入 composable 或拆子组件），这**不再是「纯移动」**，且运行期行为（录音/上传/归并/动画）**只有真机可验** ⇒ 按"探索性设计先探讨"的规则，提请用户决策后再执行。
- **B5.2f-2d（本轮 · 第十二轮 · 方案 A 子步 1）`RecordSheet` 语音录制状态机整块进 composable**：用户拍板**方案 A（整块 view-model 进 composable）**后执行——13 个语音状态 ref（`recording`/`recordSeconds`/`recordTimer`/`recState`/`lastTapAt`/`recordHint`/`voiceHints`/`transcribing`/`transcript`/`emotion`/`guardMsg`/`lastVoiceDuration`/`lastVoiceFile`）+ 6 个状态机方法（`setRecordingOff`/`toggleRecord`/`pauseRecordNow`/`endFromTap`/`resumeRecordNow`/`stopRecordNow`，163 行）→ 新增 `client/composables/useVoiceRecord.uts::VoiceRecorder`；注入 `anim`（动画实例）、`onStopped`（组件 `onRecordStopped`，暂留）、`resetAiLabel`（清 aiLabel 动作，等价替换原 `aiLabel.value=''`）；组件退化为**薄绑定层**（12 个顶层 ref 绑定 + 6 个薄包装，模板仍自动解包）。证据：`RecordSheet` **1054 → 889（−165）**；**冷编译成功**；轴 6 扫描 0 违规；baseline 下调 1054→889。
- 🛠 **本轮同时暴露并修掉 4 个"我自己制造的"缺陷（全过程留证）**：① **轴 6 真抓到回归**——我把组件 voice import 瘦身时误删 `recorderState`/`stopRecord`，而 `closeForm()` 仍在用（force-release 路径）⇒ 轴 6 立即点名两处；② 生成的 composable 类内方法误留 `function` 关键字；③ 内部方法互调未加 `this.`（会变未定义调用）；④ 组件改写时**先删声明再用旧索引切割**导致错位（编译报 `Unexpected token (885:0)`）。④ 之前那句"可疑未注入标识符体检"脚本**主动抓出了 `aiLabel` 漏注入**。⇒ 「生成式改写必须配静态体检 + 真实编译」，且**轴 6 已有实测战功**。
- 🎯 **B5.2f-2e（本轮 · 第十三轮 · 方案 A 子步 2）语音收尾三函数迁入 → `RecordSheet` 首次落到 800 以下 ⇒ L-01 销项**：`onRecordStopped`（转写/长录音持久上传）/`submitVoice`/`afterVoiceSaved`（147 行）作为类方法迁入 `useVoiceRecord`；`saving` 状态一并迁入；**撤销** 2d 的 `onStopped` 注入（`onRecordStopped` 已在类内），新增注入 `emitSaved`/`emitClose`/`setVoiceStage`/`effLabel`/`remarkTrimmed`/`resetFormFields`（把 `emit('saved')`、`voiceStage.value=…`、`manualLabel!=''?…:aiLabel`、`remark.value.trim()`、收尾三行清态逐一改为注入回调，**行为等价**）。证据：`RecordSheet` **889 → 753（<800）**；**冷编译成功**；轴 5 **无 CRITICAL**；**axis-5 baseline 条目按 gate 要求删除 ⇒ L-01 销项**（gate 原话"已不再超阈……请从基线删除该条"）；轴 6 0 违规。
- 🔎 **GAP-1 强化数据点（本轮）**：我的整文件正则把**方法声明**也写成 `this.afterVoiceSaved(contentId: string, …): void {`（**非法类成员语法**），而 **`项目 client 编译成功` 仍报绿**！⇒ 客户端编译门**连类成员非法语法都放过**（只有解析级 `Unexpected token` 才会红）。同一轮里 `saving` 重复声明**倒是被编译抓到**（属绑定级）。⇒ **强化结论：客户端"编译 0 error"不能作为结构正确性证据**，必须配 轴 6 + 静态结构检查 + 真机。
- **⚠️ 未闭环（如实标注）**：① **真机项未跑**（`adb devices` **无设备**）：B5c 上传、B5d/B5.2e/B5.2f-1·2a·2b·2c·2d·2e 的渲染/波形/圆点轨迹/轮盘公转/自定义标签交互/**录音状态机与保存收尾全链路**、**L-01/L-02 真机验收** 均待设备；② **L-02（`TabIndex` 1046，差 −247）未完**：按方案 A 迁 L2/L3 归并 + 照片挂载两块；③ **L-12 令牌收敛**（`design_tokens.json` 现 0 引用）**另立令牌波**；④ 64 条功能与安全缺陷 → 已拍板**另开「功能修复波」**（台账 §5.10 拍板 10.1）。

- **B11 收尾（⑦⑧）**：**D09-10 ✅** wechat_messages.status 三方枚举/默认值不一致 → ORM 定义 `WECHAT_MESSAGE_STATUSES`（唯一来源）+ schema.sql 注释对齐（**未改 DDL 默认值**，属漂移迁移待窗口）+ **新增防漂移门禁测试**（反向探针证明非空转，还暴露首版正则漏数字的盲点并已修）；**D07-14 ⏸ 登记未改** 三套 dimensions 读路径——实读澄清**云侧并无重复**（`display_dimensions` 已单点化；`export._profile_out` 属原始导出、非展示口径），真正缺口是**跨语言平行实现**（客户端用英文 dim id 作标签）⇒ 目标"不再泄漏英文 dim id"= **UI 文案变更**，移交功能波（两处 docstring 已交叉说明）。**⇒ B11 结构类项全部处置完毕**（4 修 + 1 加门禁 + 2 登记）。

## ✅ 第十轮 · L-02 销项（`TabIndex` 抽出）+ GAP-2（类成员假绿面 → 轴 7 + 修 P0 回归）（2026-09-25）

- 🚨 **复核 f333430 时发现 P0 回归并修复 —— GAP-2 成立**：B5.2f-2e 把 `saving` 迁入 `useVoiceRecord.uts` 时，**方法体已写 `this.saving.value` 却漏声明 `saving` 字段** ⇒ 真机 `submitVoice` 必 `undefined.value` 崩溃。**两道门同时失守**：① HBuilderX 编译报绿（GAP-1 已证：编译只覆盖语法/解析）；② **轴 6 也抓不到**——它只查「用了模块导出却没 import」，`this.<名>` 是**类成员访问**、不是跨模块符号。修复＝补 `saving = ref(false)`（恢复迁移前的并发保存守门）。⇒ **跨模块符号（轴 6）与类成员（新轴 7）是两道独立假绿面**。
- 🛡 **新增轴 7 `scripts/audit_class_members.py`（本波第二道客户端静态补位）**：扫描 `client/**/*.uts|*.uvue` 所有类，声明集＝类体**深度 0** 的字段/方法；覆盖两个面——① `this.<名>` 不在本类；② `const x = new Cls(…)` 之后 `x.<名>` 不在 `Cls`（实例指涉：L-02 抽 composable 后组件大量经实例调用，属同一假绿面）。**挡误报**：`extends` 类整体跳过（继承成员静态不可知）、同名变量非「**全部**赋值皆 `new Cls(`」则跳过（实测挡下 `uploader_batch.uts` 的 `held`——它既是 `new UploadProgress(…)` 又是 `heldPhotos()` 数组）。接入 `audit_harness` 新轴 `class_members`（含 `all`）+ `review_agent.check_audit_axes`（快/全量均跑）。证据：全仓 **76 类 0 误报**（3 个 extends 跳过）；**反向探针两例**（删 `saving` → 报 `this.saving`；`pa.attachAll`→`pa.attachAllZZZ` → 报 `pa.attachAllZZZ`）**均 EXIT=1 且字节级还原**；教训已登记 `docs/lessons.md`。提交 `dac0625`（缺陷 + 轴 7，`review_agent` 快速门禁 ✅）。
- 🧭 **L-02a（方案 A 子步 3）`TabIndex` 照片路径 / 挂载 / 照片详情 → `usePhotoAttach`**：`PhotoPathEntry` + 照片态（`localPhotoPath`/`eventPhotoIds`/照片详情四态）+ 两张**非响应式索引表**（`photoUrlById` 服务端票据 URL、`localPhotoPathMap` O(1) 本地查表）+ 7 方法（`photoPathOf`/`lookupEventPhotos`/`attachEventPhotos`/`onPhotoTap`/`closePhotoDetail`/`jumpFromPhoto`，注释与「2026-08-31 重写」实锤记录**逐字保留**）纯移动；`attachPhotos()` 更名 **`attachAll(days, pending)`**——原读组件 `days.value`/`pendingEvents.value`，改**入参**（渲染管线核心留组件、时序零改动）；新增 `addLocalPath`/`addEventPhoto` 把 `handleBatch` 里「数组 push + O(1) 查表 set」两处同语义写入收纳为一个方法（同进同出，防查表漂移）。证据：`TabIndex` **1046 → 899（−147）**；**冷编译 `项目 client 编译成功`**（48.7s）；轴 6/7 **0 违规**。
- 🎯 **L-02b（方案 A 子步 4）`TabIndex` 卡片操作 / 拆分 → `useEventOps` ⇒ L-02 销项**：拆分四态 + 13 方法（`onCardOps`/`doConfirm`/`confirmPending`/`ignorePending`/`patchEventById`/`removeEventById`/`getPrevL1Id`/`doMerge`/`doSplit`/`toggleSplitItem`/`confirmSplit`/`cancelSplit`/`splitTimeText`）纯移动；注入四个**取值函数**（不能传快照）：`getCurrent`（组件 `currentEvents` 是 `let`、会被整组浅替换）+ `render`（原 `renderFromEvents`）+ `refresh`（原 `refreshSilently`）+ `getDays`（原 `days.value`）；组件只留 3 个模板入口薄包装。证据：`TabIndex` **899 → 738**（累计 **1046 → 738，−308**）；**冷编译成功**（36.8s）；轴 5 **无 CRITICAL**；**axis-5 baseline 条目按 gate 要求删除 ⇒ L-02 销项**（存量超阈 **4 → 3**）；`audit_harness all` **无 CRITICAL**（INFO 18）。
- 👁 **L-02b 顺带发现（登记交功能波，非本波修）**：模板内**已无**拆分面板与照片详情浮层 ⇒ `showSplitPanel`/`splitItems`/`splitLoading`/`confirmSplit`/`cancelSplit`/`toggleSplitItem`/`splitTimeText` 与 `showPhotoDetail`/`photoDetail*`/`closePhotoDetail`/`jumpFromPhoto` 均属**模板不可达 UI 状态**（用户仍可经卡片「⋯」触发 `doSplit`，但无节点渲染该面板）= **功能缺口**。本波只纯移动，按原样搬迁 + 留此登记（避免"搬迁即修"越界）。
- 🎯 **L-18/L-19/L-20 样式外置 ⇒ 轴 5 filesize 棘轮清零（第十六轮）**：`TabAi.uvue` 974→**469**（style 507 行 → `client/styles/tab-ai.css`）/ `favorites.uvue` 957→**500**（style 459 → `favorites.css`）/ `manage.uvue` 891→**466**（style 427 → `portrait-manage.css`）——沿用 **B5d/B5.2e 已证机制**（整块 `<style>` **纯移动** + uvue 侧只留 `@import`），**零 script/运行时改动**（比抽 composable 风险更低，故优先选它）。证据：① **等价性**＝「当前 uvue + 还原 css」与 `git show HEAD:<file>` **按行尾归一后字节全等**（TabAi 工作区 CRLF、git blob LF，差值恰＝行数；**教训：字节比对前必须归一化行尾，否则会得出假"不等"**）；② **冷编译成功**（ready in 67.8s）；③ **产物取证**＝`GenComponentsTabAiTabAiSharedData.style.bytes` 含 `ai-root`/`ai-header`、`GenPagesFavoritesFavoritesSharedData.style.bytes` 含 `page-root`/`nav-title`、`GenPagesPortraitManageSharedData.style.bytes` 含 `page`/`serif` ⇒ `@import` 被真正处理；④ **三条 baseline 条目删除 ⇒ `audit_harness filesize` 报「存量超阈 0 个」**。
- **本波结构类清单已清（终态）**：**L-01**（`RecordSheet` 753）/ **L-02**（`TabIndex` 738）/ **L-18/19/20**（`TabAi` 469 / `favorites` 500 / `manage` 466）**全部销项**，轴 5 存量超阈 **4 → 3 → 0（allowlist 清空）**。**仍待真机（不可用）**：B5c 上传、B5d/B5.2e/B5.2f 全子步的渲染/波形/动画/录音保存全链路、**L-01/L-02/L-18/19/20 运行验收**。**另立波**：L-12 令牌波、64 条功能修复波。
- ✅ **全量门禁实跑（2026-09-25 · Docker/Qdrant 已起）**：`python scripts/review_agent.py --full` → **EXIT 0 / ✅ 审核通过**（`.cowork-temp/test-report.json` `passed=true`、`blocking_sections=[]`、`env_blocked_sections=[]`）：`syntax` **376 文件 ✅** / `lint` ✅ / `secrets` ✅ / `structure` 无 CRITICAL / `audit_axes` 无 CRITICAL / `env_template` ✅ / `openapi_snapshot` ✅（239518 字符逐字节一致）/ `tests` ✅（pytest 主套件 + `-m rag` 分组 `--cov-append`，**“Required test coverage of 60% reached. Total coverage: 84.59%”**）/ `api_smoke` ✅ / `research` ✅ 18 场景。⇒ **承 §8.3 B10-h 的「覆盖率 50→60 待环境验证」就此解除**（此前"无法确认"的如实标注现由实测取代）。
- 🧹 **文档口径对账（同轮，防"多窗口漂移"）**：清掉 4 处**已过期**的待办勾选（`checklist` A7 拍板项 / B5.2f"未做" / `tasks` A7 / B10-b / B10-i / B5 巨文件拆分），并把 `review_agent --full` 的实测读数回填 checklist/tasks/ledger。**说明**：这些是**状态记账漂移**（工作早已完成却仍显示待办），非新工作量；按 AGENTS「改任何全局数字必须多处同步」纪律一次性校正。

## ✅ 第十一轮 · 令牌波开波（方案先行）+ 三裁决登记（2026-09-25）

- 🧭 **用户三裁决已落定并登记**（决策台账新 **§4.16**）：① **TabIndex 悬空面板＝不恢复**（git 取证：08-29 `cd41f18`「像素级UI还原」重写本页时删掉的模板节点，未迁移未新建组件，且 `index_canvas.json` 亦无此两帧＝有意为之；**非本次重构所致**，悬空逻辑无害）② **客户端↔Agent 接线 → 归「功能修复波」** ③ 波次顺序 **先令牌波、再功能修复波**。
- 📄 **令牌波开波（用户拍板"方案文档先行"）**：产出 `.trae/specs/design-token-convergence-wave/spec.md`。
  - **输入分布（本轮实测）**：38 个样式承载文件（32 内联 `<style>` + 6 已外置 css）＝**7400 行**；hex **762 次/56 种** + `rgb()/rgba()` **190 处** + 渐变 **10 处** ⇒ **~962 个颜色位点**；模板动态 `:style=` **19 处**；`var(--` **0 次**；`design_tokens.json` 仅 **52 个叶子值**。
  - **五个结构性差异**（比数字更关键）：① tokens.dimensions 是 **390 画布 px** 而代码是 **750 rpx**（非 1:1）② 令牌集**完全没建模** rgb/渐变/阴影 ③ 只有单层"用途色"、**无语义别名层** ④ 19 处动态绑定需单独一簇 ⑤ `effects.card_shadow` 非合法 CSS。
  - **平台能力**：✅ App ucss **支持 `var()`**，官方示范的声明位是 **`app.uvue` 里的 class**（`:root`/`page` 不可用、本仓 0 处尝试）；⚠️ 本仓实锤 **App 端样式不继承**（`App.uvue:95-113`"勿改回"注释）⇒ **V1 变量作用域 / V2 跨文件可见性未验证＝方案成败点**；⚠️ `theme.json` 只管 pages.json/tabbar。
  - **前沿**：**W3C DTCG Format Module 2025.10**（2025-10-28）已成首个稳定标准（`$value`/`$type`+路径别名），SD v4/Terrazzo/Figma 均读写；但本仓 tokens **非 DTCG 形态**；单端项目**不建议引 SD 本体**（自写 ~100 行转换器更省）。
  - **方案对比 A/B/C + P1–P5 分期**已写入文档；**6 项待拍板**（DTCG 与否 / 探针优先 / 基准 750rpx vs 390px / 分期 / dark mode / P3 目视挂账）。
- ⚠️ **如实标注**：P3 的**目视真机验收**依赖设备（`adb` 无设备）⇒ 门禁/编译/产物取证可做，**目视挂账**；未拍板前**不动任何令牌/样式代码**。

## ✅ 第十二轮 · 功能修复波续执行（7 批 · 2026-09-25 会话）

> 入口：`.trae/specs/functional-fix-wave/spec.md` §4（逐批提交与证据）；本节只记速查结论。

- 🎯 **A 段 17 条 P0 全部处置完毕**（前序簇① D04-1/2/14/15、簇② D08-1/2/3/5 + 本会话 7 批）。
- 🧱 **本会话 7 批（每批独立提交 + 快速门禁 + 受影响测试）**：
  | 批 | 提交 | 缺陷 | 一句话 |
  |---|---|---|---|
  | 1 | `da8b318` | D02-2/D09-2（P0）+D09-14 | 存储键命名空间**单一来源** + 补 `wechat/`（微信原件此前恒 401）+ `.amr` MIME |
  | 2 | `2c1dc05` | D10-1/2（P0）+D10-3/4/5/12 | 护栏 fail-safe 收口（`verdict_action` pass 优先；托管空响应不再放行；4 链走策略入口；硬/软回流分流；ASR 打码） |
  | 3 | `b1f2488` | D05-3（P0） | 检索过滤器键集单一来源 + **未知键 fail-closed**（隔离键静默丢弃＝跨用户召回） |
  | 4 | `adfd4e4` | D07-1（P0） | 画像枚举集随镜像分发 + 启动自检（容器内画像首调 500 根治） |
  | 5 | `5f5c37c` | D09-1（P0）+D09-3/4/7/8/12 | 微信主链贯通（绑定表 ORM+迁移+解析+端点）、删掉走全局软删、应用 Secret 独立、回调 offload、WECOM 别名、缺 MsgId 403 |
  | 6 | `634b4b4` | D12-5（P0 结构） | 一次性生成链标记 + 路径可移植（原指向已不存在的工作树） |
  | 7 | `b7a6e04` | D08-18 | 新建内容写变更日志（他端 pull 才有源） |
- 🛡 **门禁口径**：全程 `review_agent`（快/全量）无 CRITICAL；新增测试 **68 例**（11+16+16+5+14+3）；两处**源码级反漂移断言**（存储键白名单 / 4 条主链不得直连 dashscope）与一处 **真实镜像构建取证**（D07-1）。
- ⚠️ **如实标注（未做）**：客户端编译门项（`D10-10`/`D08-4`/`D07-7..10` 等）、凭证/实网项（微信客服 `openid→unionid` 解析、微信侧「找」回复、`D09-4` 真凭证复验）、产品决策项（`D10-8/9`、`D07-12`、`D02-9`）、候审项 —— 见 spec §4 末清单，**不得视为已完成**。
## ✅ 第十三轮 · 用户四项产品决策「全同意」后落地（2026-09-25 晚）

> 拍板入口：`docs/决策台账.md` §5.11（含选项对比）+ §5.11 拍板结果（逐条落地与提交）；本节只记速查。

| 批次 | 提交 | 决策项 | 落地 |
|---|---|---|---|
| 8 | `000309f` | D10-10 | 客户端护栏默认值由**放行改拒发**（`guardrail` 缺失不再当"已通过"）；HBuilderX 冷编译 63224ms + 增量 34432ms |
| 9 | `3137132` | D02-9 半 | `.heif → image/heif` MIME 失真修复 + "受理白名单 ⊆ MIME 表"机器断言 |
| A | `58e2a71` | **D10-9（C+D）** | 软话题检测器"没跑通"不再当"内容正常"：`DetectionResult.ok` + `detect_event_sensitive_status()` + `sensitive_status="待复核"`（主动提及路径跳过，入库/检索不变）；软表扩 20 个口语词；mock 模式视为可用 |
| B | `ce71eb7` | **D10-8（B+C）** | 新增 `llm_ops/output_guard.py` + 四条生成链接线（照片描述/事件标题/画像开放值/对话回复）；对话固定过 LLM 级、照片 10% 确定性抽样（成本 3000 次/天 → 约 300 次/天）；新错误码 CHAT_004 |
| C | `56dac34` | **D07-12（B）** | 画像**真删除**端点 + 客户端接线 + 弹窗文案改真话（原承诺删云端、实际只清本地）；保留保护性敏感话题并如实回报 |
| D | 本批 | **D02-9 剩余** | 以**库内实测分布**定：`.gif` 不支持并删不可达条目；HEIF 族保留（194 个 `.heic` 在用） |

- 🐞 **过程中被抓到的两处自身缺陷**（均已登记 lessons）：① 我自己在 `event_merge` 复用变量名 `verdict` ⇒ 归并恒判 split（新测试断言"不阻断归并"当场抓到）；② 契约两处登记面只同步了 `openapi.json`、漏了人读的 `OpenAPI契约.md` 表 ⇒ 轴 1 报"同路径方法不一致"。
- 🛠 **环境**：HBuilderX 中途退出 ⇒ 由 CLI 拉起重启（冷编译 ✅）；Docker 引擎中途停止 ⇒ 拉起 Docker Desktop 恢复 redis/qdrant（否则 pytest 连接失败）。
- ⚠️ **未验证（如实标注）**：无真机（`adb devices` 空）⇒ 客户端改动仅到"编译 + 产物取证"级；画像清除按钮的真机点击链路未目视。