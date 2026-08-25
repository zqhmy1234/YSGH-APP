# 忆述光华 · 三方审查汇总报告

> 审查日期:2026-08-20｜审查方式:3 个并行 subagent(设计偏离 / 技术债 / 重构机会)+ 主会话交叉去重
> 原始报告(含完整证据):
> - [审查A · MVP 设计偏离](file:///D:/GuangH-App/.cowork-temp/review/report-A-mvp-deviation.md)(2 CRITICAL + 10 MAJOR + 13 MINOR)
> - [审查B · 技术债审计](file:///D:/GuangH-App/.cowork-temp/review/report-B-tech-debt.md)(3 CRITICAL + 16 MAJOR + 18 MINOR)
> - [审查C · 重构机会](file:///D:/GuangH-App/.cowork-temp/review/report-C-refactor.md)(5 高收益低风险 + 5 高收益高风险 + 4 低收益)

---

## 一、总体结论

代码质量高于一般水平(统一 ApiError、分层清晰、决策注释完整、CI 门禁有效),但存在 **3 个安全漏洞(1 个 IDOR、1 个护栏绕过、1 个未鉴权删除)、2 个生产数据正确性硬伤(搜索时间过滤失效、同步写影子表)、6 处"声称已完成实为占位/死代码"**。核心业务链路(B2 检索、SetFit 微调、微信协议、分片上传、认证)实现质量高,偏离集中在"边缘链路"和"宣称完成但未接线"。

去重后共 **4 CRITICAL + 18 MAJOR + 16 MINOR + 13 重构项**。

---

## 二、CRITICAL(安全 + 数据正确性,必须立即修复)

> ✅ 状态更新(2026-08-20):以下 4 项 CRITICAL 已全部修复(P0),见 [refactor-plan.md](file:///D:/GuangH-App/refactor-plan.md)。

| # | 问题 | 来源 | 位置 | 影响 | 状态 |
|---|------|------|------|------|------|
| C-1 | 上传接口 IDOR:upload_chunk/get_status/complete_upload 不校验任务归属 | B-C1 | [upload.py](file:///D:/GuangH-App/backend/app/services/upload.py) | 任意登录用户可注入分片/查看/合并他人上传任务,数据完整性破坏 | ✅ 已修复 + 越权用例 |
| C-2 | 敏感词护栏绕过:URL 黑名单命中即提前 return,跳过 reject 级词表 | B-C2 | [sensitive_words.py](file:///D:/GuangH-App/backend/app/services/external/sensitive_words.py) | "敏感词+黑名单网址"同现时只打码不拦截,reject 语义被旁路 | ✅ 已修复 + 同现用例 |
| C-3 | 未鉴权删除接口:wechat_delete 无 get_current_user | B-C3 / C-R10 | [wechat.py](file:///D:/GuangH-App/backend/app/api/wechat.py) | 匿名可枚举 msg_id 批量软删他人消息记录 | ✅ 已修复 + 401 用例 |
| C-4 | 搜索时间过滤生产静默失效:payload 存 ISO 字符串,过滤用数值 Range(Qdrant 实测空结果无报错) | A-C1 | [vector_store.py](file:///D:/GuangH-App/backend/app/services/vector_store.py) + [worker.py](file:///D:/GuangH-App/backend/app/workers/worker.py) | "去年/昨天/上个月"类查询必然空结果,B2-2"payload filter"实际未落地 | ✅ 已修复(epoch 秒 payload)+ 集成测试 |

---

## 三、MAJOR(MVP 偏离 + 明显债项)

> ✅ 状态更新(2026-08-20):本表所列 MAJOR 已全部修复(P1 技术债 + P2 架构),见 [refactor-plan.md](file:///D:/GuangH-App/refactor-plan.md)。
> 遗留说明:P2-07 L2/L3 为候选级落库(LLM 归并待真实数据);P2-01 契约变更待同步设计文档/OpenAPI。

### MVP 设计偏离(审查A,均带设计文档对照)

| # | 问题 | 位置 | 对照文档 |
|---|------|------|---------|
| M-1 | B4 同步只写影子表,从不写 contents 主库:LWW 更新/软删对搜索/回响/聚合不可见 | [sync.py](file:///D:/GuangH-App/backend/app/services/sync.py):42-195 | B4-2"云端为主库"名存实亡 |
| M-2 | 回响敏感"双查"缩水:sensitive_status 仅微信路径写入、sensitive_tags 零写入、画像敏感校验无代码 | [echo.py](file:///D:/GuangH-App/backend/app/services/echo.py):37-40 | B5b §2 双查 / SAF-006/007 |
| M-3 | 四层事件模型只落 L1:L0/L2/L3 明标"原型占位" | [events.py](file:///D:/GuangH-App/backend/app/services/events.py):31-33,112-133 | B3 §3 四层模型 |
| M-4 | 情绪关怀/每日复盘/语音完成推送全链路无触发(死代码 + 无 22:00 调度) | [notify.py](file:///D:/GuangH-App/backend/app/services/notify.py):10-11,89,110 | B5a §4 / F3 gate |
| M-5 | ASR 双通道实际单通道:SenseVoice 账号不可用恒"平静";mock 假文本无防护回写入库 | [asr.py](file:///D:/GuangH-App/backend/app/services/external/asr.py):31,88-100,126-134 | B5a §1-2 |
| M-6 | 护栏未接百炼托管 qwen_response_check;未配 key 时 fail-open(与 SAF-005 默认拒发相反) | [dashscope.py](file:///D:/GuangH-App/backend/app/services/external/dashscope.py):26,148-150 | B5b §2 / SAF-005 |
| M-7 | 照片 caption 生产链路参数错误:COS key 传给需要本地路径的 API,必然静默失败 | [worker.py](file:///D:/GuangH-App/backend/app/workers/worker.py):150 + [dashscope.py](file:///D:/GuangH-App/backend/app/services/external/dashscope.py):105-109 | B2 §4.1 |
| M-8 | 微信"找"未接真实回调;收图不做 image_audit(死代码) | [wechat.py](file:///D:/GuangH-App/backend/app/api/wechat.py):117-124 | B4 §6 / B5b §5 |
| M-9 | 纠错三道噪音闸门缺失;echo 被动确认单条即生效(未做 ≥3 次一致) | [correction.py](file:///D:/GuangH-App/backend/app/services/correction.py):168-197 | B5c §2/§6 |
| M-10 | 以图搜图生产 collection 恒空;检索 payload 无 place/tags,显式过滤恒空靠回退掩盖 | [vector_store.py](file:///D:/GuangH-App/backend/app/services/vector_store.py):160 + [worker.py](file:///D:/GuangH-App/backend/app/workers/worker.py):44-58 | B2-2/B2-4 |
| M-11 | 长录音 VAD 分段未做(8MB≈4 分钟上限,超限 422) | [asr.py](file:///D:/GuangH-App/backend/app/api/asr.py):23-24 | B5a §3 |

### 技术债(审查B)

| # | 问题 | 位置 |
|---|------|------|
| M-12 | 同步 LWW naive/aware 时间比较 TypeError → 500 | [sync.py](file:///D:/GuangH-App/backend/app/services/sync.py):79,148 + [reconcile.py](file:///D:/GuangH-App/backend/app/services/reconcile.py):21-28,53 |
| M-13 | API 请求线程同步跑重推理(SetFit 27s/BGE-M3 1.2GB/LLM 90s+),P95<3s 不可达,线程池被占满 | [classify.py](file:///D:/GuangH-App/backend/app/api/classify.py):26 / [rag.py](file:///D:/GuangH-App/backend/app/services/rag.py):202,239 |
| M-14 | 事件时间轴 N+1 + 无分页 + photo_count 语义错误 | [events.py](file:///D:/GuangH-App/backend/app/api/events.py):40-64 |
| M-15 | 手动合并事件 N+1 | [events.py](file:///D:/GuangH-App/backend/app/services/events.py):175-200 |
| M-16 | with_retry 每次新建线程池且超时无法中断,线程泄漏 | [retry.py](file:///D:/GuangH-App/backend/app/services/external/retry.py):72-82 |
| M-17 | 分片上传无分片大小校验(客户端自报 chunk_size) | [upload.py](file:///D:/GuangH-App/backend/app/api/upload.py):57 |
| M-18 | mock 凭证接口生产在线(/contents/presign 返回假凭证;upload_sts 泄漏异常类型) | [contents.py](file:///D:/GuangH-App/backend/app/api/contents.py):85-103 |
| M-19 | 检查-插入非原子(登录/照片去重/sync op_id 幂等)→ 并发 IntegrityError 500 | [auth.py](file:///D:/GuangH-App/backend/app/api/auth.py):162-180 |
| M-20 | 全表/全量加载无界查询(mark_global_candidates / reconcile) | [correction.py](file:///D:/GuangH-App/backend/app/services/correction.py):234 / [reconcile.py](file:///D:/GuangH-App/backend/app/services/reconcile.py):33 |
| M-21 | async 处理器内跑同步阻塞(wechat_callback 内 DB+LLM) | [wechat.py](file:///D:/GuangH-App/backend/app/api/wechat.py):73-93 |
| M-22 | CORS 全开 allow_origins=["*"] | [main.py](file:///D:/GuangH-App/backend/app/main.py):56-58 |
| M-23 | 队列优先级未接线:voice/photo 全走 enqueue_low,enqueue_high 死代码 | [queue.py](file:///D:/GuangH-App/backend/app/core/queue.py):19-30 |
| M-24 | 回响查询 func.extract 不可走索引 + 逐条 N+1 | [echo.py](file:///D:/GuangH-App/backend/app/services/echo.py):74-92 |
| M-25 | 敏感词打码失效:归一化命中但原文 replace 落空 | [sensitive_words.py](file:///D:/GuangH-App/backend/app/services/external/sensitive_words.py):145,168-172 |
| M-26 | sync server_version 返回全库游标而非当前用户 | [sync.py](file:///D:/GuangH-App/backend/app/services/sync.py):240-243 |

### 重构机会(审查C,高收益)

| # | 问题 | 方案 | 风险 |
|---|------|------|------|
| R-1 | API 错误契约四套并存(ApiError / HTTPException / 200+code / 裸 ValueError) | 统一 ApiError | 低 |
| R-2 | 常量/工具重复(_parse_ts、TOMBSTONE_FIELD、标签词表三份、两套重试) | 抽公共模块 | 低 |
| R-3 | updated_at 全部无 onupdate,手工赋值口径不一 | 加 onupdate=func.now() | 低-中 |
| R-4 | 路由前缀(2 处)与游标格式(3 种)不一致;SearchQuery.cursor 死字段 | 统一约定 | 低(涉及契约) |
| R-5 | 模型路径解析依赖 CWD(相对路径 "backend/models/...") | 统一 __file__ 解析 | 低 |
| R-6 | worker 模块职责错位:实为内容 AI 管线被 api 直接 import;RQ 入口无调用方 | 管线下沉 services/pipeline.py | 中(需清队列) |
| R-7 | backend 反向依赖 research(sys.path hack) | 迁入 backend 或独立包 | 高 |
| R-8 | 单例/全局状态三套模式,无 DI,测试靠 monkeypatch | 收敛统一客户端工厂 + Depends 注入 | 中 |
| R-9 | ORM ↔ schema.sql 双源无版本管理(alembic 已声明未用) | Alembic baseline 落地 | 中 |
| R-10 | 鉴权边界不一致(同 R-10= C-3) | 补鉴权 | 低 |

---

## 四、MINOR(精简,详见原始报告)

- 短信/微信登录生产 501、`_wechat_configured` 恒 False 占位与真实实现分裂(A-m4/B-m15)
- interview 无置信度机制(B1 2.3 未实现)、单请求 3 次 commit(A-m7/C-R12)
- 回响/事件标题时区口径不一致(A-m10/m11)
- 22:00 复盘与 reflow 微调无自动调度,依赖手工 cron(A-m12)
- 时间表达仅 4 个粗粒度,"去年夏天"类不支持(A-m13)
- 死代码:retry_job/token_is_valid/enqueue_high/_PRESET_SENSITIVE_WORDS/mock 占位(B-m5)
- storage fake 单例内存无限增长、COS 流未 close、STS 用 root 密钥(B-m6/m7/m14)
- debug 默认 True、deps.py UUID 未校验、interview 无鉴权(B-m8/m9/m10)
- with_retry timeout=None 也建池、ASR rec.call 无超时(B-m1/m12)
- 向量 upsert 读-改-写非原子(B-m13)、security 导入期快照(B-m16/C-R8)
- 状态值中英混用无枚举(C-R11)、延迟 import 风格不一(C-R13)
- **测试质量**:test_auth 恒真断言 `or True`;test_setfit 评估集与训练同源(门禁虚高);无跨用户/naive 时间戳/wechat delete 用例;无 conftest.py(28 文件重复样板);integration 依赖本机 PG(B-测试缺口)

---

## 五、亮点肯定

1. **B2 检索核心链路真实落地**:BGE-M3 dense+sparse 真编码、RRF 0.7/0.3、溯源 trace、降级 degraded、NER 回退——质量高。
2. **SetFit 分类+微调流水线**:真实训练、reflow 备份/staging/门禁 75% 才换入——B5c 第②③层扎实。
3. **护栏规则层**:开源词库 4 类 + 网址黑名单 O(1) + 三号打码,两档处置合理。
4. **回响每天≤1 条有 DB 部分唯一索引兜底**(并发 IntegrityError 回滚)。
5. **微信回调协议 1:1**(AES-256-CBC+SHA1 官方互通)、未配凭证 503 拒绝。
6. **分片上传状态机**:SHA256 分片校验、断点续传、complete 幂等。
7. **事件手动操作**:merge/split/confirm + EventEditLog 审计 + 用户操作优先。
8. **认证**:refresh 轮换+设备吊销、验证码 SHA-256+防刷限流、时序安全比较。
9. **工程纪律**:统一重试封装、CI review_agent 门禁、Infisical 密钥链路、教训 hook。

---

## 六、等团队/依赖项(非代码问题,阻塞对应功能)

- 微信"找"真实回调:缺 WECHAT_APPID/SECRET、WECOM_CORP_ID/TOKEN/AES_KEY
- 情绪关怀文案库、模板骨架池:等产品部提供
- 纠错测量口径(全量重测 vs 抽样):等产品部拍板
- 50 条真实搜索查询/100-200 条真实碎片/20-50 段真实录音:门禁硬依赖
- UTS POC:需 Android 原生人力(全局 Gate)
