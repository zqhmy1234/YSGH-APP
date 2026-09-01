# 拿 Key 后推进计划 + 全量性能指标需求（2026-08-19，执行进度更新 14:4x）

> 背景：2026-08-19 Infisical 密钥链路打通（CLI v0.43.122 → 登录 → yishu-backend 项目 → dev/prod 各 10 条 Secrets；流程见 `skills/infisical-secrets/SKILL.md`）。本文档盘点：①现有 Keys 解锁了哪些 Sprint 工作 ②哪些仍阻塞 ③可推进工作的测试与执行计划 ④本项目全部性能指标需求。
>
> **执行进度（2026-08-19）**：WP-A ✅ config 别名读取（6 测试）｜WP-B ✅ 百炼探测**可用**（qwen-flash 真实调用成功）｜WP-C ✅ COS 分片上传+断电续传（upload_tasks/chunks 表 + storage 抽象层 + 10 测试）｜WP-D ✅ 端云对账（reconcile 6 测试）｜WP-E ✅ CI 图片标签+审核（6 测试）｜WP-F ⏸ RAG 调优**暂停**（用户拍板：数据量不足，测意义不大；已保留路由过滤修复 route_acc 0.5→1.0 + reranker 接线）｜WP-G ✅ 微信"找"沙箱链路（find_memories + API，4 测试）｜WP-H ✅ 数据安全审计（audit_security.py 全绿，备份 RPO 已恢复 ≤24h）。全量 pytest 145 passed。
>
> **真机冒烟（2026-08-19 15:0x，用户同意费用）**：scripts/smoke_cos.py 全过——COS 上传/读回 SHA256 校验 ✅、CI 图片打标 ✅（真实截图 → ['截图','课程表']，修复响应结构 CameraLabels/WebLabels + DetectType 位掩码）、CI 内容审核 ✅（pass=True）、测试对象已清理。MinIO 断点续传实测 ✅（docker yishu-minio：中断→缺片→续传→complete→对象校验→staging 清理）。费用：CI 打标 1 次 ≈0.0015 元。
>
> **外部数据集调研（2026-08-19）**：①ASR WER：AISHELL-1（openslr.org/33，中文普通话 178h/16kHz/Apache-2.0，CN 镜像 openslr.magicdatatech.com，WER 用 test 集子集即可）；②RAG 检索层：T2Ranking（github.com/THUIR/T2Ranking，中文段落排序基准）；③RAG 端到端：CRUD-RAG（github.com/IAAR-Shanghai/CRUD_RAG，清华 KEG 中文 RAG 综合基准，数据集在 HF CRUD-RAG/CRUD-RAG，本机 HF 不可达需 hf-mirror/modelscope）；④C-MTEB（中文 embedding 评测含检索任务，hf-mirror 可下）。下载与 WER/评测集落地等用户拍板。
>
> **数据集选型论证（15:2x，用户要求符合本项目输入分布）**：
> - ASR：选 AISHELL-1（普通话 16k 单声道、安静室内麦克风）——与本项目手机录音输入分布一致；hf-mirror 官方镜像 `AISHELL/AISHELL-1`（按说话人分包）已确认可达，已下载 10 说话人包子集（S0002-S0011，~360MB）建 WER 基线（scripts/run_wer_bench.py 就绪，字级 CER）；官方 test 集划分后续对齐
> - RAG：本项目输入分布 = 个人记忆碎片（短文字 5 类/语音转写风格/照片截图），通用问答基准（CRUD-RAG）分布不同——仅作补充基线；主评测 = 团队 50 条真实查询（等团队构建）；检索层可选 T2Ranking 子集作补充（中文段落检索与碎片检索分布接近）
>
> **图片检索推进（15:2x，百炼可用后）**：Qwen3-VL 图片塔真实冒烟通过（3 张截图 2-4.4s/张，中文描述准确）；corpus-A 500 张截图批量 caption+索引（scripts/build_image_index.py，缓存断点续跑，费用 ≈1.5 元）——第一轮 379/500（网络中断 121 张），重跑补齐中；B2 设计落实度对照已产出（docs/B2设计落实度对照.md）——核心链路已落实，差距：图片闭环进行中 / NER 实体抽取未做 / reranker 为 base（设计 v2-m3）/ mixed 双路融合与以图搜图 P2 / 上线评测集等团队。
>
> **ASR 状态（15:44）**：百炼 ASR 模型（fun-asr/qwen3-asr-flash-filetrans）调用报 `Model not found`——**模型需在百炼控制台单独开通**（qwen-flash 可用证明 key 正常；ASR 未开通时 404，与 workspace 无关，已直调验证）。WER 管线与数据就绪（AISHELL-1 10 说话人 3133 条 wav + 标注，research/asr_bench/），开通后一条命令跑：`MOCK_EXTERNAL_AI=false infisical run --env=dev -- python scripts/run_wer_bench.py --n 20`。asr.py 模型名已按百炼 2026 清单修正（fun-asr / qwen3-asr-flash-filetrans，旧 paraformer-v2/sensevoice-v1 已下线）+ Recognition 实例化用法修正（SDK 1.26.7 要求 `Recognition(model=..., callback=...).call(file=...)`）。

---

> 🕓 **状态（2026-08-25 整理）：部分完成**。P0-1 RAG 真实链路已落地并超额达标（见 [RAG测评报告_20260825.md](RAG测评报告_20260825.md)）；P0-2 图片塔、P0-3 ASR 等按需推进。



## 一、密钥盘点

### 1.1 已到位（Infisical dev/prod 各 10 条，值仅经 CLI 取）

| 密钥名 | 解锁能力 | 状态 |
|---|---|---|
| DASHSCOPE_API_KEY + DASHSCOPE_WORKSPACE_ID | RAG 真实查询改写/路由/精排（qwen-flash）、Qwen3-VL 图片塔、FunASR/SenseVoice 真实转写、护栏真实检测 | ✅ 可用（sk-ws- 工作空间级 key，需验证 SDK workspace 传递） |
| BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY | 百度 OCR 文字识别（后端尚无 service，需先立项） | ✅ 可用 / 未接线 |
| TENCENT_APPID / TENCENT_COS_BUCKET(yishu-photos-1331926998) / TENCENT_COS_REGION(ap-shanghai) | COS 对象存储业务标识（公开参数） | ✅ 可用 |
| TENCENT_CI_SECRET_ID / TENCENT_GUANHAIFENG_CI_SECRET_KEY | 腾讯云子账号 AK/SK：COS 上传、CI 图片标签打标、STS 临时凭证 | ⚠️ 可用但命名与 config.py 不一致（见 1.3） |
| TENCENT_STS_ROLE_ARN | STS 角色（当前为 root ARN，建议改子账号角色） | ⚠️ 安全待优化 |

### 1.2 缺失（申请后补存，对应阻塞项）

| 密钥名 | 阻塞内容 |
|---|---|
| WECHAT_APPID / WECHAT_SECRET | F6 微信 code2session 真实登录（AUTH-001 真验）；当前生产 mock 登录 501 |
| WECOM_CORP_ID / WECOM_TOKEN / WECOM_ENCODING_AES_KEY（或 WECHAT_CORP_ID 等） | S4-01 真实企微回调（当前未配置一律 503 拒绝）；S4-02 微信"找"真实链路 |
| AMAP_WEB_API_KEY | 高德逆地理（事件聚合 L2 地点命名、无 GPS 照片归组增强；后端 service 未建，非当前 sprint 硬依赖） |
| SENTRY_DSN_DEV / SENTRY_DSN_PROD | Sentry 可观测启用（main.py 已接 sentry_sdk，无 DSN 不初始化）；S5-05 审计/S5-07 内测保障依赖 |
| XIAOMI_*/HUAWEI_*/OPPO_*/DCLOUD_* | uni-push 真实推送通道（M2 接入时再落；消息中心当前 mock 通道） |

### 1.3 使用前必做的对齐项（阻塞部分真实调用）

1. **腾讯云密钥命名对齐**：Infisical 现为 `TENCENT_CI_SECRET_ID` / `TENCENT_GUANHAIFENG_CI_SECRET_KEY`，而 `backend/app/core/config.py` 期望 `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`。二选一：改 Infisical 命名（推荐，符合手册规范）或 config.py 增加别名读取。改后 `infisical run` 注入即生效。
2. **STS_ROLE_ARN 优化**：当前值为主账号 root ARN（`qcs::cam::uin/...:root`）。按《_核心结论速查》第 2 节：建 CAM 子账号 + 最小权限策略（QcloudCOSFullAccess + QcloudCIFullAccess + QcloudSTSFullAccess）+ 角色，替换 ARN；长期密钥仅存 Infisical。
3. **DASHSCOPE workspace 传递（实测确认需改代码）**：sk-ws- 工作空间级 key 必须带 `X-DashScope-WorkSpace`。已核实 dashscope SDK 1.26.7：workspace 只能显式传参（`Generation.call(workspace=...)` / `MultiModalConversation.call(workspace=...)` / `Recognition.call(workspace=...)`），**没有环境变量兕底**。需改：config.py 增加 `dashscope_workspace_id` 字段（读 DASHSCOPE_WORKSPACE_ID）+ dashscope.py/asr.py 三处调用传 `workspace=`。
4. **本机旧环境变量清理**：shell 里残留旧格式 `DASHSCOPE_API_KEY`（sk-4980...），与 Infisical 的 sk-ws- 不一致。统一以 `infisical run --env=dev` 注入为准，避免旧值覆盖。
5. **staging 环境填充**：当前为空；CI/联调要用 staging 专属 Key 时按手册存。

---

## 二、可以继续推进的工作（含测试计划）

### P0-1 · RAG 真实 LLM 链路 + 门禁达标（F5 / M1 门禁核心，DASHSCOPE 解锁）

现状：`research/rag_benchmark/evaluation_report.json` 全分布测评 `overall_pass=false`：hit_rate@3=0.6364（门禁 0.70）、route_acc=0.5（门禁 0.90）——规则兜底路由明显不足，真实 qwen-flash 路由/改写预期可拉高。

测试/执行计划：

1. `infisical run --env=dev -- python -m research.rag_benchmark.run_eval`（真实模式，MOCK_EXTERNAL_AI=false 由 .env 控制）
2. 对比真实 vs 规则兜底：hit_rate@3 / route_acc / temporal_acc / recall@k / mrr / ndcg@k 分层报告（corpus-B 文字 84 条 / corpus-C 语音 33 条 / corpus-D 混合 / corpus-E 1 万条规模）
3. 调优路径：真实路由修正 route_acc（0.5→0.9）→ 改写提升 recall → 双层 Rerank（bge-reranker 粗排 + qwen-flash 精排，代码待接线）→ hit_rate@3 ≥0.70
4. 回归门禁：`pytest -m rag`（backend/tests/test_rag.py 6 项 + test_rag_metrics.py 指标单测）
5. 规模压力：`python -m research.rag_benchmark.run_eval --scale-eval 1000` 记录 P95 曲线（目标 P95<3s）

### P0-2 · RAG 图片塔（Qwen3-VL）+ 500 张截图图片基准（F5 corpus-A，DASHSCOPE 解锁）

现状：`dashscope.py image_caption()` 已实现（Qwen3-VL 图片→语义描述），500 张截图语料在 `C:\Users\ghf\Pictures\Screenshots`（已抽样索引过文本侧）。

测试/执行计划：

1. 图片 caption 冒烟：`infisical run --env=dev -- python -c "from app.services.external.dashscope import image_caption; print(image_caption('<截图路径>'))"`（验证 workspace 传递与计费）
2. corpus-A 图片基准：复用 `research/event_aggregation/` 截图抽样 + `build_corpora.py` 扩展图片层 → 图片描述向量入 `yishu_benchmark` → 文字搜图（RET-001）与以图搜图（RET-002）分层指标
3. 图片塔延迟记录：单张 caption 延迟（百炼 API），评估对 P95<3s 的影响（缩略图/异步策略兜底 B4-3）
4. 门禁：文字搜图命中率进入 RAG 回归基线（PERF-002 口径扩展）

### P0-3 · ASR 真实转写验证 + WER 基准（F3，DASHSCOPE 解锁）

现状：`asr.py` 双通道（paraformer-v2 + sensevoice-v1）真实调用已实现，mock 兜底确定性输出；11 项测试全过，从未真跑。

测试/执行计划：

1. 真实转写冒烟：`infisical run --env=dev -- python -c "from app.services.external.asr import transcribe; print(transcribe('<测试wav>'))"`（≤8MB wav）
2. WER 基准（VOI-001）：准备 10-20 段普通话测试音频（含标注文本）→ 计算 WER → 基线入库（JSON，进 CI 金丝雀口径）
3. 情绪映射校准（VOI-003）：SenseVoice 情绪标签 → 关怀分层映射抽查
4. 双通道并行（VOI-002）：总耗时 = max 非 sum 验证
5. 回归：`pytest backend/tests/test_asr.py`（11 项）

### P0-4 · 护栏真实检测验证（B5b，DASHSCOPE 解锁）

1. `infisical run --env=dev -- python -c "from app.services.external.dashscope import moderate; print(moderate('正常文本')); print(moderate('<敏感样本>'))"`
2. fail-safe 验证（SAF-005）：断开/错 key → 默认拒发而非放行
3. 回归：test_external.py / test_asr.py 护栏用例

### P1-1 · Sprint 4 线 C：COS 分片上传 + 断点续传 + 端云对账（S5-03/S5-04 后端，腾讯 key 解锁）

现状：无 upload/reconcile 服务（属新开发）。Sprint 5 规划：S5-03 断电续传（COS 原图分片 + 断点续传 + 校验）、S5-04 端云对账（游标比对 + 差异报告）。

测试/执行计划（开发后）：

1. 建 `services/external/tencent_cos.py`（cos-python-sdk-v5）：put_object / upload_file（分片）/ STS 临时凭证签发（assume-role）
2. `upload_chunks` 表（schema 已有 v3？需确认）+ 分片状态机：中断恢复不丢不重（VOI-010）
3. `services/reconcile.py`：游标比对 + 差异报告（S5-04）
4. 测试：MinIO 本地模拟先行（无外网成本）→ 真 COS 冒烟（上传 1 张真图 → CI 打标 → 下载校验 SHA256）
5. 密钥注入：`infisical run --env=prod -- pytest -m cos`（或 staging 填 Key 后走 staging）

### P1-2 · 腾讯云 CI 图片标签（图片打标，腾讯 key 解锁）

1. 按《_核心结论速查》第 4 节：后端显式调 `POST ci.<region>.myqcloud.com/image/Tagging` → Tags 落库（F1 L2 场景标签 / 搜索标签增强）
2. 测试：真图打标冒烟（成本 ~0.0015 元/次）+ mock 单测（test_external.py 扩展）

### P1-3 · S4-03 微信入库敏感识别（部分解锁）

- 文本敏感：护栏真实检测（P0-4）已解锁
- 图片敏感：CI 图片标签 + 规则判定（P1-2 后接线）；缩略图优先 + 原图异步（B4-3）
- 门禁：命中敏感不进云端镜像（S4-03 DoD）

### P1-4 · S5-05 数据安全审计（部分解锁）

- Infisical 方案已落地（本日）→ 密钥管理审计项 ✅
- 传输/存储/备份演练：`scripts/audit_security.py`（待建）+ backup_pg.ps1 演练（已有）
- Sentry 项待 DSN（仍阻塞）

### P1-5 · 微信"找"逻辑层先行（S4-02，沙箱可测）

- 消息解析 → F5 RAG 搜索 → 结果回复，用 wecom_sandbox 跑通全链路（不依赖真实企微）
- 10s/3s 门禁在沙箱可先测（WX-007 确认回复 P95<10s）
- 真实回调待 WECOM 凭证（阻塞项）

---

## 三、不能推进的工作（缺 Key / 缺外部条件）

| 工作 | 阻塞原因 |
|---|---|
| F6 微信 code2session 真实登录（AUTH-001 真验） | 缺 WECHAT_APPID/WECHAT_SECRET（生产 mock 登录 501 为预期） |
| S4-01 真实企微回调 + S4-02 微信"找"真实链路 | 缺 WECOM_CORP_ID/TOKEN/ENCODING_AES_KEY（当前 503 拒绝为安全设计；沙箱可先行） |
| Sentry 可观测启用（S5-07 内测保障前置） | 缺 SENTRY_DSN_DEV/PROD |
| 高德逆地理（事件聚合 L2 地点命名 / AGG-007 增强） | 缺 AMAP_WEB_API_KEY（后端 service 亦未建，属后续增强项） |
| uni-push 真实推送（消息中心真实通道） | 缺厂商通道密钥（M2 接入时再落，当前 mock 通道） |
| 客户端 T2/T3 同步/UI（S5-01/02 等） | 与 Key 无关：缺 Android 原生 Kotlin 人力（既有风险） |
| 合规三申请（企微认证/ICP/软著） | 与 Key 无关：流程未提交（既有风险，M3 硬依赖） |

---

## 四、本项目全部性能指标需求（汇总自交付文档/规划/测试清单）

> 来源：《忆述光华_测试清单.md》（17 模块 ~140 项）、《开发路线图》、《Sprint2/4/5规划》、《深度设计 B1-B5e》、《产品部验收标准转达稿》。RAG 指标已有测试管线：`research/rag_benchmark/`（metrics.py + run_eval.py + corpus.json + evaluation_report.json）+ `backend/tests/test_rag_metrics.py` + pytest 标记 `-m rag`。

### 4.1 RAG / 搜索（F5，M1/M2 门禁）

| 指标 | 目标 | 来源 |
|---|---|---|
| hit_rate@3（Top3 命中率） | ≥70%（M1/M2 门禁，每版本回归基线） | PERF-002 / API-003 / RET-014 / M1 门禁 |
| 全链路延迟 P95 | <3s（多路召回+过滤+重排+溯源） | PERF-003 / RET-018 / API-003 |
| P99 延迟 | 有预算（未定死） | PERF-003 |
| 路由行为准确率 route_acc | ≥0.90 | run_eval GATE |
| 时间过滤行为准确率 temporal_acc | ≥0.90 | run_eval GATE |
| 分层检索指标（每基准集） | recall@k / hit_rate@k / precision@k / mrr / ndcg@k（corpus-B 文字 84 条 / C 语音 33 条 / D 混合 / E 1 万条规模） | run_eval.py |
| corpus-A 图片基准（文字搜图/以图搜图） | RET-001/002 通过（Qwen3-VL 图塔生效） | 测试清单 |
| 双层 Rerank 后 Top3 | ≥70% 可回归 | RET-014 |
| 规模压力 | P95 随规模曲线记录（--scale-eval 1000/10000） | run_eval.py |
| 并发 | 100 并发不超时、连接池不耗尽 | RET-019 |
| 召回截断 | >50 条截断不丢 Top 关键结果 | RET-006 |
| 排序稳定性 / 溯源 / 多样性 | 重复查询顺序稳定 / 每条可解释 / 结果多样 | RET-015/016/017 |
| 上线评测集（M2 验收前） | 50 条真实查询：faithfulness / relevancy / context precision 三指标基线 | B2 4.3 |
| 检索回归 | Top3 基线版本间不劣化 | REG-004 |
| Qdrant 压测 | 百万向量级 QPS/延迟 | PERF-008 |

### 4.2 事件聚合（F1/B3）

| 指标 | 目标 | 来源 |
|---|---|---|
| 30s 首批 | 授权后首批 ≥50 张事件 ≤30s（端侧 L0/L1；L2/L3 异步补） | PERF-001 / B3 产品部验收 |
| L0 聚类 | 30min（保守模式）/60min（默认）+ 500m 阈值分组正确 | AGG-001 / B3 |
| L1 日聚合 | 跨天不误合、同日多簇正确合并（正确率验收） | AGG-002 |
| L2 主题流分离 | 景点+美食正确分离，互串率有基准 | AGG-003 |
| L3 LLM 语义归并 | 同事件正确归并、不同事件不误并 | AGG-004 |
| 场景分布 | 十类场景（旅行/美食/聚会/宠物/日常/证件/截图/风景等）占比正确 | AGG-006 |
| 无 GPS 归组 / GPS 容错 | 无定位按时间窗归组；<50m 抖动不碎片；大漂移判离群 | AGG-007/009/010 |
| 用户意图 / 增量 / 端云一致 | 手动操作不被反向拆回（AGG-013）；增量不漂移（AGG-015）；端云阈值一致（AGG-016） | 测试清单 |
| 万级聚类耗时 | 万张照片 L0-L2 在预算内 | AGG-017 / PERF-004 |
| 混合素材聚合 / 并发竞态 | 照片+文字+语音同事件；并发归并不脏数据 | AGG-008/018 |

### 4.3 ASR / 语音（F3/B5a）

| 指标 | 目标 | 来源 |
|---|---|---|
| WER | FunASR 普通话转写 WER 达标并可回归 | VOI-001 |
| 双通道并行 | 总耗时 = max 非 sum | VOI-002 |
| 情绪映射 | 情绪标签→关怀分层映射正确；双通道矛盾裁决 | VOI-003/004 |
| 长录音延迟实测 | 5min ≈12-18s；30min ≈1min（API 走百炼） | B5a |
| 分段 | >10min VAD 分段 2-5min，无丢失/重复、时间戳连续、不劈句 | VOI-005/006 |
| 中断/被杀 | 录音中断状态机正确；前台服务被杀自动拉起 | VOI-008/009 |
| 上传幂等 | 断网续传/重传不产生重复记忆 | VOI-010 |
| 吞吐 | 1000 段/日队列水位 | PERF-005 |
| 关怀节流 | 同日多次低情绪不重复推送 | VOI-012 |

### 4.4 分类 / 纠错（F2/F4）

| 指标 | 目标 | 来源 |
|---|---|---|
| SetFit 分类准确率 | ≥75%（M1）→ ≥80%（M2） | M1/M2 门禁 |
| 纠错收益 | 纠错后 7 天同类准确率提升 ≥10%（可测） | B5c / F4 门禁 |
| 交互体验 | 操作路径 ≤3 步；被动确认响应 <2s | B5c 验收 |

### 4.5 微信（F6/M3 门禁）

| 指标 | 目标 | 来源 |
|---|---|---|
| 收+找 | 10s/3s（确认回复 P95<10s） | M3 门禁 / WX-007 |
| 消息可靠性 | 不丢消息 99.9%（msg_id 幂等，重复只入一次） | API-019 / S4 DoD |
| 图片入口 | 3MB/张不超 3s（缩略图优先 + 原图异步） | S4 风险表 |
| 频率限制 | 批量图片队列削峰 + 指数退避 | S4 风险表 |

### 4.6 同步 / 数据（B4/M4/M5 门禁）

| 指标 | 目标 | 来源 |
|---|---|---|
| 端间同步一致 | LWW/软删/游标对账通过 | M4 门禁 |
| 断电续传 | 中断恢复不丢不重 | M4 门禁 / VOI-010 |
| 多端并发 | 吞吐与冲突率 | PERF-006 |
| 备份 | RPO≤24h / WAL≤5min（画像不可逆） | AGENTS.md 约束 |
| 软删除 | 全局 30 天 | AGENTS.md 约束 |

### 4.7 安全 / 护栏（B5b）

| 指标 | 目标 | 来源 |
|---|---|---|
| fail-safe | 百炼不可用默认拒发/延迟而非放行 | SAF-005 |
| 敏感双查 | 回响敏感校验（画像敏感 + 百炼检测） | SAF-006/007 |
| 敏感图片 | 不进云端镜像（S4-03） | S4 DoD |
| 语音隐私 | 录音加密存储、转写文本不落明文日志 | VOI-014 |

### 4.8 资源 / 成本 / 其他性能

| 指标 | 目标 | 来源 |
|---|---|---|
| 搜索延迟口径 | 摘要类 8-15s（qwen-flash 实测；3000+ token 才异步） | 转达稿 / MVP v3 Q22 |
| 月成本 | ≈317-352 元（100 用户，MVP） | B2 第 5 节 |
| PG 百万行 | 28 表核心查询不劣化 | PERF-007 |
| 冷启动 / 长任务 | 冷启动首屏预算内；AI 长任务内存/CPU 峰值受控 | PERF-009/010 |
| CI 性能金丝雀 | JSON 输出、单向阈值、中位数多次重试防 flaky、基线入库 | 决策 #29 |
| 存储 | COS 单 AZ（11 个 9 持久性，容量包 6-7 折） | _核心结论速查 |

---

## 五、执行顺序建议（下一会话起）

1. **对齐 1.3 的 5 项待办**（命名/ARN/workspace/旧 env/staging）——半小时内，扫清真实调用障碍
2. **P0-1 RAG 真实链路**（run_eval 真实模式 → route_acc/hit_rate 对比 → 调优）——M1 门禁核心
3. **P0-3 ASR 真实转写 + WER 基线**——F3 门禁核心
4. **P0-2 图片塔 + corpus-A 基准**——F5 图片检索
5. **P0-4 护栏真验** + **P1-1 COS 分片（先 MinIO 后真 COS）**
6. 每项完成后：progress.md / feature_list.json 登记 + review_agent 全绿 + 契约更新
