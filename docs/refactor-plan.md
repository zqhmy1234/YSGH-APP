# 忆述光华 · 修复与重构执行计划

> 依据 [review-report.md](file:///D:/GuangH-App/review-report.md)(2026-08-20 三方审查汇总)制定。
> 原则:P0/P1 只做局部修复不动架构;P2 重构需逐项审批;每项绑定验证方式。
> 编号规则:`P0-xx`(立即)/ `P1-xx`(本周)/ `P2-xx`(规划)/ `P3-xx`(顺手)。
> 用户已拍板(2026-08-20):①护栏未配 key 默认拒发(fail-closed)②回响双查按"已有敏感标记 + LLM 检测"实现 ③Alembic 落地(ORM 唯一权威)④P2-01 推理移 worker 确认,同步改设计文档。

---

## ✅ 51 项 Checklist(每完成一批打勾)

### P0 安全+数据正确性(7 项)——已完成 2026-08-20

- [x] C-1 上传 IDOR 越权(P0-01)
- [x] C-2 敏感词护栏 URL 早退绕过(P0-02)
- [x] C-3 wechat/delete 未鉴权(P0-03)
- [x] C-4 搜索时间过滤静默失效(P0-04)
- [x] M-7 照片 caption 参数错误(P0-05)
- [x] M-5 ASR mock 假文本入真库(P0-06)
- [x] M-6 护栏 fail-open 未配 key 放行(P0-07)

### P1 技术债+偏离修复(17 项)——✅ 已完成 2026-08-20(210 passed + ruff 全绿)

- [x] M-12 同步 LWW naive/aware 时间比较 500(P1-01)
- [x] B-M7 分片上传无大小校验(P1-02)
- [x] B-M8 mock 凭证接口生产在线(P1-03)
- [x] B-M9 检查-插入非原子并发竞态(P1-04)
- [x] B-M12 CORS 全开(P1-05)
- [x] M-2 回响敏感双查落地(P1-06,按用户拍板:已有敏感标记 + LLM 检测)
- [x] M-9 纠错三道噪音闸门(P1-07)
- [x] R-1 API 错误契约四套统一(P1-08)
- [x] R-2 常量/工具去重(P1-09)
- [x] R-5 模型路径 CWD 依赖(P1-10)
- [x] B-M3/M4/M14 N+1 修复(P1-11)
- [x] B-M13 队列优先级接线(P1-12)
- [x] B-M15 敏感词打码映射失效(P1-13)
- [x] B-M16 server_version 用户级游标(P1-14)
- [x] R-3 updated_at onupdate(P1-15)
- [x] M-11 长录音 VAD 分段(P1-16,webrtcvad-wheels)
- [x] 测试质量:恒真断言/SetFit 同源评估集(P1-17)

### P2 架构重构(7 项)——✅ 已完成 2026-08-20(211 passed + alembic check 零漂移)

- [x] B-M13 推理移出 API 进程(P2-01):classify/arbitrate 入队异步+job 轮询;search 并发信号量(4)。⚠️ 待办:同步改设计文档/OpenAPI 契约(用户确认时要求)
- [x] R-6 worker 模块拆分(P2-02):process_content 下沉 services/pipeline.py,worker 只留入口+启动命令
- [x] R-7 research 包边界(P2-03):event_aggregation 移入 backend/app/services/,删 sys.path hack
- [x] R-8 单例收敛 + DI(P2-04):security 函数内读、correction Qdrant 统一入口、fake 容量上限+reset(全量 DI 注入留后续)
- [x] R-9 Alembic baseline(P2-05):ORM 唯一权威,遗留空表收敛,check 零漂移;FinetuneJob 纳入 ORM
- [x] R-4 路由前缀/游标统一(P2-06):asr 域拆分(guard 独立 /api/v1/guard),删 SearchQuery.cursor 死字段
- [x] A-M3/M10 事件 L2/L3 + 以图搜图生产接线(P2-07):L2/L3 候选落库 draft 事件;photo 写 image_vec

### P3 顺手清理(低收益项)——部分完成 2026-08-20

- [x] 未用依赖清理(openai/slowapi/datasketch/passlib/bcrypt/python-dotenv 已删;alembic 已启用;httpx/pgvector 标注 dev)
- [x] 死代码清理(token_is_valid、_PRESET_SENSITIVE_WORDS、_rule_check 已删;retry_job 随 P2-02 移除)
- [x] conftest.py 抽取(backend/conftest.py 建立,27 个测试文件 sys.path 样板已清,ruff 自动修复 import)
- [ ] 状态值中英混用收敛为枚举(涉及 DB CHECK 约束+存量迁移,风险>收益,暂缓;建议后续单独排)
- [ ] 22:00 复盘 + reflow 调度固化(需部署方式决策:RQ scheduler vs 系统 cron,当前仅手动脚本)
- [x] storage fake 容量上限(512MB/1 万对象)+ reset(见 P2-04);COS 流已 close(MinIO 路径已处理);STS 子账号角色(用户搁置)
- [ ] deps UUID 校验/interview 鉴权豁免/debug 默认 False(小项,随日常改动顺手做)
- [ ] integration 标记容器化 + rag 测试 collection 隔离(需 CI 环境决策)

---

## P0 立即修复(安全 + 数据正确性)——✅ 已完成(2026-08-20,202 passed + ruff 全绿)

| 编号 | 任务 | 状态 | 验证 |
|------|------|------|------|
| P0-01 | 上传任务归属校验(IDOR) | ✅ | 新增 test_cross_user_access_denied |
| P0-02 | 敏感词护栏绕过修复 | ✅ | 新增 test_sensitive_word_plus_url_rejects |
| P0-03 | wechat_delete 补鉴权 | ✅ | 新增 test_wechat_delete_requires_auth |
| P0-04 | 搜索时间过滤失效 | ✅ | 新增 test_time_filter_actually_filters(epoch payload) |
| P0-05 | 照片 caption 参数错误 | ✅ | test_pipeline photo 用例适配(先落临时文件) |
| P0-06 | mock 转写拒绝入库 | ✅ | 生产模式拒绝 mock 转写 |
| P0-07 | 护栏 fail-open 修复 | ✅ | 生产未配 key → 拒发(用户拍板) |

> 注:test_rag 的 test_dense_search_recall 失败为既有环境污染(生产 collection 158 点挤占 top10),与本次改动无关;rag 测试 collection 隔离已列入 P3。

---

## P1 技术债与偏离修复(本周,按依赖排序)

| 编号 | 任务 | 位置 | 做法 | 验证 |
|------|------|------|------|------|
| P1-01 | 同步时间比较 500 | services/sync.py:79,148 + reconcile.py | `_parse_ts` 统一 naive 视为 UTC;防御 try/except TypeError | 补无时区时间戳用例 |
| P1-02 | 分片大小校验 | api/upload.py:57 + services/upload.py | `len(data) > task.chunk_size → 422`;Content-Length 预检 | 补超大片用例 |
| P1-03 | mock 凭证生产门禁 | api/contents.py:85-103 | mock 分支加 `settings.mock_external_ai` 或环境门禁;生产返回 501;upload_sts 错误消息去掉异常类型 | 补生产环境用例 |
| P1-04 | 并发竞态 IntegrityError | api/auth.py:162-180 + contents.py:40-47 + sync.py:65-67 | 捕获 IntegrityError 回滚重查(或 ON CONFLICT) | 补并发用例 |
| P1-05 | CORS 白名单 | main.py:56-58 | 按 settings 注入白名单,生产收紧 | 冒烟 |
| P1-06 | 回响敏感双查落地 | services/echo.py + 入库管线 | 实现画像敏感查询 + sensitive_tags 写入管线;echo 出包前双查(画像 + moderate) | 补双查用例;与产品部确认口径 |
| P1-07 | 纠错三道闸门 | services/correction.py:168-197 | echo/org 来源按 (user, old→new) 聚合 ≥3 次才写规则层;3 天回改检测 | 补闸门用例 |
| P1-08 | 错误契约统一(R1) | api/corrections/classify/messages/asr/search | 全部改抛 ApiError;asr 200+code 改 4xx | 回归 + 断言错误体含 code/request_id |
| P1-09 | 常量/工具去重(R2) | sync/reconcile/corrections/classifier/worker | 抽 sync_common;标签词表唯一权威;retry_job 委托 with_retry | 相关测试全量回归 |
| P1-10 | 模型路径 CWD 依赖(R5) | rerank.py:23 + config.py:88 | 统一 `Path(__file__)` 解析 | 从 backend/ 目录启动冒烟 |
| P1-11 | N+1 修复 | api/events.py:40-64 + services/events.py:175-200 + echo.py:74-92 | GROUP BY 聚合、合并批量查询、taken_at BETWEEN 走索引 | 回归 + 查询计数断言 |
| P1-12 | 队列优先级接线 | api/contents.py:79 + core/queue.py | voice/photo 走 enqueue_high;或删除未用队列 | 回归 |
| P1-13 | 敏感词打码映射修复 | sensitive_words.py:145,168-172 | 归一化文本打码后映射回原文(偏移记录) | 补全角/空格变体用例 |
| P1-14 | server_version 用户级游标 | services/sync.py:240-243 | 按 user_id 过滤取最大 id | 补多用户用例 |
| P1-15 | updated_at onupdate(R3) | db/models.py 全部带 updated_at 列 | 加 `onupdate=func.now()`;interview 冗余 commit 收敛单事务(R12) | 补 updated_at 自动前进断言 |
| P1-16 | VAD 分段(大项) | api/asr.py + services/external/asr.py | 接入 webrtcvad,>5min 分段 2-5min 转写合并 + 失败段重试;8MB 上限按分段策略放开 | 补分段用例(可 mock VAD) |
| P1-17 | 测试质量修复 | tests/test_auth.py:22 + test_setfit.py | 删恒真断言;SetFit 门禁换独立评估集(与训练种子分离) | CI 全绿 |

**P1 完成标准**:pytest 全量通过 + ruff 全绿 + 新增/修正用例覆盖上述各项。

---

## P2 架构重构(逐项审批,先补测试再动)

| 编号 | 任务 | 来源 | 前置条件 | 风险控制 |
|------|------|------|---------|---------|
| P2-01 | 推理移出 API 进程(SetFit/BGE/LLM 全部入队 worker) | B-M13 | P1-12 队列接线完成 | API 只 enqueue + 轮询结果;模型只驻 worker;压测确认 P95 |
| P2-02 | worker 模块拆分:process_content 下沉 services/pipeline.py | C-R6 | 先固化 pipeline 全类型测试;低峰期切换并清空 RQ 队列 | 存量 job pickle 路径失效风险;切换后 E2E 验证 |
| P2-03 | research/event_aggregation 纳入 backend 包 | C-R7 | 先固化事件聚合集成测试 | 删 sys.path hack;迁移 imports/测试 fixture;部署包内冒烟 |
| P2-04 | 单例收敛 + 轻量 DI(settings 注入) | C-R8 | 先锁定 fake 存储语义测试 | correction Qdrant 统一走 vector_store 工厂;security 改函数内读;勿合并错 collection |
| P2-05 | Alembic baseline 落地 | C-R9 | schema.sql 与 ORM 对账 | baseline 与现网库严格一致(alembic check 零差异) |
| P2-06 | 路由前缀 + 游标格式统一 | C-R4 | 与 T2 客户端同步 | SearchQuery.cursor 删或实现(建议删);openapi.json 重新导出 |
| P2-07 | 事件 L2/L3 落库 + 以图搜图生产接线 | A-M3/M-10 | P0-05 图片 caption 修复完成 | L2 LLM 归并 + L3 主题流;photo_event 多对多;按里程碑如实标注"仅 L1" |

---

## P3 顺手清理(排队,随改随清)

- 删除未用依赖:openai / slowapi / datasketch / passlib[bcrypt] / alembic(若 P2-05 前) / python-dotenv(requirements 审计结论)
- 死代码清理:retry_job / token_is_valid / enqueue_high / _PRESET_SENSITIVE_WORDS / upsert_image_vec 生产侧 / classify_batch 接线(worker 改攒批)
- 状态值中英混用收敛为枚举(ContentStatus 等,DB 存量兼容)
- 22:00 复盘 + reflow 微调调度:确认部署方式(RQ scheduler / 系统 cron)后固化
- storage:fake 单例容量上限、COS 流 close、STS 子账号角色
- deps.py UUID 校验、interview 无鉴权豁免、debug 默认 False
- conftest.py 抽取(消除 28 文件 sys.path 样板)+ integration 标记容器化

---

## 执行顺序与依赖

1. P0(7 项)独立并行,预期半天;完成后全量回归。
2. P1 按 01→17 顺序执行;其中 P1-08/09/10/15 为纯重构,可先做;P1-16(VAD)独立大项,单独排。
3. P2 逐项提交审批;P2-01 依赖 P1-12,P2-02 依赖 P2-01 决策,P2-07 依赖 P0-05。
4. P3 随日常改动顺手清理,不单独排期。
5. 每阶段结束更新 [progress.md](file:///D:/GuangH-App/progress.md) 与 [feature_list.json](file:///D:/GuangH-App/feature_list.json)(纠正"已完成"虚标项,如事件聚合仅 L1、护栏未接托管等)。

## 需要你决策的问题

1. P0-07 护栏 fail-open:确认生产未配 key 时默认拒发(与 SAF-005 一致)还是保持放行等配置到位?
2. P1-06 回响双查:画像敏感维度定义(用现有画像哪些字段)需产品部确认,是否先按"已有敏感标记内容 + LLM 检测"双查实现?
3. P2-01 推理移 worker 涉及接口语义变更(同步变异步),是否接受?若接受,前端/T2 需同步适配。
4. P2-05 Alembic:确认以 ORM 为唯一权威、schema.sql 降级为参考?
