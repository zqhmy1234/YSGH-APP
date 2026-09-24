# Tasks

> 波次 = 易扩展/易维护重构波。**性质：严格行为等价**（不改运行时行为）。**范围：`backend/app/` + `client/` + `scripts/` + `deploy/`；`agent/` 排除**。
> 节奏：Phase A 只读筛查 → 台账 → **用户拍板优先级** → Phase B 分批执行。

## Phase A — 逐功能域静态筛查（只读，不写业务代码）

> **2026-09-24 重启执行（抗断连策略）**：改为**串行小批量**（每批 2 域、每域 1 个子 agent、各写独立报告文件、失败即续跑），**14 域全部完成**（上一轮 7 并行 subagent 中途全部挂掉、且只做了浅层统计）。
> 产出：`audit/domain-01..14-*.md` + 统一汇总 `audit/_PHASEA_ROLLUP.md`（**249 条 = 结构类 185 + 功能与安全缺陷 64**；P0 **17** / P1 111 / P2 121；候审 30）。
> **结论已并入 `refactor-ledger.md` §9（覆盖 §2–§4 的浅层条目）**；旧条目修正 4 处见 §9.3。

- [x] Task A1: 建立台账骨架与筛查 rubric：定义条目字段（位置/类型/严重度/收益/风险/可回滚性/验证手段/批次归属）、类型枚举（巨文件·重复·死码·边界·声明漂移·门禁缺口）、严重度口径（对齐 `dev-code-review` P0/P1/P2）；产出台账文件骨架。
  - [x] A1.1 明确"候审(待验) vs 确认"标注规则（证据不足不得写成结论）
  - [x] A1.2 明确"与既有登记去重对账"清单（`决策台账 §7` / `待办处理排期_20260923` / `后端重构与性能优化计划_20260910` / `审计_未关闭缺陷_20260923`）

- [x] Task A2: 审计后端域（`backend/app/`：`api` + `services` + `core` + `db` + `workers`）：逐文件查巨文件/重复/死码/边界/依赖方向；已知候选须复核（`api/contents.py` 902 行、`api/events.py` 530 行）。
  - [x] A2.1 api 层：资源子路由职责与体量
  - [x] A2.2 services 层：重复逻辑（归属校验/软删过滤/LLM 调用样板）、注册表/端口抽象覆盖度
  - [x] A2.3 core/db/workers：配置、池、任务、错误码登记一致性

- [x] Task A3: 审计客户端域（`client/utils` + `components` + `pages` + `uni_modules`）：查巨文件/重复/死码/声明漂移（已知候选：`TabSearch.uvue` 763、`sync_client.uts` 705、`uploader.uts` 628；`shell.uvue:7`、`search_api.uts:183`）。
  - [x] A3.1 utils：重复请求/重试/错误处理样板、`.uts` 类型重复定义
  - [x] A3.2 components/pages：单文件模板+逻辑体量、Vapor/组合式一致性
  - [x] A3.3 声明漂移与死路径复核（配合 `audit_harness client`）

- [x] Task A4: 审计脚本/部署域（`scripts/` + `deploy/`）：覆盖度、幂等性、硬编码、可复跑性；标注"低覆盖/无测试"项。

- [x] Task A5: 审计 harness 工具域（`scripts/{audit_harness,review_agent,test_agent,lessons,check_schema_drift}.py`）：查门禁覆盖盲区、假阳性风险、可扩展点；产出"建议新增棘轮轴"清单。

- [x] Task A6: 台账合并、去重、对账与排序：按 收益×风险×可回滚性 折为 P0/P1/P2 并给批次归属建议；对已登记项标注来源不重复立项。

- [x] Task A7: 提交台账给用户拍板（`NotifyUser`）——**已拍板（2026-09-24）**：ORM 为权威 / 64 条功能缺陷另开功能波 / B5 不再顺延；另拍板 L-12 另立令牌波。见 `docs/决策台账.md` §5.10 + ledger §9.7 C/D。

## Phase B — 分批执行（严格行为等价，逐批过门禁）

> 以下为**已确认的种子批次**；A6/A7 后如台账新增高价值项，在此追加任务（保持"批内不混功能波"）。

- [x] Task B1: harness 棘轮落地（**先棘轮后清账**，防新债）：扩展 `scripts/audit_harness.py` 新增结构轴——文件体积轴、重复模式轴、契约路径"只增不减"断言、声明漂移扫描；**每轴先用已知样本自校验**，再以基线白名单冻结存量（只拦新增）。
  - [x] B1.1 文件体积轴（阈值 services/api ≤600 行、`.uvue`/`.uts` ≤800 行）
  - [x] B1.2 契约路径只增不减断言（落实 `决策台账 §2.14` 建议）——**勘察后判定：轴 1 双向对拍已覆盖该意图**（后端有路由而契约缺 / 契约有而后端无 均 CRITICAL），不重复实现
  - [x] B1.3 声明漂移扫描（死路径/失实 mock 宣称）

- [x] Task B2: 后端巨文件拆分（测试护航，纯移动+兼容再导出）：`backend/app/api/contents.py`、`backend/app/api/events.py`。
  - ✅ **2026-09-24 第二轮已执行 `contents.py` 拆分**（不再顺延）：`git mv` → `backend/app/api/contents/__init__.py`，移出 5 子模块（`serializers` 48 / `profile_sensitive` 89 / `favorites` 111 / `trash` 115 / `waveform` 115），`__init__.py` **902 → 524 行**（<600 ⇒ 轴 5 棘轮销项，存量超阈 7→6）。
  - [x] B2.1 `contents.py` 拆分（沿用子包先例，保持 import 兼容 + monkeypatch 面不动）
  - [ ] B2.2 `events.py` 拆分 —— **未拆**：530 行 < 600 阈值，非棘轮项、优先级低（仍登记）
  - [x] B2.3 受影响测试绿 + 全量 pytest 不红 + `audit_harness openapi` 无消失 —— 证据：受影响 **89 passed**；全量 `pytest backend/tests` → **850 passed / 4 skipped**；`openapi` **67/67 完全对齐、无幽灵路径**
  - [x] B2.4 **门禁覆盖回归修复（B2′）**：`test_authz_gate.py` `glob("*.py")` → `rglob("*.py")`（含子包）+ 新增 `test_scan_covers_subpackages` —— 证据：候选 20+ → **29**，已扫到 `contents/{__init__,favorites,trash,waveform}.py`

- [x] Task B3′: 归属 helper 收敛 + 门禁哑条目修正（L-09 / D06-1 / D06-2）——`deps.load_owned_entity` 泛化，三 loader 委托，错误码/文案/HTTP 逐字保留；`OWNERSHIP_LOADERS` 修正为真实符号 `_load_owned_capsule`、删 3 个幻影预留项；新增 `test_ownership_loaders_all_exist`。证据：自校验 `load_owned_capsule in live → False`；`pytest test_authz_gate+contents+content_upload+echo` → **56 passed**

- [x] Task B10: 门禁加固（D14-1 / D14-2 / D14-3 / D14-5）——`review_agent` 新增 `audit_axes`（轴 1–4，快/全量均跑）；CI full-gate 新增阻断式 `audit_harness all`；重复模式改总数+per_file 双 gate；镜像豁免收窄（docstring 含"镜像" ∧ 类自身有 `__tablename__`）；体积阈值改由基线驱动。证据：负向探针 → `exports` CRITICAL + `review_agent` 退出码 1，已还原

- [x] Task B10-b: 门禁缺口补强——**D11-2 ✅**（错误码 AST 跨模块盲区，旧实现 `capsules.py→[]` / 新实现 `['CAPSULE_001'..'004']`）/ **D11-1 ✅**（补登记 4 枚）/ **D12-9 ✅**（pages.json→物理文件反向对拍）；**D04-15 ⏸ 阻塞**（被功能波 D04-1/2/3/4/5 端云对齐阻塞——补夹具必致双跑门禁变红）。见 ledger §8.3

- [x] Task B12: 脚本/部署可移植性（**部分**）——D13-1 恒绿假门禁退役（CI client tsc 步骤 → 阻断式 `audit_harness client`）；D13-26 deploy/README 补齐；硬编码路径清 4 处（`smoke_cos_upload` / `backup_pg.ps1` / `reinject_missing_photos` / `seed_echo_today` 改 env+显式默认+缺失报错）。**D13-3 只登记**（实跑 `check_env_template.py` 当前即失败：`AGENT_SERVICE_*` config 有模板缺 → 先补模板再挂门禁）

- [x] Task B11: 声明漂移清零（**安全子集**）——D04-17（类型标注）、D13-28（docstring 对齐）、D13-14（注释与代码一致）、D14-2（体积阈值死配置，随 B10 修复）。残余 D02-7/9、D04-3、D05-9/15、D07-13/14、D09-10、D11-5、D12-7 待处置（需决策/属死配置删除，宜单独立项）

- [x] Task B10-c: 错误码**反向棘轮**（D11-5：registered-but-never-raised）——`test_error_registry.py` 新增 `test_no_new_dead_error_codes`（**双向**：新增死登记 → 红；基线项已销项未移除 → 也红）+ `DEAD_CODE_BASELINE`（3 枚带原因/销项触发）+ 自校验。证据：实测 `registered − raised = 恰好 3 枚`（67/64）；内存级负向探针（模拟 `ZZZ_PROBE_999`）→ 判为基线外新增 ⇒ 非空转。**刻意不删这 3 枚**（删除前需确认无外部消费方）

- [x] Task B11-x: **D07-13 改判**——实读 `validate()` 仅测试调用（生产 `get_schema()` 不调它）⇒ 属测试期契约断言（51 L0 / 193 L1 即规格契约），**非缺陷**，保留不修（理由入 ledger §9.3 第 5 条）

- [x] Task B10-d: 轴 4 `EXPORT_RE` **假阴性**修正 + 零调用能力导出棘轮——`^\s*export` 的 `\s` 含换行 ⇒ 匹配点落前导空行 ⇒ 声明行排除失配 ⇒ 每个导出把自身计成 1 引用 ⇒ **轴 4 长期假绿**（`zero_cap` 应为 4 却恒为 0；298 导出中 56 个声明行偏移）。修为 `^[ \t]*`；露出 4 枚真零调用能力导出（`AuthError`/`getRecorder`/`lastTempFile`/`stopPeriodicSync`），按 B1 口径基线冻结 + 只拦新增 + 清算僵尸豁免。证据：双负向探针（新增导出 → CRITICAL 且**行号正确**；基线塞假条目 → 僵尸豁免 CRITICAL）均已还原；`all` 无 CRITICAL

- [x] Task B10-e: filesize / dup 基线**僵尸豁免清算**（D14-4 的另一半）——原白名单只拦新增、从不检查条目是否失效（文件被删/改名/拆到阈值内 ⇒ 永久白条）。现：filesize 对 `set(allowlist) − 仍超阈` 报 CRITICAL；dup 对 `per_file` 已归零条目报 CRITICAL；存量下降报 WARN/INFO 要求同步下调基线。证据：先逐条对账（6 条 filesize / 21 条 dup per_file 全 `OK`、total 44=44）⇒ 不引入误红；双负向探针（client 阈值 800→3000 ⇒ **6 条**僵尸 CRITICAL；dup 塞不存在文件 ⇒ **1 条** CRITICAL）均 EXIT=1 且基线按原文精确还原

- [x] Task B10-f: 轴 4 **覆盖面补全**（D14-16）——① `audit_exports` 原 `glob("*.uts")` **非递归** ⇒ `client/utils/agg/**` 等子目录整体漏扫；② `EXPORT_RE` 只认 `function|const|let|class` ⇒ `export type/interface/enum/default` 漏扫。修为 `rglob("*.uts")` + 正则补 `(?:default\s+)?` 与 `type|interface|enum`。证据：先量化（补递归 +29 导出、其中零调用 1 个＝`WALK_SPEED_MS`＝深审 D04-12；补 7 类 +5 导出、零调用 0）；修后导出 **298 → 332**、存量 5、无新增；子目录负向探针（追加零调用导出）→ CRITICAL 且**行号正确**，已还原；`all` 无 CRITICAL

- [x] Task B10-g: 契约快照**再生脚本 + 一致性断言**（D14-7）——新增 `scripts/gen_openapi.py`（写入 / `--check`），接入 `review_agent` 的 `openapi_snapshot` 检查（0=一致放行 / 1=过期阻断 / 2=环境不可导入降级放行）。证据：`--check` 证明现有快照与后端**逐字节一致**（239518 字符，此前无任何机器能证）；负向探针（塞幽灵路径）→ `[FAIL]` + 精确定位「快照有而后端无（1）」EXIT=1、`review_agent` 阻断；再生还原后与 HEAD 逐字节一致

- [x] Task B10-h: 门禁**静默转绿** + **覆盖率双阈值**（D14-19 / D14-8）——① 缺 ruff（`code==127`）/ 缺 pytest 此前**静默返回 True + [skip]**（报告全绿而 lint/测试从未执行）→ 改**默认阻断**，仅 `--allow-missing-tools` 时可见放宽（带 `[放宽]` 标记）；② 覆盖率阈值收敛为单一常量 `COV_THRESHOLD = 60`（此前本工具 50 vs CI 60 ⇒ 本地绿≠CI 绿）。证据：内存级探针（monkeypatch `run`、不改文件）证明 argv 阈值=60、缺工具默认 `ok=False`、放宽路径 ok=True。**✅ 环境验证已解除（2026-09-25）**：Docker/Qdrant 起后 `review_agent --full` EXIT 0，覆盖率 **84.59% ≥ 60**（阈值达标）、api_smoke ✅、research ✅（18 场景）

- [x] Task B10-i: 门禁域剩余项——**全部收口**（D14-18 退出码 0/1/2 统一、D14-14/15 轴 2 字符级注释判定、D14-9/11/12/13 单一来源化、D14-22 报告 schema、D14-23 去 `continue-on-error`、D14-6 判定已被 B10-g 覆盖）。见下方同项与 ledger §8.3/§8.4

- [x] Task B10-j: 轴 2 **注释判定**假阳性/假阴性（D14-14 / D14-15）——原用「本行前缀有无 `//`/`<!--`」的行级近似 ⇒ ① 块注释内页面引用被当**死路由**（误报 CRITICAL）② 多行 HTML 注释同样漏判 ③ `.ts` 残留不看注释（注释里写「原 './auth.ts'」也报 CRITICAL）④ **反向**：字符串 `http://x` 之后被判为注释 ⇒ **真死路由漏报**。改为字符级 `_comment_ranges()`（跳过字符串字面量）+ `_in_comment(offset)` 精确判定。证据：纯函数对照六例（块注释/多行 HTML/块注释 `.ts`：旧 False→新 True；`http://`：旧 True→新 False；真死路由两侧一致）；文件级负向探针（追加真实死路由）→ CRITICAL @ `shell_state.uts:25`、EXIT=1，已还原；轴 2 常规态无 CRITICAL

- [x] Task B10-k: 退出码口径**统一**（D14-18）——测绘发现 `audit_harness`/`check_schema_drift`/`gen_openapi` 用 2=环境错误，而 `review_agent`/`test_agent` 只有 0/1 ⇒ 环境问题被误报为「代码违规」。`review_agent` 落地 **0 通过 / 1 违规 / 2 环境错误**：`ENV_ERR_PREFIX` 打标 + 纯函数 `_classify_failure()` 分流（违规优先）+ 报告加 `env_blocked_checks`。证据：纯函数探针 5 例全对；env 分支消息均以 `[环境错误]` 开头；CLI 实跑退出码 0。残余（已登记）：`test_agent` 仍只产 0/1（仅加 docstring 交叉引用）

- [x] Task B10-l: 密钥模式集**唯一来源**（D14-9）——新建 `scripts/secret_patterns.py`（并集 16 条），`review_agent`/`audit_security` 共用；配套 `pragma: allowlist secret` 显式行内豁免（刻意不用"整目录跳过 tests/"，避免放过真密钥）。证据：探针证明两处同源、**并集无缺失（[]）**、四类形态（腾讯云 AKID/DashScope 宽版/私钥宽版/GitHub PAT）均被提交门禁识别；放宽后暴露 `backend/tests` 4 处合成值误报 → 逐行标注；复跑静态全绿、受影响测试 9 passed

- [x] Task B10-m: `audit_security` **崩溃 + 白名单失效**（既有缺陷）——① `db/models` 已拆包仍直读 `models.py` ⇒ `FileNotFoundError`（HEAD 版本同样崩）⇒ 该安全审计**从未跑通**；② `_ALLOW_PATHS` 用仓库根相对前缀 ⇒ `backend/tests/…` 不匹配、"排除测试"意图从未生效；③ 统一合成值抑制（`change-me`/`mock`/`allowlist secret`）。证据：修复后**首次真正跑通**，`blocking` = **1**，仅剩真实项 **最近备份距今 493.0h（≈20.5 天）违反 RPO≤24h**（交运维/用户）

- [x] Task B10-n: UTF-8 兜底**同语义 20 处 → 单一实现**（D14-11）——新增 `scripts/gate_io.py::force_utf8()`（语义为各原处安全超集），**16 个文件 / 20 处**全迁移（含 6 种形态：成对守卫 / 仅 stdout / 函数内 / `__import__` 变体 / `for _stream`+suppress / 无守卫裸调用）。证据：`scripts/` 全目录 ruff `All checks passed`；17 个改动脚本 `py_compile` 全过；残留 `reconfigure` **仅剩 `gate_io.py` 自身**；门禁工具实跑全绿（`review_agent` ✅ / `audit_harness all` 无 CRITICAL / `lessons recent` 正常）

- [x] Task B10-o: 退出码口径**单一来源** + `test_agent` **姊妹假绿**（D14-18 残余 / D14-19 姊妹 / D14-13 半）——① 新增 `scripts/gate_exit.py`（`ENV_ERR_PREFIX` + `classify_failure()`），`review_agent`/`test_agent` 共用；② 修 `test_agent` 缺 pytest 依赖时 `return True, "[skip] 缺依赖"`（**报告全绿而全量测试从未执行**）→ 改为环境错误（退出码 2）默认阻断；③ `test_agent` 加 `env_blocked_sections` + 0/1/2 三态；④ `audit_harness` 排除集 `CLIENT_EXCLUDE_DIRS` 合并硬编码 2 处。证据：探针 4 例全对 + 源码断言无旧 `[skip] 缺依赖`；`scripts/` 全目录 ruff 通过；三工具实跑正常

- [x] Task B9: 端口/边界收口（**已完成，三项子批**）
  - [x] B9a 事件域门面收口（D04-16）：`l3_lifecycle` 由 `events/timeline.py` 再导出，API 只依赖 `app.services.events` 门面（API→算法包直连清零）。证据：`44a837c`；ruff 通过；`pytest test_event_sync + test_agg_reference` → **23 passed**
  - [x] B9b rag 的 LLM 调用走 `llm_ops` 门面（D05-13）：`rewrite_query` 改从 `llm_ops` 导入；新增 `llm_ops.image_caption` 转发并替换 rag 两处直连。证据：`32b4a89`；ruff 通过；5 个相关测试文件 → **79 passed**；rag 对 `external.dashscope` 直连清零
  - [x] B9c 存储注册表 + COS 唯一构造点（D02-3/D02-13）：`BackendSpec` 注册项（新增后端**只改 1 行**）+ 三单例全局收敛为一个字典 + `build_cos_raw_client()` 唯一构造点 + `cos_sts_configured()` 归位存储层（API 不再直读 COS 私有字段）。证据：`8286327`；ruff 通过；**探针 8 项断言全过**；4 个测试文件 → **83 passed**
  - [x] **D05-16 改判**：`correction._get_store()` 已用共享 `get_qdrant_client()`（P2-04）⇒「自建第二套 Qdrant」表述已过时；残余"裸调用未走 `VectorStore` 门面"是无收益间接层，且会**变更 collection schema**（行为变更）⇒ 登记不改。D07-21（函数级导入、无真实循环）P2 登记不改。
  - **登记残余**：rag 3 条非 LLM 适配器引用（需另立内容安全域/媒体域门面）；`config.py` Literal 与 `deploy` env 模板两处部署面校验

- [x] Task B3: 后端归属校验/软删过滤收敛全覆盖核对（沿用 `0efd838` AST 门禁）：确认所有按 id 路由端点均经 helper，补齐缺口。
  - ⚠️ **结论修正（2026-09-24 深审）**：原判「验证即达标、无缺口」**不成立**——`backend/tests/test_authz_gate.py:47` 登记名 `load_owned_capsule` ≠ 实际符号 `api/capsules.py:45 _load_owned_capsule` ⇒ capsules loader 为**门禁哑条目**（D06-2），靠 `user.id` 属性访问兜底过门。三 helper（D06-1）深审确认**语义相同**、测试零 monkeypatch ⇒ **收敛待办已就绪**，须同时修正门禁登记名。详见 ledger §9.3 与 `audit/domain-06`。

- [x] Task B4: AG8 `alembic check` 漂移治理：先证 N 张遗留表零引用，再决定"显式 drop 迁移"或"ORM 补声明对齐"；**不确定则只登记不删**。
  - **结论：走"只登记"分支**——实跑确认漂移量大且含**破坏性项**（多表 `remove_fk`/`remove_index`、大量 TEXT↔String）；自动生成迁移会破坏约束，**待拍板** schema.sql 与 ORM 谁是权威。

- [x] Task B5: 客户端巨文件拆分（**编译门已恢复**）——**L-04/L-05/L-06 ✅**（`play.uts` / `sync_client.uts` / `uploader.uts` 按域/职责拆分）· **L-08 ✅**（`detail.uvue` 样式外置）· **L-01 ✅**（`RecordSheet` 2209→**753**）· **L-02 ✅**（`TabIndex` 1046→**738**）；`TabSearch.uvue` 本就未超阈、无需处理；**L-12 令牌收敛 → 另立令牌波**（拍板 10.4）。证据见 §8.6–§8.14 与台账 §4.15。
  - [x] B5.1 先探编译门可用性（HBuilderX GUI + Docker）→ 2026-09-24 **用户手动启动 HBuilderX，编译门恢复**（Docker 亦已起）
  - [x] B5.2a `client/utils/play.uts` 653 行 → 6 域模块（`play_echo`/`play_interview`/`play_messages`/`play_favorite`/`play_trash`/`play_content`）；10 调用点改写；冷编译 ✅（L-04）
  - [x] B5.2b `client/utils/sync_client.uts` 705 行 → 5 模块（`sync_types`/`sync_queue`/`sync_local`/`sync_pipeline`/`sync_schedule`）；6 调用点改写；冷编译 ✅（L-05）
  - [x] B5.2c `client/utils/uploader.uts` 628 行 → 6 模块（`uploader_{types,pending,net,queue,photo,batch}`）；8 调用点改写；冷编译 ✅（L-06）；**真机上传待设备**
  - [x] B5.2d 起点（金丝雀）`client/pages/detail/detail.uvue`（L-08）**样式外置**：`<style>` 591 行 → `client/styles/detail.css`（uvue 只留 `@import`）⇒ **1167 → 577 行**（轴 5 销项）；冷编译 ✅ + 产物类名取证；**能力探针实测**：ucss 支持 `@import`（**无需 scss 插件**）、`.uts` 组合式函数可被 uvue 引入
  - [x] B5.2e（样式半）`RecordSheet.uvue`（2209→**1354**）/ `TabIndex.uvue`（1742→**1065**）样式外置到 `client/styles/{record-sheet,tab-index}.css`；逐行一致 + 冷编译 ✅ + 产物类名取证；baseline 计数下调
  - [x] B5.2f-1（第八轮）波形缓存 **5 份同构副本 → `useWaveform` 组合式函数**：新增 `client/composables/useWaveform.uts`；5 组件改用；合计 **−91 行**；等价性证明 + 冷编译 ✅；baseline 下调（TabIndex 1065→1046 / favorites 974→957）
  - [x] B5.2f-2a（第九轮）`RecordSheet` 圆点+轮盘动画 → `useRecordAnimations`（1354→**1135**，−219）；冷编译 ✅；baseline 下调
  - [x] B5.2f-2b（第十轮）`RecordSheet` 自定义标签助手 → `client/utils/custom_labels.uts`（1135→**1084**）；冷编译 ✅；baseline 下调
  - [x] 🚨 GAP-1（第十轮 · 新发现并补门禁）：HBuilderX 编译**只覆盖语法不覆盖符号解析**（8 处缺 import 仍报"编译成功"）→ 新增 `scripts/audit_client_imports.py` + `audit_harness` 轴 `client_imports` + `review_agent.check_audit_axes` 接入（端到端反向探针证真红）
  - [x] B5.2f-2d/2e（第十二/十三轮 · 方案 A 子步 1/2）`RecordSheet` 语音状态机 + 收尾三函数 → `useVoiceRecord`（1054→889→**753**）；**L-01 销项**；冷编译 ✅；轴 5/6 ✅
  - [x] 🚨 **GAP-2（2026-09-25 · 新发现并补门禁 + 修 P0 回归）**：`useVoiceRecord.uts` **`this.saving` 用了却没声明**（编译报绿 + 轴 6 抓不到 ⇒ 真机必崩），修复＝补 `saving = ref(false)`；新增 `scripts/audit_class_members.py`（**轴 7**：①`this.<名>` ②`const x = new Cls(…)` 后 `x.<名>`）+ 接入 `audit_harness` 轴 `class_members` 与 `review_agent`（快/全量）；全仓 **76 类 0 误报**、**反向探针两例 EXIT=1 且字节还原**
  - [x] B5.2f-2f（第十四/十五轮 · 方案 A 子步 3/4）`TabIndex` 拆两块 → `usePhotoAttach`（照片路径/挂载/详情，1046→**899**）+ `useEventOps`（卡片操作/拆分，899→**738**，累计 −308）；两子步各冷编译 ✅ + 轴 5/6/7 ✅；**L-02 销项**（axis-5 条目删除，存量超阈 4→3）；👁 顺带登记"拆分面板/照片详情浮层**模板不可达**"= 功能缺口（交功能波）
  - [x] B5.2f-3（第十六轮）**L-18/L-19/L-20 样式外置 → 轴 5 棘轮清零**：`TabAi` 974→**469** / `favorites` 957→**500** / `manage` 891→**466**（`client/styles/{tab-ai,favorites,portrait-manage}.css`）；归一化字节全等 + 冷编译 ✅（67.8s）+ 产物 `style.bytes` 类名取证；**三条 baseline 条目删除 ⇒ 存量超阈 0**
  - [ ] ⚠️ **真机运行验收待设备**（`adb` 无设备）：B5c 上传、B5d/B5.2e/B5.2f 全子步的渲染/波形/动画/录音保存全链路、**L-01/L-02 运行验收**（不得以冷编译冒充）
  - [x] **决策**：L-12 令牌收敛 → ✅ **另立「令牌波」**（判为复杂，台账 §5.10 拍板 10.4）；64 条功能缺陷 → ✅ **另开「功能修复波」**（拍板 10.1）

- [x] Task B10-i: 门禁自身可靠性 4 项：`D14-23` CI schema-drift 去 `continue-on-error` / `D14-22` 统一机读 schema（新 `scripts/gate_report.py`）/ `D14-12` 收敛 subprocess 双实现（新 `scripts/gate_proc.py`）/ `D14-6` 判定已被 B10-g 覆盖。见 ledger §8.4。

- [x] Task B11（第七轮）声明漂移残余：`D05-9` ✅（rerank 模型名单点化）/ `D02-7` ✅（photos 键布局单点化，新 `services/media_keys.py`）/ `D04-3` ✅（`night` 接线 + 删冗余 `gps_speed`）/ `D12-7` ✅（失效证据书面作废）/ `D02-9` ⏸ **登记未改**（属行为变更 → 功能波）/ `D09-10` ✅（wechat status 唯一来源 + 防漂移门禁）/ `D07-14` ⏸ **登记未改**（属 UI 文案变更）。**B11 结构类项已全部处置完毕**。见 ledger §8.5。

- [x] Task B6: 客户端声明漂移清理：`shell.uvue:7`、`search_api.uts:183`、`feature_list.json` 自相矛盾证据；`audit_harness client` 无新增漂移。
  - **结论：验证即达标**——三则已由 09-23 审计窗修复；残余两处注释原文含「对齐原 …」，B1 的「原」标注抑制生效 → 轴 2 无注释内未注册页面引用。

- [x] Task B7: 脚本/部署域清理：仅做台账中"低风险、可自验"项（幂等/硬编码/注释）；不触碰 `deploy/` 出包链路的敏感逻辑。

- [x] Task B8: 每批收口（对本波所有批次循环执行）：`python scripts/review_agent.py` → 受影响测试 →（涉客户端）冷编译 → `python scripts/review_agent.py --full` → `python scripts/lessons.py add` 登记踩坑 → `docs/决策台账.md` 回填进度 → 独立提交（描述性 message + 批次号）。
  - **执行证据**：`--full` → `✅ 审核通过`（syntax 362 / lint / secrets / structure / tests / research 全 ✅）；基线 `pytest` 848 passed；lessons 登记 1 条；台账 §4.13/§5.9 回填；`progress.md` 追加速查卡；两批独立提交 `6acd826`/`09708d7`（均经 hook，未用 `--no-verify`）。

## Task Dependencies

- A7 依赖 A1–A6 全部完成。
- Phase B 全部依赖 A7（用户拍板）。
- B1 先于 B2–B7（棘轮先立，防止清账过程中新增债）。
- B2/B4 相互独立，可并行；B3 先于 B4（同属后端且 B4 可能改模型声明）。
- B5/B6 依赖 B1（漂移轴）且同属客户端，须串行以避免多次触碰同一文件、减少编译门开销。
- B7 独立，可随时插入。
- B8 对每批串行执行，不可与其他批次并行提交。