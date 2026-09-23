# 域⑥ · 回响/胶囊/消息 深度审计

> 波次：易扩展/易维护重构波 · Phase A（逐功能域深度代码审计）
> 性质：**严格行为等价侦察**——本文件只登记，不改任何业务代码。
> 审计日：2026-09-24 ｜ 基线：develop 工作区（`git status --short` 见下）
> 范围：`backend/app/` + `client/` + `scripts/` + `deploy/`；`agent/` 排除。

## 覆盖范围与实读清单

行号口径 = Read 工具 `cat -n` 实际看到的行号（权威）；未读到的部分明确标注。

### 后端（实读）
| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/app/api/echo.py` | 53 | 全文 |
| `backend/app/api/capsules.py` | 187 | 全文（含 `_load_owned_capsule` L45、`_content_brief` L55） |
| `backend/app/api/messages.py` | 144 | 全文（含 `load_owned_message` L37） |
| `backend/app/api/deps.py` | 102 | 全文（含 `load_alive_content` L84） |
| `backend/app/services/echo.py` | 339 | 全文（含死码 `_is_sensitive` L152、画像敏感 CRUD L30-149） |
| `backend/app/services/notify.py` | 348 | 全文（推送端口 L36-73、create_message L178-216） |
| `backend/app/workers/capsule_scan.py` | 89 | 全文 |
| `backend/app/schemas/message.py` | 30 | 全文 |
| `backend/app/schemas/echo.py` | 20 | 全文 |
| `backend/app/schemas/capsule.py` | 48 | 全文 |
| `backend/app/db/models/message.py` | 33 | 全文 |
| `backend/app/db/models/capsule.py` | 37 | 全文（确认无 `deleted_at`） |
| `backend/app/db/models/echo.py` | 39 | 全文（确认部分唯一索引 L23-31） |
| `backend/app/services/auth/providers.py` | 实读 1-100（短信端口 + TODO(T1) L11/L73） | 299 行，仅端口段 |
| `backend/app/services/auth/auth.py` | 实读 70-110（生产门控 L95-96 + TODO L92） | 357 行，仅 send_sms 段 |
| `backend/app/services/pipeline_ext/emotion.py` | 实读 95-123（notify 接线 try/except） | 全文相关段 |
| `backend/scripts/daily_review.py` | 66 | 全文（定时编排入口） |
| `backend/app/api/contents.py` | 实读 80-95、460-515、580-690、870-890 | 807 行；`profile_sensitive_router` L89 + 3 端点 L466/484/501；`load_alive_content` 消费点 |
| `backend/app/main.py` | 实读 38、120 | router 注册 |

### 测试（实读）
`backend/tests/test_authz_gate.py`（296）、`test_echo.py`（348）、`test_ba2_capsule.py`（330）、`test_notify.py`（334）。

### 客户端（实读）
`client/pages/messages/messages.uvue`（579 全文）、`client/components/EchoSheet/EchoSheet.uvue`（252 全文）、`client/components/CapsuleSheet/CapsuleSheet.uvue`（342 全文）、`client/components/MessageDetailSheet/MessageDetailSheet.uvue`（179 全文）、`client/utils/capsule_api.uts`（172 全文）、`client/utils/play.uts`（实读 30-140；文件 ~612 行，echo/message 封装段）。

### 未实读（仅 grep 命中，结论标注为候审）
`deploy/RUNBOOK.md`（§5 定时任务表 L151-161，经 grep 命中，未逐行通读全文）、`deploy/systemd/yishu-worker.service`、`client/components/TabIndex/TabIndex.uvue`（echo 卡消费点 L236-760，仅 grep）。

### `git status --short`
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
**无在途业务文件**（AGENTS.md 记载的「第四窗媒体票据 8 文件在途」当前不在工作区，工作树对 `backend/app`/`client/` 干净）。

---

## 归属 helper 三分裂对照表（本域重点）

| 维度 | `capsules._load_owned_capsule` | `deps.load_alive_content` | `messages.load_owned_message` |
|---|---|---|---|
| 位置 | `backend/app/api/capsules.py:45-52` | `backend/app/api/deps.py:84-102` | `backend/app/api/messages.py:37-50` |
| 所在层 | api（域内私有） | api/deps（共享） | api（域内，**公有名**） |
| 可见性 | `_` 私有 | 公有 | 公有（无下划线） |
| 查询形状 | `select(Capsule).where(id==, user_id==)` | `select(Content).where(id==, user_id==, deleted_at IS NULL)` | `select(Message).where(id==, user_id==)` |
| 软删过滤 | **无**（Capsule 模型无 `deleted_at` 列） | **有**（`Content.deleted_at.is_(None)`） | **无**（Message 模型无 `deleted_at` 列） |
| 归属条件 | 有（`Capsule.user_id == user_id`） | 有（`Content.user_id == user_id`） | 有（`Message.user_id == user_id`） |
| 错误码/HTTP | `CAPSULE_002` / 404 / 「胶囊不存在或无权访问」 | `ERR_CONTENT_010` / 404 / 「内容不存在或无权访问」 | `ERR_MSG_002` / 404 / 「消息不存在」 |
| 404 vs 403 | 统一 404（不区分不存在/非本人，IDOR 防护） | 统一 404（不区分三种情形） | 统一 404（不区分两情形） |
| 消费点 | `capsules.py:156`（open）、`capsules.py:182`（delete） | `capsules.py:103`；`contents.py:593/613/627/648/667/683/883`（共 8 处） | `messages.py:122`（mark_read） |
| 门禁登记 | `test_authz_gate.py:47` 登记名 `load_owned_capsule`（**无下划线**）≠ 实际符号 | 登记名一致（L45） | 登记名一致（L48） |

**语义是否相同**：**是**——三者承载同一语义「按 id + user_id 取本人单行，未命中统一 404 不泄露存在性」，差异仅在实体类型、软删列有无、域内错误码（契约要求保留）。故属**真三分裂**（同一语义三实现），非三套不同语义。

**收敛最小方案**

1. `app/api/deps.py` 新增一个私有泛化实现（约 12 行）：
   `_load_owned(db, model, id_col, *, user_id, err_code, message, alive_col=None)` —— 承载唯一查询形状与「未命中 → `ApiError(err_code, message, http=404)`」。
2. `deps.load_alive_content`（deps.py:84）改为委托 `_load_owned(..., alive_col=Content.deleted_at)`：**对外签名/错误码/文案/HTTP 码逐字不变**。
3. `messages.load_owned_message` 迁至 `deps.py`，`messages.py` 保留一行兼容再导出（`from app.api.deps import load_owned_message  # noqa: F401`）→ 消费点 `messages.py:122` 与门禁 AST 均不变。
4. `capsules._load_owned_capsule` 改名为 `load_owned_capsule` 并迁 `deps.py`（或留 capsules 内薄壳委托），**同步修正 `test_authz_gate.py:47` 的登记名**——不改名则该 loader 永远不会被门禁识别（见 D06-2）。

**风险**
- 错误码/文案必须逐字保留（`CAPSULE_002`/`ERR_CONTENT_010`/`ERR_MSG_002` 三套并存是**对外契约**，泛化实现须把 err/message 作参数，禁止统一错误码——统一会直接打挂 `test_ba2_capsule.py:293-306`、`test_notify.py:189-194` 的码断言）。
- `alive_col` 仅 Content 适用；Capsule/Message 无软删列（`models/capsule.py:23-37`、`models/message.py:11-33` 已确认）→ 传 None，不可臆造软删过滤（会改变 `capsules`/`messages` 现行为）。
- import 面变动 3 处（`contents.py:29`、`capsules.py:21`、`messages.py`）。

**monkeypatch 面（会不会影响现有测试）**
- **无影响**：全仓测试对这三个 loader **零 monkeypatch**（grep 全 `backend/tests`，仅 `test_notify.py:41`/`:279` patch `notify.datetime`；`test_ba2_capsule.py:265` 复位 `scan_mod._last_scan_ts`）。
- 门禁 `test_authz_gate.py` 用 AST 认 `fn.id`（裸名调用）**或** `fn.attr`（模块限定调用，L141-142），故迁到 deps 后 `deps.load_owned_message(...)` 形态同样通过；`test_scanner_accepts_both_valid_patterns`（L249-278）已固化该双向判定。

**验证手段**
`python -m pytest backend/tests/test_authz_gate.py backend/tests/test_ba2_capsule.py backend/tests/test_notify.py backend/tests/test_echo.py backend/tests/test_ab_scenarios.py backend/tests/test_data_chains_12.py -q`
（前置：PG yishu 库 + Docker Desktop 起 yishu-redis/yishu-qdrant，见 AGENTS.md 已知环境项。）

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D06-1 | `backend/app/api/capsules.py:45`、`backend/app/api/deps.py:84`、`backend/app/api/messages.py:37` | 同语义三实现（归属 loader 三分裂） | P1 | 统一「按 id 取本人实体」入口，新增域不再自造第 4 份 | 三套对外错误码须逐字保留，误统一即破契约测试 | 行为等价替换 | pytest test_authz_gate + test_ba2_capsule + test_notify + test_echo + test_ab_scenarios | 批次1（与 D06-2 同批） | 确认 |
| D06-2 | `backend/tests/test_authz_gate.py:47` vs `backend/app/api/capsules.py:45` | 门禁登记名与实际符号不匹配（哑条目） | P1 | 门禁真能认 capsules loader，而非靠 `user.id` 兜底 | 改名须同步门禁与 2 个消费点 | 纯移动+兼容再导出 | pytest test_authz_gate（含 test_whitelist_and_exempt_entries_still_exist） | 批次1 | 确认 |
| D06-3 | `backend/app/services/echo.py:152`（死） vs `backend/app/services/echo.py:239-254`（活） | 死码 + 同语义双实现（回响候选敏感筛选） | P1 | 删除死码消除「两处语义各自演化」漂移 | 删码前须确认无外部 import（已证零调用） | 删码(需证死) | `Select-String "_is_sensitive\("` 全仓仅 1 命中（定义行）；pytest test_echo | 批次1 | 确认 |
| D06-4 | `backend/app/api/capsules.py:157-167`、`backend/app/workers/capsule_scan.py:56`、`client/components/CapsuleSheet/CapsuleSheet.uvue:138` | 到期判定三实现 | P1 | 到期语义单一来源（避免口径漂移） | 三处时区/边界口径不同（含本地↔UTC），收敛须先对齐口径 | 行为等价替换 | pytest test_ba2_capsule（open 409 + scan due 幂等） | 批次2 | 确认 |
| D06-5 | `client/utils/capsule_api.uts:23/31`、`deploy/RUNBOOK.md:151-161`、`backend/app/workers/capsule_scan.py:83-89` | 注册点缺失（到期提醒链路无触发入口） | P1 | 补齐定时登记，到期提醒才可达 | 生产 crontab 在仓库外，无法静态证实 | 新增工具（补登记） | 见 D06-5 证据（客户端零 GET /capsules + RUNBOOK §5 无 scan） | 批次2（需人工确认生产 cron） | 候审 |
| D06-6 | `backend/app/services/echo.py:30-149` + `backend/app/api/contents.py:466-511` | 职责边界错位（画像敏感 CRUD 住 echo 服务，由 contents 路由消费） | P1 | 域职责归位，echo 服务回归回响 | 画像敏感被 3 处消费（回响 L1 / B1-6 API / 测试），搬迁需保持导入路径 | 纯移动+兼容再导出 | pytest test_echo（test_profile_sensitive_* 5 例） | 批次2 | 确认 |
| D06-7 | `backend/app/api/messages.py:100-104`、`:122-128`、`:138-142` | 已读口径三处重复（`status=='unread'` + `func.now()`） | P2 | 未读口径单一常量 | 低（纯常量/语句提取） | 新增工具 | pytest test_notify::test_messages_api_list_read | 批次3 | 确认 |
| D06-8 | `backend/app/api/capsules.py:55-73`（+ 调用点 `:85`/`:136`） | 软删口径不一致 + 列表 N+1 | P2 | 与 `load_alive_content` 软删口径对齐；列表去 N+1 | 加 `deleted_at` 过滤会改变「内容已软删仍显示摘要」的现行为（需产品确认） | 行为等价替换（去 N+1 部分）/ 需拍板（软删部分） | pytest test_ba2_capsule::test_seal_list_open_full_flow + test_ab_scenarios::test_list_carries_content_brief | 批次3 | 确认 |
| D06-9 | `backend/app/services/echo.py:305-325` vs `backend/app/services/notify.py:257-266`、`backend/app/workers/capsule_scan.py:61-76` | 通知失败处理口径三处不一 | P2 | 统一「通知失败不阻断主流程」军规 | 加 try/except 会吞掉真实故障（须配 logger.error） | 行为等价替换 | pytest test_notify + test_ba2_capsule | 批次3 | 确认 |
| D06-10 | `backend/app/api/echo.py:42-52` + `backend/app/services/echo.py:328-338` | dismiss 幂等 TOCTOU（先 count 后无条件 insert） | P2 | 幂等真正原子 | 低（dismiss 行不占每日名额，非泄漏） | 行为等价替换 | pytest test_echo::test_echo_dismiss + test_data_chains_12 | 批次3 | 确认 |
| D06-11 | `backend/app/api/messages.py:122-128` | mark_read 由 1 次往返变 2 次（SELECT 结果未使用） | P2 | 省一次查询；消除「查了不用」 | 低；但回退到原「UPDATE 未命中→补查」须保留 404 语义 | 行为等价替换 | pytest test_notify::test_messages_api_list_read（含越权 404） | 批次3 | 确认 |
| D06-12 | `client/pages/messages/messages.uvue:155-177` + `client/components/MessageDetailSheet/MessageDetailSheet.uvue:57-71` | 客户端 msg_type→展示映射两套 | P2 | 新增 msg_type 只改一处 | 低（UTS 侧禁 type 字面量，映射须用 class/常量） | 新增工具 | HBuilderX 冷编译 + 真机 messages 页目视（无自动化） | 批次3 | 确认 |
| D06-13 | `backend/app/workers/capsule_scan.py:35`、`:83-89` | 模块级节流状态跨进程失效 | P2 | 节流语义明确（或改注释为「每进程」） | 低（去重靠原子 UPDATE 兜底，非泄漏） | 行为等价替换 | pytest test_ba2_capsule::test_lazy_scan_on_list（复位 `_last_scan_ts`） | 批次3 | 确认 |

**分布**：P0 = 0 ｜ P1 = 6（D06-1~D06-6）｜ P2 = 7（D06-7~D06-13）｜ 合计 13 条。
**状态**：确认 12 ｜ 候审 1（D06-5）。

### 证据

**D06-1 证据**：三函数逐字实读，查询形状与 404 语义见上表；同一语义三实现，命名/层/可见性/软删过滤全不一致。消费点由 `Select-String "load_alive_content|_load_owned_capsule|load_owned_message"` 全仓列出（capsules 2 + contents 8 + messages 1）。

**D06-2 证据**：`test_authz_gate.py:43-52` 的 `OWNERSHIP_LOADERS` 含 `"load_owned_capsule"`（无下划线）；实际符号 `capsules.py:45 def _load_owned_capsule(...)`（有下划线）。`_ownership_evidence`（L136-154）以 `fn.id in OWNERSHIP_LOADERS` 精确匹配 → `_load_owned_capsule` 不命中；capsules 端点实际靠 `capsules.py:156/182` 传入 `user.id`（属性访问 → 证据 `"user.id"`，L143-149）通过门禁。`test_whitelist_and_exempt_entries_still_exist`（L200-212）**只校验 CREATION_WHITELIST 与 EXEMPT**，不校验 `OWNERSHIP_LOADERS` → 该哑条目永不被发现。

**D06-3 证据**：`Select-String "_is_sensitive\("` 全 `backend/` 仅 1 命中 = 定义行 `services/echo.py:152`（docstring 提及 L160 非调用）。`get_today_echo` 在 L239-254 内联同语义检查（`_profile_hit` + `sensitive_status` + `moderate`）而不调用 `_is_sensitive` → 零调用死码 + 双实现。

**D06-4 证据**：`capsules.py:157-161`（`open_at > now` → 409 + `remaining_days`）；`capsule_scan.py:56`（`Capsule.open_at <= current` → 置 due）；`CapsuleSheet.uvue:138`（`dueMs <= Date.now()` 本地先校验）。三处各自编码「到期」语义，且 `capsules.py:158` 有裸时间按 UTC 兜底而客户端 `openAtMs`（`CapsuleSheet.uvue:89-92`）用 `Date.UTC(本地字段)`（自带 ≤8h 偏差，注释已承认）。

**D06-5 证据**：
- 客户端零调用列表端点：`Select-String "capsule"` 全 `client/` 命中里，`capsule_api.uts:23` 定义 `PATH_CAPSULES`，**唯一使用处**为 `capsule_api.uts:40`（`post(PATH_CAPSULES, body)` = sealCapsule）；无任何 `get(PATH_CAPSULES)`/`/capsules/{id}/open`/`/capsules/scan` 调用 → `GET /api/v1/capsules`（惰性扫描唯一前端触发点，`capsules.py:126`）在客户端不可达。
- 仓库内定时登记：`deploy/RUNBOOK.md:151-161` §5「定时任务（系统 cron，不进代码调度）」仅 3 行——每日 22:00 `daily_review.py`、每日 03:30 备份、每周 orphan_scan；**无 capsule scan 登记**。`capsule_scan.py:10-17` 模块 docstring 自述「未挂 GET /messages…后续批次若引入 APScheduler/部署侧 cron，可把 scan_due_capsules 直接登记」= 未闭环。
- 候审原因：生产机 `crontab -l` 在仓库外，无法静态证实是否另有登记（`RUNBOOK.md` 仅 grep 命中，未逐行通读全文）。

**D06-6 证据**：`services/echo.py:30-31`（`PROFILE_BLOCK_DISPOSITIONS`/`PROFILE_DISPOSITIONS`）、`:73`（`upsert_profile_sensitive`）、`:125`（`delete_profile_sensitive`）、`:139`（`list_profile_sensitive`）全住 echo 服务；消费方是 `api/contents.py:89`（`profile_sensitive_router = make_router(prefix="/api/v1/profile")`）与 `:473/491/508`（三端点函数内 `from app.services.echo import ...`）；`main.py:120` 注册该 router。echo 模块 docstring L10-11 自认该混装（「本模块同时提供 profile_sensitive 表的服务函数」）。

**D06-7 证据**：`messages.py:100-104`（`unread_count` 用 `Message.status == "unread"`）、`:122-128`（`mark_read` UPDATE `status=="unread"` → `read` + `func.now()`）、`:138-142`（`mark_all_read` 同款 UPDATE 无 id 条件）。三处硬编码同一状态字面量与时间函数。

**D06-8 证据**：`capsules.py:57` `select(Content).where(Content.id == content_id)` —— **无 `Content.user_id`、无 `Content.deleted_at.is_(None)`**；对照 `deps.py:93-99` 的 `load_alive_content` 三条件。N+1：`list_capsules`（`capsules.py:127-136`）对每行调 `_capsule_out`（`:76-86`）→ 每行一次 `_content_brief`（`:85`）= 每胶囊 1 次 Content SELECT（+ 可选签票）。
（IDOR 判定：无泄漏——`cap.content_id` 在封存时经 `load_alive_content` 归属校验（`capsules.py:103`），且 `Content.user_id` 不可变；本条仅指软删口径不一致 + N+1。）

**D06-9 证据**：`echo.py:305-325`（`_create_echo_message`：`try/except Exception` → `db.rollback()` + `logger.error`）；`pipeline_ext/emotion.py:111-123`（两处 `try/except` + `logger.exception`）；**裸调无保护**：`notify.py:257-266`（`generate_daily_review` 直接调 `create_message` 后 `db.commit()`）、`capsule_scan.py:61-76`（循环调 `create_message`，任一抛错则整批回滚）。`notify.py:47-49` 端口 Protocol 仅在 docstring 要求「实现方…失败记日志不抛」= 契约约定，无强制。

**D06-10 证据**：`api/echo.py:42-51` 先 `select(func.count())` 查 `already`，为 0 才调 `dismiss_echo`；`services/echo.py:328-338` 内**无条件** `db.add(EchoHistory(action="dismiss"))` + `commit()`。两请求交错时均读到 `already==0` → 落 2 行 dismiss（`uq_echo_history_daily` 的 `postgresql_where` 排除 dismiss，`models/echo.py:23-31`）→ 不占每日名额、不泄漏，仅幂等不严格。

**D06-11 证据**：`messages.py:122` `load_owned_message(...)`（SELECT，返回值被丢弃）紧跟 `:123-127` 的 `update(...)`（第二次往返）。改造前（注释 `:116-120` 自述）为「UPDATE 未命中 → 补查存在性」= 常态 1 次往返。

**D06-12 证据**：`messages.uvue:155-158`（`msgSegment`）、`:159-167`（`msgIcon`）、`:168-177`（`msgBg`）；`MessageDetailSheet.uvue:57-71`（`typeLabel`）。两文件各自维护 msg_type 映射；`capsule_due`（后端 `capsule_scan.py:65` 产）在两处均无分支 → 客户端落「系统通知」兜底（`MessageDetailSheet.uvue:70`），`msgIcon` 落 `msg-bell.svg`（`messages.uvue:166`）。

**D06-13 证据**：`capsule_scan.py:35` `_last_scan_ts: dict[str, float] = {"ts": 0.0}`（模块级全局）+ `:83-89`（`maybe_scan_due` 读/写该全局）。多 worker（`deploy/systemd/yishu-worker.service` 多进程形态）下每进程独立计时 → 节流跨进程失效；重复消息由 `:54-59` 原子条件 UPDATE 领取兜底（`capsule_scan.py:44-48` docstring 自述 P2-3 修复）。

---

## 依赖方向与抽象覆盖度结论

### FF1：本域是否存在反向依赖？

**存在，共 4 处（均为函数内延迟 import，非模块顶层环）**：

| # | 位置 | 方向 | 判定 |
|---|---|---|---|
| 1 | `backend/app/api/capsules.py:33` `from app.workers.capsule_scan import maybe_scan_due, scan_due_capsules` | **api → workers**（API 层直接依赖定时/worker 层） | 反向（越层）；`list_capsules` 在请求路径内同步执行全表扫描（`:126`） |
| 2 | `backend/app/api/echo.py:16` `from app.services.echo import _fingerprint, ...` | api → services（**消费下划线私有符号**） | 跨层使用私有符号（下划线私有被 api 层引用） |
| 3 | `backend/app/services/echo.py:306`（`_create_echo_message` 内）`from app.services.notify import create_message` | **services → services**（同层跨域） | 服务层互依；且 `:317` 自行 `db.commit()`，绕过调用方事务边界 |
| 4 | `backend/app/services/echo.py:172`、`:251`（函数内）`from app.services.llm_ops.base import moderate` | services → services（跨域，LLM 域） | 回响服务内联 LLM 护栏调用（可接受，但耦合点在服务层深处） |

正向依赖（无需改）：`workers/capsule_scan.py:29` → `services/notify`；`services/notify.py:31` → `db.models`；`api/*` → `schemas`/`core.errors`。

**职责边界结论（判据4）**：
- `services/echo.py` 是**双职责**模块：回响（`get_today_echo`/`dismiss_echo`/`_fingerprint`/`_local_now`）+ 画像敏感 CRUD（`:30-149`）。后者被 `api/contents.py` 的 `/profile/sensitive` 消费 → 见 D06-6。
- `services/notify.py` 是**三职责**模块：推送端口抽象（`:36-73`）+ 消息入库（`create_message` `:178-216`）+ 业务文案编排（`generate_daily_review` `:242`、`notify_voice_done` `:270`、`maybe_send_emotion_care` `:296`，含 `CARE_TEMPLATES` 文案库 `:93-118` 与 `_SAD_REASON_MARKERS` 启发式 `:121-124`）。
- `workers/capsule_scan.py` 越界持有**业务消息文案**（`capsule_scan.py:66-73` 标题/正文/`msg_type=capsule_due` 硬编码），与 `notify.py` 的文案集中地（`notify.py:9-19` docstring 声称文案统一）不一致。

### 新增一种通知渠道（如短信/推送）需改哪几处？全部注册点

**A. 站内/推送渠道（本域 `notify` 端口）——共 6 处**
1. `backend/app/services/notify.py:212` — `create_message` 的渠道分发硬编码 `if channel == "push":`（新增渠道名须加分支）
2. `backend/app/services/notify.py:44-49` — `PushChannel` Protocol（渠道接口）
3. `backend/app/services/notify.py:52-56` — `MockPushChannel`（默认实现）
4. `backend/app/services/notify.py:59-73` — `_push_channel_ref` 单槽全局 + `get_push_channel()`/`set_push_channel()`（**单例单槽，无「按 channel 名注册」表** → 多渠道并存须改这 4 处结构）
5. `backend/app/db/models/message.py:23` — `channel` 列注释枚举 `in_app / push`（无 DB 约束，仅注释；新渠道需改注释与 `String` 约定）
6. `backend/app/schemas/message.py:10` — `MessageOut.channel: str`（宽松 str，无枚举，无需改但缺校验）

**B. 短信渠道（auth 域平行端口，非本域 `notify`）——共 3 处**
7. `backend/app/services/auth/providers.py:49-61` — `SmsSender` ABC 端口
8. `backend/app/services/auth/providers.py:63-88` — `MockSmsSender` / `AliyunSmsSender` / `get_sms_sender()` 配置分发
9. `backend/app/services/auth/auth.py:95-96` — 生产环境门控（`app_env == "production"` → 501 `ERR_AUTH_010`）

**C. 客户端展示映射——共 2 处**
10. `client/pages/messages/messages.uvue:155-177` — `msgSegment`/`msgIcon`/`msgBg`（按 `msg_type`，未按 channel）
11. `client/components/MessageDetailSheet/MessageDetailSheet.uvue:57-71` — `typeLabel`（按 `msg_type`）

**D. 定时编排登记点（渠道落地必需的调度面）——共 3 处**
12. `deploy/RUNBOOK.md:151-161` — §5 定时任务表（人工 cron 登记处）
13. `backend/scripts/daily_review.py:53` — 每用户调用点（复盘 push 唯一编排入口）
14. `backend/app/workers/capsule_scan.py:83-89` — `maybe_scan_due`（**当前无任何 cron 登记**，见 D06-5）

**3 处真实 TODO 涉及短信/推送通道（核实结果：3 文件 / 4 行）**

| # | 位置 | 内容 | 与本域关系 |
|---|---|---|---|
| 1 | `backend/app/services/notify.py:5` | `配置后零切换（channel == push 时走真实厂商，见 TODO(T1)）` | **本域**（推送厂商未接入，仅 mock 日志） |
| 2 | `backend/app/services/auth/providers.py:11` | `AliyunSmsSender —— 真实通道占位（TODO T1 接入前 501…）` | 邻域（auth 短信端口，平行于本域 push 端口） |
| 3 | `backend/app/services/auth/providers.py:73` | `"""真实短信通道占位：TODO(T1) 接入阿里云短信（0.045 元/条）` | 同上（同文件第二处） |
| 4 | `backend/app/services/auth/auth.py:92` | `与 wechat_login 的"生产未接入 → 501"对齐；真实短信通道（TODO T1）接入前` | 同上（生产门控注释） |

> 说明：全仓 `TODO` grep 命中中，**真正指向「短信/推送通道未接入」的仅上述 4 行（3 文件）**；其余 `TODO` 命中均为分类标签字面量 `todo`（`classifier.py`、`schemas/correction.py` 等）与 `review_agent.py` 的 TODO 计数器，非真实待办。故「3 处真实 TODO」口径应表述为 **3 文件**（`notify.py`、`auth/providers.py`、`auth/auth.py`）。

### 判据6 · 异常 / 事务 / 软删口径一致性

- **通知发送失败是否影响主事务**：本域 3 类调用点口径不一——`_create_echo_message`（`echo.py:305-325`）**保护**（try/except+rollback+error 日志）；管线接线（`pipeline_ext/emotion.py:111-123`）**保护**；`generate_daily_review`（`notify.py:257-266`）与 `scan_due_capsules`（`capsule_scan.py:61-76`）**裸调无保护** → 真实推送通道若抛错（Protocol docstring 仅约定「不抛」，未强制）会回滚整批复盘/整批到期消息（见 D06-9）。当前默认 `MockPushChannel`（`notify.py:52-56`）只记日志不抛，故**现状无事故**，属潜在债。
- **回响幂等（同一"去年今日"是否可能重复发）**：**不会**。三重兜底：① 当日 `shown_today >= ECHO_DAILY_LIMIT` 早返回（`echo.py:192-202`）；② 部分唯一索引 `uq_echo_history_daily`（`models/echo.py:23-31`，`WHERE action IS DISTINCT FROM 'dismiss'`）；③ 插入冲突 `IntegrityError` → `rollback()` → 返回 None（`echo.py:260-273`）。消息侧同样「只在到达新展示分支才建发」（`echo.py:274-286`，注释自述天然 ≤1 条/回响）。测试覆盖 `test_echo.py:106-120`（并发双插必 IntegrityError）与 `:80-93`（同日第二次不重复建消息）。
- **胶囊到期消息幂等**：**成立**。原子条件 UPDATE 领取（`capsule_scan.py:54-59`，`WHERE status='sealed' AND open_at<=now RETURNING`）+ status 流转（sealed→due）；二次扫描 `produced=0`（`test_ba2_capsule.py:224-236` 断言）。
- **软删 30 天口径**：`capsules` / `messages` / `echo_history` **三表均无 `deleted_at` 列**（`models/capsule.py:23-37`、`models/message.py:11-33`、`models/echo.py:12-39` 逐列确认）→ 「软删 30 天」对本域**不适用**。相关行为：`delete_capsule`（`capsules.py:185`）为**硬删**（撤销封存语义，模块 docstring `capsules.py:181` 与 `models/capsule.py:30` 明示「不加 FK——软删生态零侵入」），被关联 content 不受影响（`test_ba2_capsule.py:328-330` 断言）。唯一口径瑕疵 = `_content_brief` 未过滤 `Content.deleted_at`（见 D06-8）。

---

## 本域已查无问题项（避免遗漏被误读）

1. **归属 404 不泄露存在性（三 loader 一致）**：`capsules.py:46/51`、`deps.py:85/101`、`messages.py:38/49` 均统一 404、不区分「不存在 vs 非本人」；越权读他人消息 → 404 有测试（`test_notify.py:189-194`）。
2. **messages 分页/游标**：共享 `pagination_params`（`deps.py:30-39`），limit 由 Query 校验 1..100，游标非法 → 422 `ERR_MSG_003`（`messages.py:71-76`）；非 UUID 无关（messages id 为 BigInteger）。
3. **`create_message` 事务边界**：只 `db.flush([msg])` 不 commit（`notify.py:209-210`），落库由最外层编排者统一 commit；有专门测试 `test_notify.py:74-100`（独立会话 commit 前不可见）。
4. **回响敏感双查 fail-safe**：LLM 护栏不可用/未过 → 拒发（`echo.py:171-177` 与 `:250-254`，`moderate` 返回 `pass=False` 即跳过）；测试 `test_echo.py:192-198`（未标记敏感但命中规则词 → None）。
5. **回响画像 L1 校验**：`profile_sensitive` 命中 `forbid/caution/review` → 跳过（`echo.py:30/68`）；`allow/mention` 放行；测试 `test_echo.py:208-231` 三态覆盖。
6. **`upsert_profile_sensitive` 竞态**：改 `ON CONFLICT DO UPDATE` 原子 upsert（`echo.py:97-117`）+ `db.expire_all()` 破 identity-map 缓存；测试 `test_echo.py:256-287`（双线程并发仅 1 行）。
7. **回响本地日界**：`_local_now()`（`echo.py:38-41`）+ `shown_date` 显式落列（`models/echo.py:17-19` 说明 timestamptz::date 非 IMMUTABLE 不能建索引）；测试 `test_echo.py:123-172`（含闰年 2/29 退化 `echo.py:184-188`）。
8. **回响候选查询走索引 + 无 N+1**：本地日期范围查询（`echo.py:206-218`，注释自述替代原 `func.extract`）+ 划掉指纹一次 IN（`:221-228`）+ 画像行一次加载（`:231-233`）。
9. **messages `content_id` 列防污染**：`_coerce_content_id`（`notify.py:162-175`）非 UUID 置 None，不让消息建发因关联字段失败；测试 `test_ba1_fields.py:144-165`。
10. **胶囊 open 幂等**：已 opened 直接返回现态（`capsules.py:168-171`）；测试 `test_ba2_capsule.py:156-159`。
11. **`Capsule.status` 有索引**（`models/capsule.py:37` `index=True`）→ 扫描 `WHERE status='sealed'` 走索引。
12. **客户端 UTS 边界合规**：EchoSheet/CapsuleSheet/MessageDetailSheet 均只收基本类型 props（`EchoSheet.uvue:39-54`、`CapsuleSheet.uvue:61-71`、`MessageDetailSheet.uvue:27-53`），符合项目 UTS 组件边界先例。
