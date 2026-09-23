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

- [ ] Task A7: 提交台账给用户拍板（`NotifyUser`）：用户确认优先级与批次切分后方可进入 Phase B。

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

- [ ] Task B10-b: 门禁缺口补强（深审新立项，待执行）——D04-15 双跑夹具补 `approx`/`corrected` 分支；D11-2 错误码 AST 门禁跨模块盲区；D12-9 轴 2 三向缺一向

- [x] Task B12: 脚本/部署可移植性（**部分**）——D13-1 恒绿假门禁退役（CI client tsc 步骤 → 阻断式 `audit_harness client`）；D13-26 deploy/README 补齐；硬编码路径清 4 处（`smoke_cos_upload` / `backup_pg.ps1` / `reinject_missing_photos` / `seed_echo_today` 改 env+显式默认+缺失报错）。**D13-3 只登记**（实跑 `check_env_template.py` 当前即失败：`AGENT_SERVICE_*` config 有模板缺 → 先补模板再挂门禁）

- [x] Task B11: 声明漂移清零（**安全子集**）——D04-17（类型标注）、D13-28（docstring 对齐）、D13-14（注释与代码一致）、D14-2（体积阈值死配置，随 B10 修复）。残余 D02-7/9、D04-3、D05-9/15、D07-13/14、D09-10、D11-5、D12-7 待处置（需决策/属死配置删除，宜单独立项）

- [x] Task B10-c: 错误码**反向棘轮**（D11-5：registered-but-never-raised）——`test_error_registry.py` 新增 `test_no_new_dead_error_codes`（**双向**：新增死登记 → 红；基线项已销项未移除 → 也红）+ `DEAD_CODE_BASELINE`（3 枚带原因/销项触发）+ 自校验。证据：实测 `registered − raised = 恰好 3 枚`（67/64）；内存级负向探针（模拟 `ZZZ_PROBE_999`）→ 判为基线外新增 ⇒ 非空转。**刻意不删这 3 枚**（删除前需确认无外部消费方）

- [x] Task B11-x: **D07-13 改判**——实读 `validate()` 仅测试调用（生产 `get_schema()` 不调它）⇒ 属测试期契约断言（51 L0 / 193 L1 即规格契约），**非缺陷**，保留不修（理由入 ledger §9.3 第 5 条）

- [x] Task B10-d: 轴 4 `EXPORT_RE` **假阴性**修正 + 零调用能力导出棘轮——`^\s*export` 的 `\s` 含换行 ⇒ 匹配点落前导空行 ⇒ 声明行排除失配 ⇒ 每个导出把自身计成 1 引用 ⇒ **轴 4 长期假绿**（`zero_cap` 应为 4 却恒为 0；298 导出中 56 个声明行偏移）。修为 `^[ \t]*`；露出 4 枚真零调用能力导出（`AuthError`/`getRecorder`/`lastTempFile`/`stopPeriodicSync`），按 B1 口径基线冻结 + 只拦新增 + 清算僵尸豁免。证据：双负向探针（新增导出 → CRITICAL 且**行号正确**；基线塞假条目 → 僵尸豁免 CRITICAL）均已还原；`all` 无 CRITICAL

- [x] Task B10-e: filesize / dup 基线**僵尸豁免清算**（D14-4 的另一半）——原白名单只拦新增、从不检查条目是否失效（文件被删/改名/拆到阈值内 ⇒ 永久白条）。现：filesize 对 `set(allowlist) − 仍超阈` 报 CRITICAL；dup 对 `per_file` 已归零条目报 CRITICAL；存量下降报 WARN/INFO 要求同步下调基线。证据：先逐条对账（6 条 filesize / 21 条 dup per_file 全 `OK`、total 44=44）⇒ 不引入误红；双负向探针（client 阈值 800→3000 ⇒ **6 条**僵尸 CRITICAL；dup 塞不存在文件 ⇒ **1 条** CRITICAL）均 EXIT=1 且基线按原文精确还原

- [x] Task B10-f: 轴 4 **覆盖面补全**（D14-16）——① `audit_exports` 原 `glob("*.uts")` **非递归** ⇒ `client/utils/agg/**` 等子目录整体漏扫；② `EXPORT_RE` 只认 `function|const|let|class` ⇒ `export type/interface/enum/default` 漏扫。修为 `rglob("*.uts")` + 正则补 `(?:default\s+)?` 与 `type|interface|enum`。证据：先量化（补递归 +29 导出、其中零调用 1 个＝`WALK_SPEED_MS`＝深审 D04-12；补 7 类 +5 导出、零调用 0）；修后导出 **298 → 332**、存量 5、无新增；子目录负向探针（追加零调用导出）→ CRITICAL 且**行号正确**，已还原；`all` 无 CRITICAL

- [x] Task B10-g: 契约快照**再生脚本 + 一致性断言**（D14-7）——新增 `scripts/gen_openapi.py`（写入 / `--check`），接入 `review_agent` 的 `openapi_snapshot` 检查（0=一致放行 / 1=过期阻断 / 2=环境不可导入降级放行）。证据：`--check` 证明现有快照与后端**逐字节一致**（239518 字符，此前无任何机器能证）；负向探针（塞幽灵路径）→ `[FAIL]` + 精确定位「快照有而后端无（1）」EXIT=1、`review_agent` 阻断；再生还原后与 HEAD 逐字节一致

- [ ] Task B9: 端口/边界收口（**待执行**）——L-03c API/rag 层越级直连；D05-16 `correction.py` 自建第二套 Qdrant；D02-3 存储后端注册点（含 api 层硬编码 COS）

- [x] Task B3: 后端归属校验/软删过滤收敛全覆盖核对（沿用 `0efd838` AST 门禁）：确认所有按 id 路由端点均经 helper，补齐缺口。
  - ⚠️ **结论修正（2026-09-24 深审）**：原判「验证即达标、无缺口」**不成立**——`backend/tests/test_authz_gate.py:47` 登记名 `load_owned_capsule` ≠ 实际符号 `api/capsules.py:45 _load_owned_capsule` ⇒ capsules loader 为**门禁哑条目**（D06-2），靠 `user.id` 属性访问兜底过门。三 helper（D06-1）深审确认**语义相同**、测试零 monkeypatch ⇒ **收敛待办已就绪**，须同时修正门禁登记名。详见 ledger §9.3 与 `audit/domain-06`。

- [x] Task B4: AG8 `alembic check` 漂移治理：先证 N 张遗留表零引用，再决定"显式 drop 迁移"或"ORM 补声明对齐"；**不确定则只登记不删**。
  - **结论：走"只登记"分支**——实跑确认漂移量大且含**破坏性项**（多表 `remove_fk`/`remove_index`、大量 TEXT↔String）；自动生成迁移会破坏约束，**待拍板** schema.sql 与 ORM 谁是权威。

- [ ] Task B5: 客户端巨文件拆分（**需编译门**）：`TabSearch.uvue`、`sync_client.uts`、`uploader.uts`；每拆一次跑冷编译 0 error。
  - [x] B5.1 先探编译门可用性（HBuilderX GUI + Docker）；不可用则本任务顺延并登记 → **用户拍板：不可用，顺延**
  - [ ] B5.2 逐个拆分 + 冷编译验证（**顺延**，待编译门可用）

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