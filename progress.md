> 🧹 **2026-09-04 整饬**：已完成/已闭环条目已压缩为「✅ 速查卡」（分支/SHA｜文件｜方法｜设计来源）；未实现/半通/待复验/待拍板条目保持原叙述。未闭环事项现行权威清单 = `_diff_ledger.md` 与 `_execution_plan_20260904.md` §1。
> 📌 **当前状态速览（2026-08-29）**：收尾 Wave 1–4 全收口（17/17），真机 7 清单全达终态+补验。终值：**用户故事 ✅46/🟡7/❌0 · A 级 32 条 · 性能门禁 10/2/6 · 30s ✅6.0s**。
> 数字唯一现行口径 = `AGENTS.md`「当前状态」节；术语/决策/待拍板 = `docs/决策台账.md`；缺陷台账（19 单：D-01~D-16、D-18、D-19、D-21）与环境事件（O-1/O-2）= `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4/§5。
> **下一步 = 4b 修复批次（执行计划已定：`docs/4b修复批次执行计划_20260829.md`，P-0 拍板五项全落、P-1 隔离工作区已建）**：批次1 D-18/D-19（重打包复验）→ 批次2 D-16/D-07/D-08＋散单 D-05/D-10/D-14/D-21（08-29 拍板并批）→ 批次3 S2 校准（卡真值）+D-06（**等价复现验收，无需第二设备**，台账 §1.8）。
> 本文件为**时间线日志**（新旧混排属历史演进），新条目追加在**末尾**；历史条目只读保留，仅加 [勘误] 注记。与速览冲突的旧数字以速览为准。

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

- **rebase 后解冻清单实施完毕（fix/4b 新增五枚）**：`23e8604` D-22 客户端半（意图先流+仲裁纯参考，16 绿+编译过）→ `97eb795` D-07/D-08 语音链（短录音带音频入库+250s 分流黑洞修复+超时伸缩+失败段持久队列，test_pipeline 25 绿）→ `2a0194b` D-10/D-12（parseBody 安全解析+回退链 warn）→ `381a210` D-05/D-14（聚合共口+四路同源+双队列去重）。tracker §4 七行状态同步。
- **worktree 事故与自愈**：主仓 `.git/worktrees/` 被清理 Agent 误注销（三 worktree 全掉注册，分支/文件零损失）——fix4b 重建注册+`.env` 回迁+**无缓存全量冷编译两次通过**；wrap1 worktree 旧目录原样保留（其窗在途 uvue_gen 清理现场），dashscope worktree 已合流无需重建。禁碰清单已重申。
- **🌑 暗物质第三例（O-5）**：`dismissCorrectionPrompt` 及 D4 弹窗整条特性从未提交（只活在 stash 树），台账 5.3「hook 已就绪」前提崩塌——**U3 悬置待重拍（A 回炉移植/B 二期/C R2 后评）**。
- **R2 云打包已发车**（后台，Vapor 自定义基座，参数含 `--project` 绝对路径新规）；包出→aapt 尸检（D-19 service 注册）→真机装基座（先查纯净模式）→D-18/19 日志复验→纠错/语音/断网重传真机电池。内存 4GB 档，HBuilderX 独占已协调。
