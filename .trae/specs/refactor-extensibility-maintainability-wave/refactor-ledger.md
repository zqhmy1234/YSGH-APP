# 重构台账（Refactor Ledger）· 易扩展/易维护重构波

> 唯一排期事实源（本波）。**性质：候审项 vs 已确认项分离**；判据一律以代码/命令输出为证据。
> 生成日：2026-09-24 ｜ 范围：`backend/app/` + `client/` + `scripts/` + `deploy/`（`agent/` 排除）
> 证据命令：`python scripts/audit_harness.py all`、行数/模式静态统计（PowerShell `Select-String`）、定点读码。

## 0. 筛查 rubric（A1）

- **类型**：巨文件 · 重复/未收敛 · 死码/冗余 · 边界/依赖方向 · 声明漂移 · 门禁缺口 · 文档债
- **严重度**（对齐 `dev-code-review`）：**P0**=阻碍改动/易致事故 ｜ **P1**=明确结构债 ｜ **P2**=顺手可清
- **可回滚性**：`纯移动+兼容再导出`（最可逆）｜`行为等价替换`｜`新增工具`（零风险）｜`删码`（需证死）
- **状态**：`确认`（有直接证据）｜`候审`（启发式，需人工确认）

## 1. 基线事实（证据）

| 事实 | 值 | 来源 |
|---|---|---|
| 契约对拍 | **67/67 完全对齐**，无幽灵路径、无方法差 | `audit_harness openapi` |
| 客户端死路由 | **0**（`USE_MOCK_*` 2 处未关；注释内旧页引用 2 条） | `audit_harness client` |
| 零调用能力导出 | **0**（轴 4 已归零） | `audit_harness exports` |
| 模型 0 引用 | **2 个（假阳性，见 L-11）** | `audit_harness models` |
| 真实 TODO | **仅 3 处 T1 短信/推送通道**（正当登记，非死码） | 模式扫描 |
| 归属/软删手抄 `deleted_at.is_(None)` | **44 处 / 21 文件**（`contents.py` 8、`events.py` 6 最密） | 模式扫描 |
| 波D 遗留 | services 三大巨文件（pipeline 708 / agg 615 / aggregate 516）**已拆并合入** | `git log`（`1ec8a15/b37dac7/2f14af0`） |
| **在途业务文件（2026-09-24 复核）** | **0** —— AGENTS「第四窗媒体票据 8 文件在途」在本时点**均已入库**，勿碰名单可解除 | 14 份域报告一致记录 `git status --short` 仅 `?? .codebuddy/` |
| **Phase A 深度审计规模** | 14 域 / **249 条** = 结构类 185 + 功能与安全缺陷 64（P0 17 / P1 111 / P2 121；候审 30） | `audit/_PHASEA_ROLLUP.md` + `audit/domain-01..14-*.md` |
| 软删手抄计数（`backend/app`） | **44 处 / 21 文件**（生产口径，与旧台账一致）；含 tests/scripts 另计 5 处 → 全 `backend/` 49 处/25 文件 | `Select-String 'deleted_at\.is_\(None\)'` |
| 错误码登记对拍 | **漏登记 4 枚**（CAPSULE_001..004 就地定义）、**死登记 3 枚**（AUTH_099/CONTENT_004/CONTENT_011） | `audit/domain-11` D11-1/D11-5 |
| 设计令牌收敛度 | `design_tokens.json` **零代码引用**（机制覆盖率 0%）；763 处色字面量中 660 处靠人工抄写巧合相等 | `audit/domain-12` D12-1 |
| 端云聚合一致性 | 共享参数 **8 项数值零漂移**，但 **语义/行为漂移 7 处**（B1–B7，含 L1 时区、预处理去重） | `audit/domain-04` + 汇总 §6.1 |
| LWW 端云一致性 | 4 项规则中 **3 项不一致**（tie-break 客户端零判定、字段级无本地载体、软删双轨） | `audit/domain-08` + 汇总 §6.2 |

## 2. P0 · 巨文件（阻碍改动，最优先）

| # | 位置 | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次 |
|---|---|---|---|---|---|---|---|---|
| L-01 | `client/components/RecordSheet/RecordSheet.uvue` **2208 行**（script 1095 / style 857 / tpl 251） | 巨文件 | P0 | 记录面板是本应用最高频交互；拆分后单点改动不再触碰 2 千行 | 高（交互核心，回归面大） | 纯移动+兼容 | 冷编译 0 error + 真机记录链路 | B5 |
| L-02 | `client/components/TabIndex/TabIndex.uvue` script **848 行** | 巨文件 | P0 | 时间轴主页逻辑与模板纠缠 | 高 | 纯移动 | 冷编译 + 真机 | B5 |
| L-03 | `backend/app/api/contents.py` **902 行**（手抄软删模式 8 处）——深审确认**4 router 物理交错混居**（`router` / `profile_sensitive` / `favorites` / `trash` / waveform） | 巨文件+重复 | P0 | 内容域为后端最热 API；拆分+走 helper 降回归面 | 中（有 contents 测试族护航） | 纯移动+兼容再导出 | pytest contents/content_upload/photo_content + `audit_harness openapi` | B2（**最小方案已定，见 §9.4**） |
| L-03b | `backend/app/api/events.py` **530 行**（软删手抄 6 处；`_batch_*` 同语义双实现、图片 URL 三写） | 巨文件+重复 | P1 | 事件域读写混杂 | 中 | 纯移动+兼容再导出 | pytest events/aggregation + openapi | B2 |
| L-03c | `api/events.py:100` / `rag/rewrite.py:15` / `rag/image.py:44,52` / `api/upload.py:264` → **API 层与 rag 层越级直连**（绕过 `services/external` 端口 / `llm_ops` 门面） | 边界/依赖方向（FF1） | P1 | 端口抽象被架空，换厂商成本外溢 | 中 | 行为等价替换 | 静态 import 边 + pytest | 后续 |
| L-18 | `client/components/TabAi/TabAi.uvue` （原 974 行；含 `USE_MOCK_CHAT` 演示开关，已登记 B8） | 巨文件 | P1 | ✅ **已销项 2026-09-25**：`<style>` 507 行**纯移动**到 `client/styles/tab-ai.css` ⇒ **974 → 469**（轴 5 条目删除） | 低（样式纯移动） | 纯移动 | 归一化字节全等 + 冷编译 + 产物 `style.bytes` 含 `ai-root`/`ai-header` | B5 |
| L-19 | `client/pages/favorites/favorites.uvue` （原 957 行；已随 B5.2f-1 抽 `useWaveform`，975→957） | 巨文件 | P1 | ✅ **已销项 2026-09-25**：`<style>` 459 行**纯移动**到 `client/styles/favorites.css` ⇒ **957 → 500**（轴 5 条目删除） | 低 | 纯移动 | 同上（`page-root`/`nav-title`） | B5 |
| L-20 | `client/pages/portrait/manage.uvue` （原 891 行） | 巨文件 | P1 | ✅ **已销项 2026-09-25**：`<style>` 427 行**纯移动**到 `client/styles/portrait-manage.css` ⇒ **891 → 466**（轴 5 条目删除） | 低 | 纯移动 | 同上（`page`/`serif`） | B5 |

## 3. P1 · 结构债

| # | 位置 | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次 |
|---|---|---|---|---|---|---|---|---|
| L-04 | `client/utils/play.uts` **653 行 / 22 导出**：跨域 façade（favorite/trash/interview/messages/users/remark 混居） | 未收敛 | P1 | 客户端最典型"万能模块"，按域拆分后职责可读、可测 | 中 | 纯移动+再导出 | 冷编译 + 调用方零改动 | B5 |
| L-05 | `client/utils/sync_client.uts` **705 行 / 22 导出** | 巨文件 | P1 | 同步域核心，体量近 god-module | 中 | 纯移动+再导出 | 冷编译 + sync 相关 | B5 |
| L-06 | `client/utils/uploader.uts` **628 行**（**第四窗在途域**，编辑前必须复核 `git status`） | 巨文件 | P1 | 上传管线最脆弱链，体量增加误改概率 | 高 | 纯移动 | 冷编译 + 真机上传 | B5 |
| L-07 | `backend/app/api/events.py` **530 行**（手抄软删模式 6 处） | 巨文件+重复 | P1 | 事件域读写混杂 | 中 | 纯移动+兼容再导出 | pytest events/aggregation + openapi | B2 |
| L-08 | `client/pages/detail/detail.uvue`（script 426 / style 592） | 巨文件 | P1 | 详情页样式膨胀 | 中 | 纯移动 | 冷编译 | B5 |
| L-09 | **归属 helper 三分裂**：`api/capsules.py:45 _load_owned_capsule`（私有、api 层）· `api/deps.py:84 load_alive_content` · `api/messages.py:37 load_owned_message` → 命名/家/可见性三不一致 | 边界/未收敛 | P1 | 新端点无统一范式可循，易写漏（P2-2 根因）；收敛后扩展成本降 | 中 | 行为等价替换 | `pytest`（authz 族 + AST 门禁 `0efd838`） | B3 |
| L-10 | `audit_harness.py` **无结构轴**（体积/重复/契约路径只增不减断言） | 门禁缺口 | P1 | 清账成果无法防回潮；`决策台账 §2.14` 建议未落地 | 低（纯新增） | 新增工具（零风险） | 已知样本自校验 | B1 |
| L-11 | `audit_harness.py:280` 轴 3 把**镜像表**判为 CRITICAL：`KnowledgeCollection`/`MemoryCategory`（`models/memory_agent.py`）实为**刻意镜像**——docstring 明载"表若只存在于迁移、不进 `Base.metadata`，`--autogenerate` 会生成 DROP TABLE" | 门禁缺口 | P1 | 当前每跑一次轴 3 就有 1 条假 CRITICAL → **假阳性会污染台账**（先例 44 条假死路由） | 低 | 新增工具 | 自校验 + `alembic check` 仍绿 | B1（已做，见 §9.3 修正） |
| ~~原 L-11 称有 `test_agent_schema_alignment.py` 守卫~~ | **更正（2026-09-24）**：该守卫文件**全仓 + git 全历史均不存在**（`Get-ChildItem -Recurse -Filter *alignment*` → 0；`git log --all --diff-filter=A` → 空）。B1 的镜像豁免当前**无任何自校验** | 声明漂移 | P1 | 幻影守卫使豁免失去正当性依据 | — | — | 补真实对拍测试 或 删该声明 | 后续窗口（见 §9.3） |
| L-12 | 客户端 `.uvue` **style 块合计 7391 行 / 32 文件**，而 `client/design_tokens.json` 存在 → 设计令牌未收敛到样式（**深审升格，见 D12-1/D12-2/D12-3**） | 重复/令牌零覆盖 | **P0**（深审后升级） | 令牌机制覆盖率 **0%**（`design_tokens.json` 零代码引用）；763 处色字面量、660 处靠人工抄写巧合相等；令牌值本身也已漂移（26/56 去重值缺失） | 中 | 行为等价替换（仅样式；App 端样式不继承 ⇒ 须构建期注入） | 冷编译 + 目视 | B5 |

## 4. P2 · 顺手可清

| # | 位置 | 类型 | 严重度 | 处置 | 批次 |
|---|---|---|---|---|---|
| L-13 | `feature_list.json:64`（F5 evidence）**追加式巨串 ~4k 字符**，靠"以本注为判据"救场 | 文档债 | P2 | 建议 evidence 只保留指针 + 指向 `docs/`；**本波只登记，改工具链另议** | 登记 |
| L-14 | `scripts/agg_load_real_photos.py:5,32` 硬编码个人绝对路径 `C:\Users\ghf\Pictures\Screenshots` | 可移植性 | P2 | 改为环境变量（已有 `SCREENSHOT_DIR` 约定，补默认+报错） | B7 |
| L-15 | `scripts/backup_pg.ps1:8` 默认 `C:\Program Files\PostgreSQL\17\bin`（机器/版本绑定） | 可移植性 | P2 | 默认值改为"探测 + 显式提示" | B7 |
| L-16 | `scripts/check_schema_drift.py:56` 默认 `postgresql+psycopg://postgres:admin@localhost:5432/postgres`（凭据内联默认） | 凭据卫生 | P2 | 默认改空 + 必填校验（同 `deploy/.env` 纪律） | B7 |
| L-17 | `client/pages/shell/shell.uvue:63,65` 行内注释引 `/pages/search/search`、`/pages/ai/ai`（未注册）未标「原」 | 声明漂移 | P2 | 加「原」字样（`audit_harness client` INFO→0） | B6 |

## 5. 与既有登记对账（A6：**已登记项不重复立项**）

| 已有登记 | 出处 | 本波处置 |
|---|---|---|
| §3.1 声明漂移三则（`shell.uvue:7` / `search_api.uts:183` / `feature_list.json:64`） | `审计_未关闭缺陷 §3` | ✅ **均已由 09-23 审计窗修复**（逐条读码核实）；残余仅 L-17（注释标「原」） |
| `USE_MOCK_LOCATION`（`RecordSheet.uvue:493`）/ `USE_MOCK_CHAT`（`TabAi.uvue:216`）=true | `远期待办 B8` | 保持登记，**本波不动**（翻转改行为，需前置） |
| AG8 `alembic check` 常年红（12+ 遗留表） | `待办处理排期 §AG8` | 见 B4（先证零引用，不确定则只登记） |
| §2.15 空串绕过签名密钥（`_apply_production_safety`） | `决策台账 §2.15` | **本波不修**（改行为/fail-closed），登记为缺陷 |
| `agent/` 354 处 lint 违规 + AG7 豁免撤销 | `决策台账 §4.12` | **本波范围外**（`agent/` 排除） |
| 真实 TODO 3 处（短信/推送 T1） | `后端重构计划 P2-3` | 正当登记，非死码，**结案** |
| 波D services 三大巨文件 | `后端重构计划 波D` | ✅ 已闭环（`1ec8a15/b37dac7/2f14af0`） |
| `memory_agent.py` 2 镜像表 0 引用 | `models/memory_agent.py` docstring | 属**刻意镜像**，非缺口；转化为 L-11（修门禁假阳性） |
| 图片查询从未评测 / RAG 负样本误召回 0.5714 | `决策台账 §7.2/§7.3` | 属能力缺口（功能波），**非结构债**，不在本波 |

## 6. 批次归属建议（供拍板）

| 批次 | 内容 | 依赖 | 风险 |
|---|---|---|---|
| **B1**（先做） | L-10 结构轴 + L-11 镜像豁免（**先棘轮后清账**） | 无 | 低（纯新增） |
| **B2** | L-03 `contents.py`、L-07 `events.py` 拆分 | B1 | 中（测试护航） |
| **B3** | L-09 归属 helper 收敛 | B2（同域先后端） | 中（AST 门禁护航） |
| **B4** | AG8 漂移治理 | B3 | 低（只登记亦可） |
| **B5** | L-01/02/04/05/06/08/12 客户端巨文件与样式收敛 | B1 + 编译门 | **高**（交互核心，须冷编译+真机；L-06 属在途域需复核） |
| **B6** | L-17 声明漂移残留 | 无 | 极低 |
| **B7** | L-14/15/16 脚本可移植性/凭据卫生 | 无 | 低 |

**推荐顺序**：B1 → B6 → B7 → B2 → B3 → B4 → B5（B5 视编译门可用性决定是否顺延）。

## 7. 本波不覆盖（边界声明）

- `agent/`（独立改写批次）、`research/`（评测语料）；功能性/安全缺陷（§2.15、RAG 重校、推送真实通道、图搜评测）→ 交**功能波**（2026-09-24 已拍板，§5.10 拍板 10.1）。
- 架构边界变更（如需调整依赖方向/拆服务）→ 另开 `architecture-decisions` ADR。
- **L-12 设计令牌收敛 → 另立「令牌波」**（2026-09-24 已拍板，§5.10 拍板 10.4；判为复杂：规模/令牌值漂移/App 端样式不继承需构建期注入/验收须目视真机）。

## 8. 执行结果（2026-09-24 回填）

| 批次 | 条目 | 状态 | 证据 / 提交 | 备注 |
|---|---|---|---|---|
| B1 | L-10 结构轴 + L-11 镜像豁免 | ✅ 完成 | `6acd826`；`audit_harness all` → **无 CRITICAL**（原 1 条假阳性已消） | 新增轴 5 体积 / 轴 6 重复模式 + `review_agent` 接入 `structure` 检查；基线 `scripts/audit_harness_baseline.json`；负向探针（清空基线 → 体积 7 条 / dup 1 条 CRITICAL）证明非空转 |
| B3 | L-09 归属校验覆盖 | ⚠️ **结论修正（深审后）** | 原判据 `pytest backend/tests/test_authz_gate.py -q` → 6 passed；但域⑥ 实读发现**门禁哑条目**（`test_authz_gate.py:47` 登记名 `load_owned_capsule` ≠ 实际符号 `api/capsules.py:45 _load_owned_capsule`）→ `_ownership_evidence` 精确匹配失配，capsules loader **从未被门禁认出**，靠 `user.id` 属性访问兜底通过；`test_whitelist_and_exempt_entries_still_exist` 只校验 CREATION_WHITELIST/EXEMPT，**不校验 OWNERSHIP_LOADERS** ⇒ 哑条目永不被发现 | 原"验证即达标/无缺口"**不成立**：门禁存在覆盖盲点（D06-2）。归属三 helper 收敛（D06-1：`capsules._load_owned_capsule` / `deps.load_alive_content` / `messages.load_owned_message`，深审确认**语义相同**、测试对三者零 monkeypatch ⇒ 迁移安全）升级为**待办**，须同时修正门禁登记名 |
| B6 | L-17 声明漂移 | ✅ 完成（**验证即达标，无代码改动**） | `audit_harness client` → **无注释内未注册页面引用** | 该两处注释原文已含「对齐**原** …」，B1 的「原」标注抑制生效；连同 §3.1 三则（09-23 已修）＝漂移清零 |
| B7 | L-14/15/16 脚本可移植性/凭据卫生 | ✅ 完成 | `09708d7`；ruff 0 / py_compile 0 / 无凭据路径实测 exit 2 / `backup_pg.ps1` PowerShell 解析 0 错 | 个人绝对路径清理；PGBin 探测 + 显式报错；`check_schema_drift.py` 移除内联 `postgres:admin` 默认凭据 |
| B4 | AG8 `alembic check` 漂移 | ⏸ **仅登记（未改）** | 实跑 `python -m alembic check`（DATABASE_URL=yishu_app@localhost/yishu）：漂移**量大且含破坏性项**（大量 `modify_type` TEXT↔String、多表 `remove_fk` / `remove_index`、索引改名） | 按 B4 预设口径「不确定则只登记不删」——**自动生成迁移会 DROP 外键/索引**，属可删数据/破坏约束的静默事故。**待拍板**：`schema.sql`（CI 建库源）与 ORM/alembic 谁是权威，再定"补 drop 迁移"或"ORM 补声明对齐" |
| B2 | L-03 `contents.py` / L-07 `events.py` 拆分 | ⏸ **顺延（本会话未执行）** | 仅完成勘察：`contents.py` 实为 **4 router 混居**（`contents` / `profile_sensitive`（454-514）/ `favorites`（641-689）/ `trash`（690-814）/ waveform（815-902）） | 顺延理由（工程判断，非偷懒）：① 天然拆法＝转 `api/contents/` 子包（沿用 `upload/` 先例）；② 部分抽取（如只移 profile_sensitive）后 **902→~845 仍 >600**，棘轮不解除、纯增文件，收益为零；③ 完整 4-router 迁移≈900 行移动 + 多处 `_to_out`/`_attach_thumb` 共享符号与测试 monkeypatch 面，须**专用验证窗口**；④ 棘轮已冻结该文件，**不产生新债**。L-07 `events.py` 530 行**低于 600 阈值**，非棘轮项、优先级更低 |
| B5 | L-01/02/04/05/06/08/12 客户端拆分 | ⏸ **顺延（用户拍板）** | — | 编译门（HBuilderX GUI + Docker）不可用 → 用户拍板顺延，避免"未验证改动堆进最脆弱链路" |

### 8.2 Phase B 深审后新增批次（2026-09-24 第二轮执行）

| 批次 | 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| B2 | L-03 `contents.py` → `api/contents/` 子包 | ✅ 完成 | 拆 5 子模块（serializers 48 / profile_sensitive 89 / favorites 111 / trash 115 / waveform 115），`__init__.py` **902 → 524 行（<600 ⇒ 棘轮解除）**，`git mv` 保历史；`py_compile` 6 文件 0 错；`pytest` 受影响 89 passed；`audit_harness openapi` 67/67 对齐、无幽灵路径；轴 5 存量超阈 **7 → 6** | AST 逐语句比对：原 902 行 **31/31 顶层语句文本完全一致**，零缺失零 diff；`MAX_PHOTO_BYTES`/`enqueue_unique` 与 6 个核心端点**留在 `__init__.py`**（测试 monkeypatch 面）；唯一可观测差异＝favorites 模块 logger 通道名变为 `app.api.contents.favorites`（未改代码） |
| B2′ | **B2 引发的门禁覆盖回归修复**（本波自查发现） | ✅ 完成 | `test_authz_gate.py` 由 `glob("*.py")` → **`rglob("*.py")`（含子包）**；候选 **20+ → 29**，子包模块 `contents/{__init__,favorites,trash,waveform}.py` 已被扫到；新增 `test_scan_covers_subpackages` 防回归 | 若不修：contents 全部端点**静默退出扫描面**，门禁"全绿"却不覆盖（D12-9「扫描面缺一向」同族）。另 `_router_var_names` 扩展为「本地 make_router ∪ import 的 router」以覆盖共享 router 端点 |
| B3′ | L-09 三 helper 收敛 + D06-2 门禁哑条目 | ✅ 完成 | `deps.py` 新增泛化 `load_owned_entity`；`capsules._load_owned_capsule` / `messages.load_owned_message` / `load_alive_content` **三处委托**（错误码/文案/HTTP 逐字保留）；`OWNERSHIP_LOADERS` 修正 `load_owned_capsule` → **`_load_owned_capsule`**、删 3 个幻影预留项、加 `load_owned_entity`；新增 `test_ownership_loaders_all_exist`（登记名必须对应真实符号） | 自校验实证：`'load_owned_capsule' in live → False`（旧哑名会被拦）、`'_load_owned_capsule'/'load_owned_entity' → True`；`pytest test_authz_gate + contents + content_upload + echo` → **56 passed**；全量 `pytest backend/tests` → **850 passed / 4 skipped**（较基线 848 = 新增 2 条门禁自检，零回归） |
| B10 | 门禁加固：D14-1 轴 1–4 进自动门禁 + D14-3/5 假阴假阳 + D14-2 | ✅ 完成 | `review_agent.py` 新增 `audit_axes` 检查（快/全量都跑）；`ci.yml` full-gate 新增**阻断式** `audit_harness all`；`audit_harness.py` 重复模式改**总数 + per_file 双 gate**、镜像豁免收窄为「模块 docstring 含"镜像" **且** 该类自身声明 `__tablename__`」、体积阈值改由基线 `filesize.thresholds` 驱动 | 负向探针：临时造零调用导出 → `audit_harness exports` 报 **CRITICAL**、`review_agent` **退出码 1**；已还原（`client/utils/config.uts` 无残留改动） |
| B12 | 脚本/部署：D13-1 假门禁 + D13-26 + 硬编码路径 4 处 | ⚠️ **部分完成** | **D13-1 已修**：删除 CI「client tsc 试点」——核实其 `continue-on-error: true` + 脚本末尾**无条件 `exit 0`**（真恒绿假门禁，且 `client/tsconfig*.json` 根本不存在、tsc 不认 `.uts`），替换为**阻断式** `audit_harness client`；**D13-26 已修**（deploy/README 补齐 3 脚本）；**硬编码路径已清 4 处**（`smoke_cos_upload.py` / `backup_pg.ps1` / `reinject_missing_photos.py` / `seed_echo_today.py`，均改 env + 显式默认 + 缺失报错） | **D13-3 只登记**：实跑 `check_env_template.py` **当前即失败**（`AGENT_SERVICE_BASE_URL/TIMEOUT_S/TOKEN` config 有、模板缺）→ 按纪律**先补模板再挂门禁**，不接假绿调用；**D13-17/18/25/21/22/23/24/13、D13-6/7 只登记**（改动即改部署行为且本机不可验证／一次性死脚本） |
| B11 | 声明漂移·安全子集 | ⚠️ **部分完成** | **已修 3 项**：D04-17（`tags: list[str] \| None`，仅类型标注）、D13-28（docstring 与代码对齐）、D13-14（注释写 `Token=***` 而代码明文 → 注释改为与代码一致） | **D13-14 附注**：该文件（`backend/scripts/wecom_sandbox.py`）**整体持有企微官方文档测试凭据**（Token/EncodingAESKey/corpid 明文，非生产密钥），根治须改 env 注入＝行为变更 → **移交后续**；其余漂移项（D02-7/9、D04-3、D05-9/15、D07-13/14、D09-10、D11-5、D12-7、D14-2✅）见 §9.7 状态 |
| B9 | 端口/边界收口（L-03c 越级直连 / D05-16 第二套 Qdrant / D02-3 存储注册点） | ⏸ **待执行** | — | 深审首次立项；改动依赖方向与厂商调用点，**须专用窗口 + 逐点读码**（不可与 B2 同批混提） |

### 8.3 B10-b 门禁缺口补强（2026-09-24 第三轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **D11-2** 错误码 AST 门禁跨模块盲区 | ✅ 修复 | `test_error_registry.py` 新增 `_scan_source_codes`（就地常量优先 + `ERR_*` 引用收集）；实证 **旧实现 `capsules.py → []`（盲区）／新实现 → `['CAPSULE_001'..'004']`**；反向探针（临时 `_probe_d11_2_tmp.py` 抛 `PROBE_900`）→ 门禁红，已删 | 连带 **D11-1（P0 结构类）已闭环** |
| **D11-1** `CAPSULE_001..004` 漏登记 | ✅ 修复 | `app/core/errors.py` 补登记 4 枚，**http 与 raise 点逐条一致**（001=422 / 002=404 / 003=409 / 004=409）；`capsules.py` 就地常量与 raise **未改动**（只补登记） | 验证：`pytest test_error_registry + test_ba2_capsule` → **14 passed** |
| **D12-9** 轴 2 三向缺一向 + 未扫生成物 | ✅ 修复（部分） | `audit_harness.py` 新增 `_declared_pages_missing_files`（**pages.json → 物理文件反向对拍**，缺失即 CRITICAL）；实证 `pages.json 16 条均有物理文件 ✓`；负向探针（加 phantom page）→ CRITICAL、EXIT=1，已还原 | `uvue_gen/` **判定不纳入**扫描面（仓根 232 个生成物，非 client 源码面，纳入会产假阳/假阴）——理由写入 `audit_client` docstring |
| **D04-15** 双跑夹具未覆盖 `approx`/`corrected` | ⏸ **阻塞（不可在本波修复）** | 实证判定：服务端 `corrected` 把坐标**拉回众数格中心**、客户端 `pipeline.uts` **置空**，且速度参照不同（服务端用 `folded[i-1]` 原始坐标 / 客户端用可能已 null 的 `corrected` 末张 → 跳过）⇒ 级联差异。最小 4 点用例（F1@t0, F2@+20s, A@+30s, F2@+230s）：服务端簇 `['p2,p3,p4']` vs 客户端 `['p1,p2,p3,p4']`，归一后不等 ⇒ **补夹具必致双跑门禁变红** | **依赖关系明确**：D04-15 的修复**被 D04-1/2/3/4/5（端云对齐，属功能波）阻塞**——先由功能波对齐端云，再补夹具。这解释了"为何双跑全绿"：现有 `gps-drift-corrected` 夹具落 `degraded`（众数支持 1<2）恰与客户端 null **巧合同值** |
| **D13-3** `check_env_template.py` 无门禁 + 模板真缺口 | ✅ **修复（原判"只登记"已升级为修复）** | 实跑确认真缺口 3 项 = `AGENT_SERVICE_BASE_URL/TIMEOUT_S/TOKEN`（真实 config 面：`core/config.py:64-68`，被 `services/external/agent.py:47-68,92` 实际使用）；**模板 §7 末补齐**（URL/TIMEOUT 生效行 = config 默认；TOKEN 保持**注释**态，守模板"必填不留空值"纪律）；`review_agent` 新增 **`env_template` 阻断式检查**（快/全量都跑）；CI full-gate 新增独立步骤（无 `continue-on-error`） | 验证：`check_env_template.py` → **EXIT 0**（`config 字段 73 ｜ 生效 59 ｜ 注释 39`）；`review_agent` → `[✅] env_template` + 审核通过。**反向探针**：临时删 `AGENT_SERVICE_BASE_URL=` → `[❌] env_template 真缺口: ['AGENT_SERVICE_BASE_URL']`、**EXIT 1**，已完全还原 |
| **D11-5** 死登记 3 枚（`AUTH_099`/`CONTENT_004`/`CONTENT_011`） | ✅ **已闭环（棘轮化）** | 实测 `registered − raised = 恰好 3 枚`（67 注册 / 64 使用，与域⑪ 结论一致）；`test_error_registry.py` 新增**反向棘轮** `test_no_new_dead_error_codes`（**双向**判定：新增死登记 → 红；基线项已销项却未移除 → 也红）+ `DEAD_CODE_BASELINE`（3 项均带原因与销项触发）+ `test_dead_code_ratchet_selfcheck` | 负向探针（**内存级、不改业务文件**）：真实集合 = 基线 → 绿；模拟新增 `ZZZ_PROBE_999` → 基线外新增 `['ZZZ_PROBE_999']` → 红 ⇒ **非空转**。**刻意不删除这 3 枚**：删除前需确认无外部消费方（客户端/文档可能按码写分支），故留「基线冻结 + 销项触发」而非贸然删码 |
| **D07-13** 画像 `validate()` 硬编码维度数 | ⚠️ **改判为非缺陷**（见 §9.3 第 5 条） | 实读：`validate()` 仅测试调用、生产 `get_schema()` 不调它 ⇒ 属**测试期契约断言**；51/193 即规格契约本身 | 保留，不修（理由已入 §9.3） |
| **B10-e** filesize / dup 基线**僵尸豁免清算**（D14-4 的另一半） | ✅ 完成 | 原状：白名单只拦"新增"，**从不检查条目是否已失效** ⇒ 文件被删/改名/拆分到阈值内后，豁免变成**永久白条**，基线会慢慢烂成万能豁免池（D14-4 记录的一半）。现实现：`_filesize_findings` 对 `set(allowlist) - 仍超阈集合` 报 **CRITICAL**；`_dup_findings` 对 `per_file` 中**已归零**的条目报 **CRITICAL**；存量**下降**（仍超阈但变小 / total 变小）报 **WARN/INFO 要求同步下调基线**（棘轮只许收缩，缩了要记账） | 先对账确认基线与现状**逐条一致**（6 条 filesize 全 `OK`、21 条 dup per_file 全 `OK`、total 44=44）⇒ 加清算**不引入误红**；双负向探针：①`client` 阈值 800→3000 ⇒ **6 条 CRITICAL 僵尸豁免**、EXIT=1；②`dup per_file` 塞不存在文件 ⇒ **1 条 CRITICAL**、EXIT=1；基线**按原文精确还原**（断言通过）。基线态 `all` → 无 CRITICAL |
| **B10-d** 轴 4 `EXPORT_RE` **假阴性**（⚠️ **深审 249 条未涵盖的新发现**） | ✅ **修复 + 棘轮化** | 根因：`^\s*export` 的 **`\s` 含换行** ⇒ 匹配点落**前导空行** ⇒ 「声明行自身不算引用」的排除**失配** ⇒ **每个导出把自身计成 1 处引用** ⇒ 真零调用导出被降级进 `low`、**永不 CRITICAL**。实测：298 导出中 **56 个**声明行偏移 1 行；`zero_cap` 由「应为 4」被掩盖为 **0** ⇒ **轴 4 长期假绿**。修为 `^[ \t]*`（只吃横向空白）。修正后**露出 4 枚真零调用能力导出**：`AuthError`(`client/utils/auth.uts:51`)、`getRecorder`/`lastTempFile`(`voice.uts:104/108`)、`stopPeriodicSync`(`sync_client.uts:584`)；后一枚即深审 **D08-10**，前三枚为**新发现**（深审未涵盖 → 249 条计数应在下轮修正为 +3）。**不删除**：属客户端 `.uts`，接线/删除须编译门（不可用），故按 B1 口径**基线冻结 + 只拦新增 + 清算僵尸豁免** | 负向探针①（**前导空行**追加新导出）→ `[CRITICAL] **新增**零调用的能力导出（1 个）` @ `shell_state.uts:26`（**行号正确**）、EXIT=1；探针②（基线塞入不存在条目）→ `[CRITICAL] 僵尸豁免`、EXIT=1；两探针均已还原。常规态：`无**新增**… ✓` + `存量 4 个（基线冻结）` + `all` 无 CRITICAL |

| **B10-f** 轴 4 **覆盖面补全**（D14-16 的导出面/目录深度部分） | ✅ 完成 | 两处盲区：① `audit_exports` 原用 `utils_dir.glob("*.uts")`（**非递归**）⇒ `client/utils/agg/**` 等**子目录整体不在扫描面**；② `EXPORT_RE` 只认 `function|const|let|class` ⇒ `export type/interface/enum/default` 全漏扫。修为 `rglob("*.uts")` + 正则补 `(?:default\s+)?` 与 `type|interface|enum` | 先量化：补递归 **+29 个导出**（298 → 327），其中零调用 **1** 个＝`WALK_SPEED_MS`(`client/utils/agg/agg_config.uts:19`，**与深审 D04-12 相互印证，非新发现**）；再补 type/interface/enum **+5 个导出、零调用 0** ⇒ 不影响结论但消除盲区。修后导出 **332 个**、`无**新增**… ✓`、存量 **5** 个（基线新增 `WALK_SPEED_MS` 一条）；**子目录探针**（往 `agg_config.uts` 追加零调用导出）→ `[CRITICAL] 新增` @ `:31`（**行号正确**）、EXIT=1，已还原；`all` 无 CRITICAL |

| **B10-g** 契约快照**再生脚本 + 一致性断言**（D14-7） | ✅ 完成 | 原状：`docs/openapi.json` 的再生是**人工步骤**（无脚本、无断言）⇒「文档里的契约」是否真等于「代码里的契约」**无法证明**；轴 1 只做**路径级**对拍，字段级漂移（schema/参数/状态码/描述）看不见。新增 `scripts/gen_openapi.py`（写入 / `--check` 只校验），接入 `review_agent` 为 **`openapi_snapshot`** 检查（退出码 0=一致放行、1=过期**阻断**、2=后端环境不可导入**降级放行**，与轴 1 降级口径一致） | 关键发现：**现有快照与后端实时导出逐字节一致**（`--check` → `[OK] …一致（239518 字符）`）⇒ 从无漂移，但此前**无任何机器能证明这一点**。负向探针（往快照塞幽灵路径 `/api/v1/__probe_ghost__`）→ `[FAIL] …已过期` + 精确定位「快照有而后端无（1）」、EXIT=1；`review_agent` → `[❌] openapi_snapshot` 阻断；用再生脚本还原后与 **HEAD 逐字节一致**（`git status` 干净）、`--check` → 0。常规态 `review_agent` → `[✅] openapi_snapshot` |

| **B10-h** 门禁**静默转绿**与**双阈值**（D14-19 / D14-8） | ✅ 完成（含 1 项**待环境验证**） | ① **D14-19（假绿同族）**：`code==127`（ruff 缺）与 pytest `No module named` 此前**返回 True + "[skip]"** ⇒ 报告全绿而 lint / 全量测试**从未执行** → 改为**默认阻断**（附安装指引），仅在显式 `--allow-missing-tools` 时**可见放宽**（带 `[放宽]` 标记）；② **D14-8（双口径）**：覆盖率阈值此前 本工具硬编码 `50` vs CI `--cov-threshold 60` ⇒ 「本地 --full 绿」**≠**「CI 绿」→ 收敛为单一常量 `COV_THRESHOLD = 60`（取 CI 值，**本地不得比 CI 松**） | 内存级探针（monkeypatch `run`，**不改任何文件**）：`COV_THRESHOLD=60` 且**实际传给 test_agent 的 argv 就是 60**；缺 ruff / 缺 pytest **默认 `ok=False`**、`allow_missing=True` 时 `ok=True` 且首行含 `[放宽]` ⇒ 不再静默。⚠️ **未验证项（如实登记）**：阈值 `50 → 60` 后**实际覆盖率是否 ≥60 无法在本机确认**（Docker/Qdrant 未起 ⇒ 全量测试跑不了）；CI 本就以 60 为准，故本改动方向是"消除本地与 CI 的口径差"，但**须在 Docker 可用后跑一次 `--full` 确认** |

| **B10-j** 轴 2 **注释判定**假阳性/假阴性（D14-14 / D14-15） | ✅ 完成 | 原实现用「**本行前缀里有没有 `//`/`<!--`**」判注释（行级近似）⇒ ① **块注释** `/* */` 内（该行无 `//`）的页面引用被当**死路由** → 误报 CRITICAL；② 多行 `<!-- -->` 同样漏判；③ `.ts` 残留扫描**完全不看注释** ⇒ 注释里写「原 `'./auth.ts'`」也报 CRITICAL；④ **反向假阴性**：字符串里 `http://x` 之后的内容被判为注释 → **真死路由漏报**。改为**字符级注释区间**扫描 `_comment_ranges()`（并跳过字符串字面量）＋ `_in_comment(offset)` 精确判定，②③ 均改用它 | 纯函数对照探针（**不碰文件**）六例：块注释 A / 多行 HTML 注释 C / 块注释内 `.ts` D —— **旧判 `False`（会误报 CRITICAL）→ 新判 `True`**；字符串 `http://` F —— **旧判 `True`（漏报）→ 新判 `False`**；真死路由 E 两侧均 `False`。**文件级负向探针**：追加真实代码内死路由 → `[CRITICAL] 代码内引用未注册页面（死路由，1 条）` @ `shell_state.uts:25`、EXIT=1，已还原（文件洁净）。轴 2 常规态 **无 CRITICAL**（`无代码内死路由 ✓` / `pages.json 16 条均有物理文件 ✓` / `无 .ts 残留引用 ✓`） |

| **B10-k** 退出码口径**统一**（D14-18） | ✅ 完成（`review_agent` 侧；`test_agent` 仅登记） | 测绘：`audit_harness` / `check_schema_drift` / `gen_openapi` 用 **2 = 环境错误**，而 `review_agent` / `test_agent` **只有 0/1** ⇒ 「环境没配好」被**误报成「代码违规」**（归因错误）。现 `review_agent` 落地统一口径 **0 通过 / 1 违规 / 2 环境错误**：新增 `ENV_ERR_PREFIX` 给"环境错误型"检查打标；抽出**纯函数** `_classify_failure()` 做分流（**违规优先 → 1**，仅环境错误 → 2）；报告新增 `env_blocked_checks` 字段 | 纯函数探针 **5 例全对**（全通过 `([],[])` / 仅违规 `([],['lint'])` / 仅环境错误 `(['lint'],[])` / 违规+环境错误 → 违规优先 / 含 lessons）；`check_lint`/`run_tests` 的 env 分支消息**均以 `[环境错误]` 开头**；CLI 实跑 `✅ 审核通过` 且退出码 **0**。**残余（已如实登记）**：`test_agent` 仍只产 0/1（未改——避免未经验证的跨工具契约变更），已在其 docstring 加口径交叉引用 |

| **B10-l** 密钥模式集**唯一来源**（D14-9） | ✅ 完成 | 原状：`review_agent`（提交门禁）与 `audit_security`（安全审计）**各维护一套**模式且**互有缺口**——审计认得 `AKID…`（腾讯云）/`\bsk-…\b` 宽版/私钥宽版，提交门禁**看不到**；提交门禁认得 `ghp_/sk_live_/glpat-` 等，审计不认 ⇒ **同一份密钥可能被其中一个放行**（假安全感）。新建 `scripts/secret_patterns.py` 作为**并集唯一来源**，两处共同 import；配套新增**显式行内豁免** `pragma: allowlist secret`（detect-secrets 惯例）——刻意**不**用"整目录跳过 tests/"（那会把真密钥一起放过） | 探针（**不落盘**）：共享模式 **16** 条；`review_agent.SECRET_PATTERNS` 与 `audit_security._SECRET_PATTERNS` **同为该来源**；**并集相对合并前两套缺失模式 = `[]`**（无覆盖率下降）；四类形态（腾讯云 AKID / DashScope 宽版 / 私钥宽版 / GitHub PAT）**均被提交门禁识别**。**真实误报被当场暴露**：放宽后在 `backend/tests/test_guard_managed.py` 命中 4 处 `sk-fake-key-…`（合成值）→ 逐行加 `pragma: allowlist secret` 标注；复跑 `--full --skip-tests` → **secrets ✅ 且静态全绿**；受影响测试 **9 passed** |
| **B10-m** `audit_security` **直接崩溃 + 路径白名单失效**（既有缺陷，本轮坐实并修复） | ✅ 完成（**暴露 1 项真实运维发现**） | ① **崩溃**：`db/models` 早已由单文件**拆为包**，`:92` 仍按文件直读 ⇒ `FileNotFoundError` ⇒ **该安全审计根本无法跑完**（用 `git show HEAD:` 原始版本复跑同样崩 ⇒ 既有缺陷，非本轮引入）；修：`_models_source()` 兼容包/单文件（递归拼接）。② **白名单意图从未生效**：`_ALLOW_PATHS` 用**仓库根相对**前缀（`tests/`…），而扫描对象是 `backend.rglob` ⇒ `backend/tests/…` 不匹配 ⇒ "排除测试与示例"落空（实测把 `backend/tests/` 两处合成值报成"硬编码密钥"）；修：`_allowed()` 改**按路径段**匹配。③ 与提交门禁**统一合成值抑制口径**：`change-me` / 含 `mock` / 显式 `pragma: allowlist secret` | 首次真正跑通：`key_management` ✅（源码无硬编码密钥）/ `transport` ✅✅ / `storage` ✅✅ / `backup` **❌**。⚠️ **真实发现（交运维/用户）**：**最近备份距今 493.0h（≈20.5 天）**，违反 RPO≤24h —— 此前该检查因崩溃**从未被评估**（D13-4 记的"RPO 检查恒通过"实为"压根没跑"）。`blocking` 由崩溃前的"跑不完"变为 **1** 条（且是真实项） |

| **B10-n** UTF-8 兜底**同语义 20 处 → 单一实现**（D14-11 / D14-12 姊妹项） | ✅ 完成 | 原状：把 stdout/stderr 切到 UTF-8（Windows GBK 下 `✓`/`✅` 会 `UnicodeEncodeError` 直接崩）这 2~4 行，在 `scripts/*.py` 里**抄了 20 处 / 16 个文件**，且**形态四分五裂**：① `if hasattr(sys.stdout, "reconfigure")` 成对守卫；② 仅 stdout；③ 函数内同款；④ `__import__("sys").stdout` 变体；⑤ `for _stream in (...)` + `contextlib.suppress`（audit_harness）；⑥ **无守卫裸调用**（warm_hf_models）⇒ 想加一层兜底（如顺带设 `PYTHONIOENCODING`）得改 20 处且必漏。新增 `scripts/gate_io.py::force_utf8()` 作为唯一实现（语义为各原处的**安全超集**：stdout+stderr 都处理、无 `reconfigure` 时静默降级），16 个文件全部改为 `force_utf8()` | 迁移后：**`scripts/` 全目录 ruff `All checks passed`**；**17 个改动脚本 `py_compile` 全过**；**残留 `reconfigure` 仅剩 `gate_io.py` 自身**（grep 实证）。门禁工具实跑：`review_agent` ✅ 通过 / `audit_harness all` 无 CRITICAL / `lessons recent` 正常；10 个数据类脚本中文输出**正常无导入错误**（`--help` 场景各自因缺数据退出属其自身语义，非本次改动） |

| **B10-o** 退出码口径**单一来源** + `test_agent` **姊妹假绿**（D14-18 残余 / D14-19 姊妹 / D14-13 半） | ✅ 完成 | ① 口径与判定函数抽到 **`scripts/gate_exit.py`**（`ENV_ERR_PREFIX` + `classify_failure()`），`review_agent` 与 `test_agent` **共用**（此前一方持有、一方缺失 ⇒ 同一"跑不了"语义不一致）；② 顺带发现并修 **`test_agent` 的姊妹假绿**：pytest/pytest-cov 缺失时 `return True, "[skip] 缺依赖"` ⇒ 报告全绿而**全量测试从未执行**（与 `review_agent` 缺 ruff 同族）→ 改为**环境错误**（统一前缀 ⇒ 退出码 **2**）默认阻断；③ `test_agent` 新增 `env_blocked_sections` 报告字段 + **0/1/2 三态**退出；④ `audit_harness` 内 `{"unpackage","node_modules",".hbuilderx"}` **硬编码 2 次** → 收敛为 `CLIENT_EXCLUDE_DIRS`（**D14-13 半**） | 探针：共享前缀/判定被两处共用（`R.ENV_ERR_PREFIX == GE.ENV_ERR_PREFIX`、`T.gate_exit is GE`）；`classify_failure` **4 例全对**（含"违规优先"）；`R._classify_failure` 与共享实现**等价**；**源码断言** `test_agent` 不再含 `[skip] 缺依赖` 且已改为环境错误。`scripts/` 全目录 ruff **通过**；`review_agent` ✅ / `test_agent --help` ✅ / `audit_harness all` 无 CRITICAL。**残余（如实登记）**：`D14-12` subprocess 双实现（签名差异 + OOM 提示分支）、两工具**跨文件**目录排除清单仍各持一套、`D14-22` 报告 schema 未统一、`D14-23` CI schema-drift job 仅 schedule |

| **B10-p** 修 **B10-n 引入的导入回归**（Docker 起后全量门禁当场抓到） | ✅ 完成 | **回归**：B10-n 把样板抽成同目录 `gate_io` 后，`agg_generate_photos` / `agg_load_real_photos` / `run_wer_bench` / `build_truth_corpus` / `gen_agg_fixtures` **这 5 个会被后端按包导入**（`from scripts.x import …`）的脚本在该路径下 **`sys.path` 无 `scripts/`** ⇒ `from gate_io import …` 抛 `ModuleNotFoundError` ⇒ **pytest 收集中断**（`test_event_aggregation_scripts`）+ **research 段崩**。修：这 5 个改为**双路径导入**（`try: from gate_io … except ModuleNotFoundError: from scripts.gate_io …`） | ①「按包导入」冒烟：五处 `from scripts.x import …` **全部成功**；② 受影响测试 `test_event_aggregation_scripts` + `test_cli_scripts` → **21 passed**；③ `test_agent --only research` → ✅；④ `scripts/` 全目录 ruff 通过。**教训（已登记）**：`py_compile`/`ruff` **查不出导入期缺模块**，抽共享件必须枚举"直跑 vs 按包导入"两种路径并做真实 import 冒烟 —— 此前 B10-n 的"17 脚本 py_compile 通过"是**不充分证据**（如实更正） |

| **B10-q** 轴 4 **注释计数假阴性**（清理 B10-d 残层 · 由 B5b 当场暴露） | ✅ 完成 | 根因：`audit_exports` 引用计数用裸 `\bname\b` **全文本**匹配、**不剔注释** ⇒ 「注释里提到导出名」也算 1 处引用 ⇒ 真·零调用导出被**注释掩盖**（B10-d 只修了「`\s` 含换行致声明行偏移」那一层，此为同族假阴性的残余层）。**暴露路径**：B5b 生成的模块 doc 头含「对外导出：…」⇒ `stopPeriodicSync` 凭空多 1 引用 ⇒ 被 B10-e 的僵尸豁免检测报 CRITICAL。修：复用轴 2 既有的 `_comment_ranges()`/`_in_comment()` 剔除注释区间内的匹配（口径与 `audit_client` 一致）；顺带把「按名重读每份源文件」改**预读缓存**（原为 导出数 × 文件数 次 `read_text`） | **先量化**（`.cowork-temp/probe_exports_comments.py` A/B 对照，不落盘）：排除注释后零调用能力导出 **4 → 9**，新露出 **4 枚真死码**＝`AGG_CHECK_ON_DEVICE`(`config.uts:72`)/`invalidateTimelineCache`(`timeline.uts:244`)/`isIgnoredEvent`(`event_ops.uts:196`)/`parseErrorString`(`api.uts:537`)（逐枚全仓 grep：仅 docs/注释提及、代码零引用 ⇒ 已核）；按棘轮口径**冻结进 `zero_cap_allowlist`** 并登记销项触发；`stopPeriodicSync` 的 `place` 随 B5b 更新为 `sync_schedule.uts:48`。**反向探针**（临时 `client/utils/_probe_b10q_tmp.uts`：导出仅在自身 doc 注释被提及）→ **A 现实现 `zero_cap=4`（漏判）／B 修正后 `zero_cap=10`（捕获）**，门禁实跑 `[CRITICAL] **新增**…probeZeroCapB10Q` @ `:6`、EXIT=1，已删探针。常规态：`无**新增** ✓ / 存量 9（基线冻结）/ 无 CRITICAL`；`ruff` All checks passed |

| **B9a** 事件域门面收口（D04-16） | ✅ 完成 | 原状：`api/events.py:100` 直接 `from app.services.event_aggregation.pipeline import l3_lifecycle` ⇒ **API 层越级直连算法包**（跨层反向依赖）。修：`l3_lifecycle` 由 `services/events/timeline.py` 再导出（该模块职责本就含"L3 生命周期读取时派生"），`events/__init__` 加入导入与 `__all__`，API 只依赖 `app.services.events` 门面。依赖方向变为 events 域 → 算法包（同域 service→algorithm，正确）；已核 `agg_candidates` 仅依赖同包 `agg_types`/`st_dbscan`、`pipeline` 不反向依赖 events ⇒ **无环** | `44a837c`；ruff 通过；`pytest test_event_sync + test_agg_reference` → **23 passed**；**grep 复核 API 层对 `event_aggregation` 的直连 = 0** |
| **B9b** rag 的 LLM 调用全部走 `llm_ops` 门面（D05-13） | ✅ 完成 | 原状：rag 越级直连 `external`，把专为该层设立的 `llm_ops` 门面架空（`rag/rewrite.py:15` 的 `rewrite_query`、`rag/image.py:44,52` 的 `image_caption`）。修：`llm_ops/base.py` 新增 `image_caption` 转发（`prompt=None` 时不传第二参以**沿用 dashscope 默认提示词**，避免字面量漂移；用**属性访问**转发 ⇒ 测试对 `dashscope` 模块的打桩仍生效），`llm_ops/__init__` 导出；rag 两处改从 `llm_ops` 导入 | `32b4a89`；ruff 通过；`pytest test_image_search + test_pipeline + test_external + test_ba3_ai_chain + test_rag` → **79 passed**（15 deselected）；**grep 复核 rag 对 `external.dashscope` 直连 = 0**。**登记未改**：rag 另有 3 条**非 LLM** 的同层适配器引用（`filter_sensitive_rule`×2 = 内容安全规则；`content_urls`×1 = 媒体 URL 签名）——不属 `llm_ops` 职责，未强行塞入；收敛需另立**内容安全域/媒体域门面**（架构决策） |
| **B9c** 存储后端**注册表** + COS **唯一构造点**（D02-3 / D02-13） | ✅ 完成 | ① 原状"新增后端需改 **7 处**"（注册表 + `_FAKE/_MINIO/_COS_INSTANCE` 三个全局 + `reset` + `get` 的三段 `if key ==` + config 字面量 + env 模板）→ 引入 `BackendSpec`（`factory`/`singleton`/`guard`）注册项：**新增后端只改 `_BACKENDS` 1 行**；三个单例全局收敛为**一个** `_INSTANCES`（fake 由"导入期即建"改**懒建**，构造仅内存字典 ⇒ 语义等价）。② COS 客户端原在**两处**各自构造（`CosStorageBackend.__init__` 与 `tencent_ci._client`，参数与校验相同、仅文案不同）→ 新增 **`build_cos_raw_client()` 唯一构造点**；`tencent_ci` **刻意保留自身前置校验与对外文案**（该文案被 `docs/项目API密钥清单与获取.md` 引作 api_smoke 降级警告口径 ⇒ 严格行为等价）。③ `api/upload.py` 的 `_cos_sts_configured()` **直读 COS 私有配置字段**（API 越界读存储内部）→ 归位为 `storage.cos_sts_configured()`，API 侧实现与字段直读删除 | `8286327`；ruff 通过；**探针 8 项断言全过**（fake 单例 / fs 每次新建 / 未知后端 ValueError 文案不变 / reset 后为空新实例 / **fake 容量守卫仍生效** / COS 唯一构造点·源码断言 / **API 层不再直读 COS 私有字段** / `cos_sts_configured` 可用）；`pytest test_storage_backends + test_upload + test_content_upload + test_cleanup_job` → **83 passed**。**残余（登记）**：`config.py` 的 `storage_backend` Literal 与 `deploy` env 模板仍各 1 处——属**部署面显式校验**，去重会让"配置写错"从启动期报错退化为运行期报错，故保留 |

| **B5a** `client/utils/play.uts`（653 行 / 6 域混居）按域拆分（L-04） | ✅ 完成 | 原状：`play.uts` 653 行、30 个顶层符号、横跨**6 个域**（回响 / 访谈 / 消息 / 收藏 / 垃圾箱 / 内容AI·波形·用户·备注），是"万能玩法层"——单点改动必须读 650 行。修：**纯移动**为同目录 6 个域模块（`play_echo` 70 / `play_interview` 125 / `play_messages` 182 / `play_favorite` 53 / `play_trash` 63 / `play_content` 181 行，导入按各域**实际用到的符号**裁剪），**10 个调用点**改为按域直接导入。⚠️ **UTS 平台限制（本轮实测坐实）**：`client/utils/*.uts` **不支持** `export { … } from './x.uts'` 再导出（Rollup 把目标 `.uts` 当 JS 解析 ⇒ `[plugin:uts] Expression expected ... need plugins to import files that are not JavaScript`）⇒ 原计划的"门面兼容再导出"**不可行**，改用调用点直接指向域模块（`@/utils/play_<域>`）。**度量**：`play.uts` **653 → 0 行（删除）**，单文件最大 **182 行**（阈值 800 内） | ① **逐字节等价证明脚本**：6 个模块的代码体与 `git show HEAD:client/utils/play.uts` 对应切片**逐行一致**（60/115/173/45/53/168 行全 OK）；**导出全集 28 = 28，缺失/多出均为 `[]`**；两个私有 helper（`flattenDimensions`/`patchJson`）仍在各自模块且未外泄 ⇒ **纯移动成立**；② 残留引用 `utils/play'` grep = **空**；③ **冷编译 `项目 client 编译成功`（ready in 85951ms，0 error）**；④ 教训已登记（UTS 再导出限制 + 我脚本误取头部 22 行致门面混入截断导入块） |

| **B5b** `client/utils/sync_client.uts`（705 行 / 22 导出）按职责拆分（L-05） | ✅ 完成 | 原状：`sync_client.uts` 705 行，同步域全部内聚（共享常量 + 结果 DTO / 入队 API + 暂停失败计数 / 游标 + 本地镜像 / push·pull·对账管线 / 2h 定时·后台任务·初始化）。修：**纯移动**为同目录 5 个模块（`sync_types` 77 / `sync_queue` 84 / `sync_local` 86 / `sync_pipeline` 291 / `sync_schedule` 101 行），**6 个调用点**改为按模块直接导入（`App.uvue` 保持**相对路径** `./utils/…`，其余用 `@/utils/…`）。**度量**：`sync_client.uts` **705 → 0 行（删除）**，单文件最大 **291 行**（阈值 800 内） | ① **逐字节等价证明**：5 模块体按**原位置**排序拼接 == `git show HEAD:client/utils/sync_client.uts` 63–705 行（非空行 **639 逐行一致**；仅丢弃 4 个段间空白行 + 1 个原导入行，脚本已断言**非空白行零丢弃**）；**导出全集 22 == 22**。② **表面增量（如实登记）**：跨模块共享符号由私有改 `export`——`sync_types` **6 常量** + `sync_local` **4 函数**（`readCursor`/`writeCursor`/`applyChanges`/`mirrorSnapshot`，被 `sync_pipeline` 的 pull 段调用）⇒ 仅**扩大导出面**、不改任何行为。**此点即险**：首版脚本给 `sync_local` 空导出（且导出器只认 `const` 不认 `function`）→ `sync_pipeline` 引用未定义符号、必编译失败；已修为「常量/函数声明皆可导出」并复跑落盘。③ 残留 `from '…sync_client'` **import 引用 = 0**（余者皆注释 / `README.md` 文字，属声明漂移·后置）。④ **冷编译 `项目 client 编译成功`（ready in 170871ms，0 error）** |

| **B5c** `client/utils/uploader.uts`（628 行）按**文件自带分节**拆分（L-06） | ✅ 完成（**结构 + 冷编译**；真机上传待设备） | 原状：628 行，上传域全部内聚（常量·类型 / 断点续传 / 流量约束 / held·failed 队列 / 单张上传 / 并发池与批量入口）。修：**纯移动**为同目录 6 个模块（`uploader_types` 58 / `uploader_pending` 55 / `uploader_net` 59 / `uploader_queue` 120 / `uploader_photo` 93 / `uploader_batch` 185 行），**8 个调用点**按模块直接导入（`event_ops`/`play_*` 保**相对路径** `./uploader_net`）。**度量**：`uploader.uts` **628 → 0 行（删除）**，单文件最大 **185 行**（阈值 800 内） | ① **逐字节等价证明**：区间**连续覆盖 59–628（缺失 `[]`）**；拼接（取原行）**逐行一致**（非空行 **516 vs 516**）。② **导出面改由脚本自动推导**（不再手工登记 ⇒ 结构上规避 B5b 的"漏算被引用的私有符号"坑）：自动算出跨模块引用 → **13 个原私有符号自动提升 `export`**（`PENDING/HELD/FAILED_KEY`、`is4xx`、`findPending/savePending/clearPending`、`resolveMode`、`addHeld/removeHeld/addFailed/removeFailed`、`uploadWithRetry`）；**原有 14 个 export 全部保留（不删任何既有导出）**，导出总数 **27**。③ 残留 `from '…uploader'`（精确路径）**= 0**（余者皆注释/README 文字）。④ **冷编译 `项目 client 编译成功`**（51s，0 error）。⑤ **唯一语义细节（已分析并登记）**：原文件**末尾**的 `onNetworkRestored` 钩子（模块加载期注册）随其唯一消费者 `maybeUploadHeldOnWifi` 落入 **`uploader_batch`** ⇒ 钩子注册时机由"uploader 首次加载"变为"uploader_batch 首次加载"；**启动链 `pages/shell/shell → <TabIndex> → import { uploadBatch }`（首页 tab）** 仍保证开屏即注册 ⇒ **行为等价**（旧链路里由 `event_ops`/`detail` 等仅取 `getNetKind` 触发的**更早注册**被取消，但那些时刻尚无待传队列，钩子无实际作用）。⑥ **真机上传未跑**（`adb devices` **无设备**）⇒ 标注**待补验**，不以"冷编译通过"冒充真机验收。⑦ **轴 5 附带读数（如实登记）**：拆分把调用点的单条 `import` 变 2 条 ⇒ `RecordSheet.uvue`/`TabIndex.uvue` 各 **+1 行**（Python 精确核：2208→2209 / 1741→1742），触发轴 5「超阈且增长」**WARN（非阻断）**；另实测 `TabIndex`(1740)/`detail`(1165)/`favorites`(974) 的基线本就**低于实际 1~2 行**（HEAD 即已如此，**先于本轮、非本轮造成**）——按「棘轮只许收缩」**不擅自上调基线**，待 B5.2d 拆分时一并销项（这四个文件正是 B5.2d 目标）|

| **B5d（金丝雀）** `client/pages/detail/detail.uvue` **样式外置**（L-08） | ✅ 完成（结构 + 冷编译 + **产物取证**） | 原状：1167 行（template 143 / script 429 / **style 593**），L-08 既定成因就是样式膨胀。修：`<style>` 块**纯移动**到新文件 `client/styles/detail.css`，uvue 侧只留 3 行 `<style>` / `@import '@/styles/detail.css';` / `</style>` ⇒ **1167 → 577 行（<800，轴 5 销项）**。**关键＝先做能力前置验证**（吸取 `utils` 再导出不可行的教训）：最小探针实测 **(a)** `.uts` 组合式函数（`import { ref } from 'vue'` + `export function useXxx()`）**可**被 `<script setup lang="uts">` 引入并编译通过；**(b)** ucss **支持** `<style>` 内 `@import '@/styles/x.css'`——**无需 scss 插件**（本机 HBuilderX `plugins/` 无 scss/sass 编译插件，本仓此前亦无 `lang="scss"`/`@import`/`.scss` 先例）| ① **等价性**：`detail.css` 内容 == 原 `<style>` 内容 **591 行逐行一致**（非空行 591 vs 591）；② **冷编译 `项目 client 编译成功`**（0 error）；③ **产物取证（不止"编译通过"）**：编译产物 `client/unpackage/dist/*/app-android/bytes/GenPagesDetailDetailSharedData.style.bytes` 中可检索到 `detail.css` 的真实类名 `page-root`/`detail-scroll` ⇒ `@import` 被 ucss **真正处理**（而非静默丢弃）；④ 轴 5 条目**销项**：已从 `scripts/audit_harness_baseline.json` 删除 `client/pages/detail/detail.uvue`（若忘删将由 B10-e 报僵尸豁免 CRITICAL），删后 `audit_harness all` **无 CRITICAL**。⑤ **本文件未做 script 侧 composable**（其成因是样式；composable 机制已由探针证明可行，留给 RecordSheet/TabIndex）；**视觉复验待真机** |

| **B5.2e（样式半）** `RecordSheet.uvue` / `TabIndex.uvue` **样式外置**（L-01 / L-02 的样式半） | ✅ 完成（结构 + 冷编译 + 产物取证）| 沿用 B5d 已验证的机制（`<style>` **整块纯移动** → `client/styles/*.css`，uvue 侧 `@import`）：`RecordSheet` **style 858 → `record-sheet.css`**（2209 → **1354 行**）；`TabIndex` **style 680 → `tab-index.css`**（1742 → **1065 行**）。两者仍 >800（script 1097 / 851 未动）⇒ **保留 baseline 条目并下调计数**（2208→1354 / 1740→1065，棘轮收缩记账）| ① 等价性：两 css 内容与原 `<style>` **逐行一致**（非空行 846 vs 846 / 655 vs 655）；② **冷编译 `项目 client 编译成功`**（0 error）；③ **产物取证**：`GenComponentsRecordSheetRecordSheetSharedData.style.bytes` 含 `scrim`/`grab`、`GenComponentsTabIndexTabIndexSharedData.style.bytes` 含 `header-title` ⇒ `@import` 被真正处理；④ `audit_harness all` **无 CRITICAL**（两文件的"超阈且增长"WARN 随计数下调消失）；⑤ **未做**：script 侧 composable（**需真机验收，`adb` 无设备**）⇒ **L-01/L-02 未闭环**；⑥ 残留（**先于本轮**、非本轮造成）：`favorites.uvue` 基线 974 vs 实际 975 的记账漂移——按「棘轮只许收缩」**不上调基线**，保持 WARN 如实提示 |

### 8.4 B10-i 门禁自身可靠性收口（2026-09-24 · 第六轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **D14-23** CI `schema-drift-weekly` **永绿**（`continue-on-error: true` + 仅 schedule） | ✅ 修复 | **去掉 `continue-on-error`**：本 job 仍**仅** `schedule`/`workflow_dispatch` 触发（**不拖慢 push/PR**），但**定时检出漂移即红 = 告警信号**——口径与同文件 `pip-audit-weekly`（"发现即失败、红色定时 run 即告警"）**统一**；三处注释同步（`on.schedule` 说明 / job 头 / step 头） | 与 **D13-1**（client tsc 恒绿）**同族**：CI 里"**不可能变红的门禁**"。本地无法执行 CI ⇒ 证据＝`yaml.safe_load` 解析通过（jobs 4 个）+ 该 step 已无 `continue-on-error` + `if` 仍限定 `schedule/workflow_dispatch`（语义读码）；**无本地可跑的等价探针**（如实登记） |
| **D14-22** 机读报告 **schema 不统一** + `write_text` 无兜底 | ✅ 修复 | 新增 **`scripts/gate_report.py`**（`SCHEMA_VERSION=1`、`COMMON_KEYS`、`enrich()`、**带兜底** `write_report()`）；三工具接入：`audit_harness --json` 由 `{critical,info}` 改为**统一信封**（补 `tool`/`passed`/`generated_at`/`schema_version`/`critical_count`）；`review_agent`/`test_agent` 改走 `write_report`——**自有键一律保留**（`details`/`blocking_checks` 等，CI 消费不受影响） | **探针三例**：① `audit_harness filesize --json` 产物键含 `schema_version/generated_at/passed`（`COMMON ok=True`）；② 两工具实跑后 `review-report.json`/`test-report.json` **均含三公共键**且自有键齐全；③ **异常兜底**——`--json "scripts/audit_harness.py/x.json"` → 打印 `[WARN] 机读报告写入失败（FileExistsError…）`、工具**不崩**、`EXIT=0`（**原实现会崩**）。`ruff scripts/` 全绿 |
| **D14-12** subprocess 包装 **双实现**（超时/env/OOM 分支各异） | ✅ 修复 | 新增 **`scripts/gate_proc.py`** 单一实现（UTF-8 解码 / `FileNotFoundError`→127 / `TimeoutExpired`→124 / `returncode<0`→OOM 提示）；`review_agent` **删除本地 `run`** 改用共享（超时缺省仍 300）；`test_agent` 的 `run` 收敛为**仅绑定自有差异**的 2 行委托（`timeout=600` + 含 `PYTHONPATH` 的 `base_env`） | **探针**：`gate_proc.run` 缺命令 → **127**、超时 → **(124,'timeout')**；**源码断言**——`review_agent` 已无本地 `def run`、`test_agent` 已无 `subprocess.run(`、委托 `_gate_run(..., timeout=600)`；两工具实跑 ✅。**登记**：OOM 提示对 `review_agent` 属**诊断增强**（仅 `returncode<0` 罕见分支多出文案，返回码语义不变） |
| **D14-6** 轴 1 只做**路径级**对拍（字段级漂移在覆盖外） | ✅ **判定已被覆盖**（无需新增代码） | 用审计**自己的判据**做反向探针：临时给响应模型 `SyncPushResult` 加一字段 → **轴 1 仍报"路径与方法集完全对齐 ✓ / 无 CRITICAL"**（**证实路径级确实盲**）；而 **`openapi_snapshot`（B10-g，字节级）→ `[FAIL] docs/openapi.json 已过期`，并明示"路径集合一致 ⇒ 差异在字段级"** ⇒ **字段级漂移已被 B10-g 覆盖**（`review_agent` 快/全量均跑）。探针**已完全还原**（`gen_openapi --check` 复绿、`git diff` 干净） | 轴 1 **保持路径级**（更廉价、职责不同：只增不减 + 幽灵路径）；字段级由 B10-g 字节级对拍补上。**刻意不新增代码**——避免为"已被覆盖的缺口"重复造门禁（与 B10 系列"先证明再动手"一致） |

### 8.5 B11 声明漂移残余收口（2026-09-24 · 第七轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **D05-9** rerank 模型名**三方不一致** | ✅ 修复 | 权威值＝`bge-reranker-v2-m3`（`config.reranker_model` 默认 + `backend/.env.example` + `deploy/.env.production.template` + `pull_models.sh` + MVP方案/B2 设计文档；`_diff_ledger.md` 早已记「**以 config 为准，不注释**」）。改 **5 处陈旧值**：`rerank.py` docstring、`rerank.py:57` fallback、`warm_hf_models.py:25`、`ci.yml:289` 注解、`docs/审计_未关闭缺陷_20260923.md` S3（**活登记**） | ① `rerank.py:57` **删除 fallback 字面量**（单点化到 config）——行为等价：`RERANKER_MODEL` 置空时两分支都走 `return None`（模型目录不存在 ⇒ 优雅降级；且 `backend/models/` 实为空）；② `pytest test_rag.py` → **31 passed**；③ 残留 `bge-reranker-base` 仅剩"删改说明"注释 |
| **D02-7** `photos/` 命名空间**两套键布局**（带/不带 `{yyyymm}/`） | ✅ 修复 | 新增 `backend/app/services/media_keys.py::photo_object_key`（**唯一布局** `photos/{uid}/{yyyymm}/{hex12}_{safe}`）；`upload/protocol._final_key` 改为委托；`photo_content` 的 multipart 路径由 `photos/{uid}/{hex32}{ext}` 改走同一布局（**保留扩展名**，content-type 派生依赖它） | ① 读取侧无需改（键按值存取 ⇒ 保留历史键读取）；② **唯一"解析键"处** `thumbnails.derive_thumbnail_key` 只把首段目录替换为 `thumbnails/`（prefix 级、**与布局无关**）⇒ 不受影响；③ 测试仅断言 `photos/` 前缀 ⇒ 无破坏：`test_upload + test_content_upload + test_photo_content + test_thumbnails + test_cleanup_job` → **80 passed**；④ `test_content_upload.py:81` 注释同步更正 |
| **D04-3** `AGG_CONFIG["night"]`/`["gps_speed"]` **写了无人读**（死配置 + 声明漂移） | ✅ 修复 | ① **`night` 接线**：`st_dbscan._iso_day` 改读 `AGG_CONFIG["night"]`（原硬编码 `23/30`，**值相同 ⇒ 行为等价**）；② **删除冗余键 `gps_speed`**（消费方 `agg_preprocess.py:14,17,68,72` 直接 import `WALK_SPEED_MS`/`DRIVE_SPEED_MS` 常量、从不读该键）；③ `agg_config.md` 两处同步更正（云侧键列改为常量名 / 注明 night 已真接线） | **非空转探针**（内存级，不落盘）：默认(23/30) → `23:45→前一天`、`22:45→当天`；**改配置为(22/30)** → `22:45` 也变前一天、`23:45` 变当天（**两值随配置翻转 ⇒ 确已接线**）；还原后恢复原值。`pytest test_agg_reference + test_event_aggregation_scripts + test_event_sync` → **31 passed**；ruff 通过。**⚠️ 附注（接线暴露的新发现，已登记）**：`agg_types.py:19 from …st_dbscan import Photo` 与 `st_dbscan → agg_types` 构成**模块级循环依赖**（既有结构问题）⇒ 本批用**函数内局部 import** 规避；循环本身未在本批重构，登记待后续 |
| **D02-9** 照片扩展名**三表不一致**（`.heif` 无 MIME 映射；`.gif` 仅 MIME 表有） | ⏸ **登记未改**（判定属**行为变更**） | 收敛必须二选一：改下发 MIME（`.heif`→`image/heif`）或改受理格式（放开/收紧 `.gif`）⇒ **两者都是用户可见/契约变更**，越出本波「行为等价」边界（ledger 原 §3 行亦已注明"改即改魔数判定，属行为"）| 已在 `media_url._CONTENT_TYPES`、`upload_meta.ALLOWED_PHOTO_EXTS`、`file_magic` 三处加**交叉说明**（谁是唯一来源 / 已知差异 / 为何不改）⇒ 漂移**已书面化**（不再是"静默不一致"）；修复移交**功能波**（台账 §5.10 拍板 10.1）|
| **D12-7** `uvue_gen/audit_report.md` 引用**不存在的对拍器**（死证据） | ✅ 修复（书面作废） | 该文件为 **git 跟踪的生成物**：所引 `scripts_verify_layout.py` **全仓 + git 全历史均不存在**，且其覆盖的老页面（`index`/`search`/`profile`/`ai/*`/`press`）**已整体退役** ⇒ 「561 元素超差 0」**不可复现、结论失效**。**在文首加失效声明**（4 条：对拍器不存在 / 对象已退役 / 不再作任何验收依据 / 现行像素验收走真机） | **保留原文**供历史对照（未删除证据）；`uvue_gen/**` 仍属域⑫「未覆盖」清单（生成物），本次仅就其**声明**作废 |

| **D09-10** `wechat_messages.status` **三方枚举/默认值不一致** | ✅ 修复（+ **新增防漂移门禁**） | 原状：ORM 注释 3 值（`processed/failed/deleted`）、**schema.sql 注释 2 值**（`processed/failed`）、`service.py` **实写 4~5 值**（`processing`/`processed`/`media_failed`/`sensitive`/`deleted`）；且 **DDL 默认 `'processing'` ≠ ORM 默认 `"processed"`**。修：在 **`db/models/wechat.py` 定义 `WECHAT_MESSAGE_STATUSES`（取值唯一来源）**；schema.sql 的 status **注释**改为指向该常量并注明差异（**未改 DDL 默认值**——改 DDL 属漂移迁移，按 §5.10 拍板 10.3③ 待专用窗口）；**新增门禁测试** `test_wechat_message_status_writes_are_declared`（扫描 `service.py` 的 `record.status = "…"` 与 `pg_insert(WechatMessage)…values(status="…")` 两种写入形态，断言 ⊆ 声明集合） | ① `pytest test_wechat.py` → **23 passed**；② **反向探针非空转**：临插 `record.status = "zzz_probe_d0910"` → 该测试 **FAIL** 且报错精确列出未声明取值（**探针还暴露了我首版正则 `[a-z_]+` 漏数字的盲点，已修为 `[a-z0-9_]+`**）；探针**字节级还原**（`git status` 干净）；③ 登记（**新发现**）：DDL 默认 `'processing'` 与 ORM 默认 `"processed"` 的**默认值分歧**仍在，属漂移迁移范畴 |

| **D07-14** 三套 dimensions 读路径（跨端） | ⏸ **登记未改**（判定属**行为/UI 变更**） | 实读澄清审计前提**部分不成立**：**云侧并不存在重复**——"展示口径"已单点化为 `profile_annotator.display_dimensions`（`interview.py` ×2 + 测试均复用；`export._profile_out` 是**原始结构化导出**、非展示口径，属另一关注点）。真正的缺口是**跨语言平行实现**：客户端 `play_interview.uts::flattenDimensions`（UTS，同语义但**用英文 dim id 作标签**）。D07-14 的目标（"**客户端不再泄漏英文 dim id**"）需先建立 **dim id→中文标签映射** ⇒ **UI 文案变更**，越出本波「行为等价」边界 | 已在 `display_dimensions` docstring 与 `flattenDimensions` 注释处**互相交叉说明**（谁是云侧唯一实现 / 平行实现在哪 / 缺口与为何不改）⇒ 口径关系**已书面化**；修复移交**功能波**（台账 §5.10 拍板 10.1）。**未改任何代码逻辑**（仅注释） |

### 8.6 B5.2f（第 1 步）波形缓存单点化：5 份同构副本 → `useWaveform` 组合式函数（2026-09-24 · 第八轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **B5.2f-1** 波形缓存（`contentId → /waveform` 峰值缓存）**5 份同构副本** → 单一组合式函数 | ✅ 完成（结构 + 冷编译 + **等价性证明**） | 原状：同一段 ~17 行实现**被复制 5 份**（`TabIndex` / `TabSearch` / `detail` / `favorites` / `portrait-theme-detail`），语义完全一致（非响应式 `Map` + **版本计数器**驱动重渲染；`null` = 未拉到 → 组件回退伪波形）。新增 **`client/composables/useWaveform.uts::WaveformCache`**（唯一实现），5 个组件改为 `const wf = new WaveformCache()` + `wf.waveformOf(...)` / `wf.loadWaveform(...)`；并从各文件移除不再使用的 `fetchWaveform` import | ① **等价性证明**（`.cowork-temp/verify_waveform_extract.py`）：5 份块归一化后**均为同一组 17 条逻辑行**（`OK` ×5），且 composable **覆盖全部逻辑行（缺失 `[]`）**——仅声明形态按预期变化（`let/function` → `private`/类方法）、`waveformMap/Version` → 实例字段 `map/version`；② **冷编译 `项目 client 编译成功`**——**首轮曾失败**：我的 import 重写脚本把 `", ".join(syms)` 误写成 `" ".join(...)` ⇒ detail 的多符号 import 被压成 `import {fetchContentAiMeta ContentAiMeta updateContentRemark}` ⇒ `[vue/compiler-sfc] Unexpected token, expected ","`；**被编译门当场抓到并修复**（教训：改 import 必须保留逗号分隔符）；③ 行数：`TabIndex` 1065→**1046**、`favorites` 975→**957**、`TabSearch` 763→744、`detail` 577→559、`theme-detail` 443→425（合计 **−91**，新增 composable 40 行）；④ 轴 5 baseline 按「棘轮只许收缩」**下调**（TabIndex 1065→1046、favorites 974→957），`audit_harness all` **无 CRITICAL** | **未闭环**：L-01/L-02 仍 **>800**（`RecordSheet` 1354 / `TabIndex` 1046）⇒ 需继续抽 script composable 才能销项；**真机渲染复验待设备**（波形加载是运行时行为——"编译通过 ≠ 运行正确"） |

### 8.7 B5.2f（第 2 步）RecordSheet 圆点/轮盘动画 → `useRecordAnimations`（2026-09-24 · 第九轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **B5.2f-2a** `RecordSheet` **圆点动画（176 行）+ 轮盘公转（42 行）** → 单一组合式函数 | ✅ 完成（结构 + 冷编译） | 新增 **`client/composables/useRecordAnimations.uts`**：`DotSpec`/`DotPt` + 模块级只读常量与预计算轨道表（`DOT_TRACK/COS/SIN`，构建一次）+ **`RecordAnimations`** 类（实例字段承载原模块级可变状态 `pts/dotAnims/ghostTimers/dotPaused/pxScale/wheelAnim`；原 `buildDotTrack/buildDotPts/dotRender(0)` 三处模块级初始化移入**构造器**⇒ 时机同属组件初始化期）。原函数仅**改名**为实例方法（`dotRender→render`、`startDotAnims→startDots`、`pause/resume/stopDotAnims→pause/resume/stopDots`、`start/stop/pause/resumeWheelAnims→start/stop/pause/resume`）；`dotCls` 原读组件 `recording.value` ⇒ 改为**入参** `cls(p, i, recording)`。组件侧：删 2 块（182+42 行）→ `const ra = new RecordAnimations()` + 顶层绑定 `const dotPts = ra.pts`（**模板自动解包**——`ra.pts` 是 Ref，直接写进 `v-for` 不会解包）+ `dotCls` 薄包装（保持**模板零改动**）；轮盘 4 处调用点改写为 `ra.start/stop/pause/resume` | ① **冷编译 `项目 client 编译成功`**（0 error）；② 残留旧调用名（`*DotAnims()`/`*WheelAnims()`）**= 0**；③ **行数**：`RecordSheet.uvue` 1354 → **1135（−219）**；④ 轴 5 baseline 按「棘轮只许收缩」**下调** 1354→1135 | **未闭环**：`RecordSheet` 仍 **>800**（1135）⇒ 需继续抽（自定义标签 ~95 / 语音录制流程 ~225 / 选图·文字提交 ~150）；**真机动画复验待设备**（圆点轨迹/轮盘公转是纯运行时视觉行为，编译通过 ≠ 运行正确） |

### 8.8 B5.2f-2b（自定义标签助手外置）+ 🚨 新发现：**编译门只覆盖语法**（轴 6 补位）（2026-09-24 · 第十轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| 🚨 **GAP-1** HBuilderX「项目 client 编译成功」**只覆盖语法/解析，不覆盖符号解析** | ✅ **发现 + 已补门禁（轴 6）** | **实测反证**：`client/components/RecordSheet/RecordSheet.uvue` 里 `new RecordAnimations()` / `const dotPts = ra.pts` 等 **8 处符号完全没有 import**（2 个新模块：`useRecordAnimations` 2 个 + `custom_labels` 6 个），而冷编译**连续两次报「项目 client 编译成功」**（15:21 / 15:26）。对照：**语法**级错误（import 丢逗号）会被 `[vue/compiler-sfc] Unexpected token, expected ","` 抓到 ⇒ **编译门的覆盖面＝语法，不含符号解析** | **补位（三重）**：① 新增 `scripts/audit_client_imports.py`（对 `client/composables/**`+`client/utils/**` 的导出符号做"用了却没 import"扫描；已剔除**注释**与**字符串字面量**误报——26→10→8 收敛过程实证；`.uvue` **只对 `<script>` 区段**做 JS 剔除，避免模板引号配对吞行造成**漏报**）；② 接入 `audit_harness` 新轴 **`client_imports`**；③ 接入 `review_agent.check_audit_axes`（**快/全量门禁均跑**）。**反向探针（端到端）**：删掉其中 1 行 import → `review_agent` **❌ 阻断**（`audit_axes`，精确点名 `RecordAnimations`/`DotPt`）→ 字节级还原 → 复跑 ✅ 通过 |
| **B5.2f-2b** `RecordSheet` **自定义标签**的 解析/持久化/选中态类名助手 外置 | ✅ 完成（结构 + 冷编译 + 门禁） | 新增 **`client/utils/custom_labels.uts`**（`readStoredCustomLabels` / `persistCustomLabels` / `customOn` + 4 个 chip 类名助手；原 `isCustOn(c)` 读组件 `manualLabel.value` ⇒ 改**入参** `manual`）；组件只留 3 个 ref（`customLabels`/`plusOpen`/`newCat`）与 3 个改状态函数（`confirmCustom`/`onCustChipTap`/`onPlusTap`）；**刻意不与内建标签对齐选中语义**（内建=手选优先→AI 预选；自定义=只看 `manual == 'custom:'+名称`，因 AI 从不预选自定义）⇒ 保持逐字等价 | ① `RecordSheet.uvue` 1135 → **1084（−51）**；② 4 个模板调用点改带 `manualLabel` 入参；③ **冷编译成功**；④ 轴 6 静态扫描 **0 违规**（362 导出符号全量）；⑤ 轴 5 baseline **下调** 1135→1084；⑥ 顺带修正：**6 个文件**新增 import 行的缩进统一为与该文件既有 import 一致的 Tab（原插入用了空格） | **未闭环**：`RecordSheet` 1084 / `TabIndex` 1046 **仍 >800** ⇒ 继续抽（语音录制流程 / 选图·文字提交 / TabIndex 两块）；**真机复验待设备** |

### 8.9 B5.2f-2c（模板取值封装外置）+ B5.2f 剩余=**设计决策项**（2026-09-24 · 第十一轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **B5.2f-2c** `RecordSheet` **模板取值封装**（4 个无状态纯函数）外置 | ✅ 完成（结构 + 冷编译） | 新增 `client/utils/record_ui.uts`（`fmtRecTime` / `recSecFromMs` / `cellCls` / `imgCls`；原设计注释与峰爸拍板记录**逐字保留**）；`setInfoTime`（写组件 `infoTime` ref）**留在组件内**；模板按名调用 ⇒ **模板零改动** | ① `RecordSheet.uvue` **1089 → 1054（−35）**——同时**消除**上一轮我新增 import 造成的 +5 增长 `[WARN] …应抽取而非加长`；② **冷编译成功**；③ 轴 6 扫描 0 违规；④ baseline 按棘轮**下调** 1084→1054（gate 自己提示"缩了要记账"） |
| 🧭 **B5.2f 剩余部分**（`RecordSheet` 1054→800 需 −254；`TabIndex` 1046→800 需 −247） | ⏸ **需设计决策 + 真机同批**（**妥善停在边界，未硬做**） | 实测剩余 script **全部是表单编排逻辑**（照片/文字/语音三段、L2/L3 归并），依赖组件状态（`photos`/`photoSizes`/`recording`/`recSec`/`transcript`/`emotion`/`days`… **10+ 个 ref**）与 `emit`/动画实例。继续抽**必须重新分配状态归属**：**方案 A** 整块 view-model 移入 composable（组件退回薄绑定层）；**方案 B** 拆子组件（模板+逻辑同迁，改 props/emits）。两者**都不再是「纯移动」**，且运行期行为（录音中断/上传/归并/动画）**只有真机可验** | 依用户规则「探索性/创造性设计前必须先探讨」，**未擅自开工**；已提请决策（见 progress）。风险权衡：**方案 A** 改动面集中在 script、模板几乎不动、回归面较小但"状态归属"属结构性变更；**方案 B** 结构最清晰但模板搬运量大、props/emits 契约易漏 ⇒ 更依赖真机逐路径验收 |

### 8.10 B5.2f-2d（**方案 A** 子步 1）：语音录制状态机整块进 composable（2026-09-24 · 第十二轮）

> **决策**：用户 2026-09-24 拍板 **方案 A —— 整块 view-model 进 composable（组件退化为薄绑定层）**（见 progress/本表 8.9 的选项说明）。

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **B5.2f-2d** `RecordSheet` **语音录制状态机**（13 状态 + 6 方法，163 行）→ `useVoiceRecord::VoiceRecorder` | ✅ 完成（结构 + 冷编译 + 轴 6） | 新增 `client/composables/useVoiceRecord.uts`：13 个语音 ref 成为**实例字段**，6 个状态机方法（`setRecordingOff`/`toggleRecord`/`pauseRecordNow`/`endFromTap`/`resumeRecordNow`/`stopRecordNow`）为类方法（注释与真机实锤记录**逐字保留**）；注入 `anim`（轮盘/圆点动画实例）、`onStopped`（组件 `onRecordStopped`，**暂留**）、`resetAiLabel`（等价替换原 `aiLabel.value = ''`）。组件退化为薄绑定层：12 个顶层 `const x = vr.x`（**Ref 必须顶层绑定才会被模板解包**）+ 6 个薄包装 | ① `RecordSheet.uvue` **1054 → 889（−165）**；② **冷编译 `项目 client 编译成功`**；③ 轴 6 扫描 0 违规；④ baseline 下调 1054→889 | 
| 🛠 **同轮修掉 4 个自己制造的缺陷**（留证；这是"生成式改写"的教训） | ✅ 全部修复 | ① **轴 6 真抓到回归**：组件 voice import 瘦身时误删 `recorderState`/`stopRecord`，而 `closeForm()` 仍在用（force-release 路径）→ 轴 6 **立即点名两处**；② 生成的类内方法误留 `function` 关键字；③ 内部方法互调未加 `this.`（运行期会变未定义调用）；④ 组件改写**先删声明、再用旧索引切割** ⇒ 错位（编译 `Unexpected token (885:0)`）。另：自写的"可疑未注入标识符体检"**主动抓出 `aiLabel` 漏注入**（否则会静默引用未定义符号） | **教训**：「生成式改写必须配 ①静态体检（未注入标识符）②真实编译 ③轴 6 导入检查」三道；**轴 6 在本轮已产生真实战功**（抓到我自己引入的回归）。**未闭环**：`RecordSheet` 889（差 −89）⇒ 需同法迁 `onRecordStopped`/`submitVoice`/`afterVoiceSaved`（~150 行）；`TabIndex` 1046（差 −247）；**运行期（录音状态机全链路）只有真机可验** |

### 8.11 B5.2f-2e（方案 A 子步 2）：语音收尾三函数迁入 → **L-01 销项**（2026-09-24 · 第十三轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **B5.2f-2e** `onRecordStopped` / `submitVoice` / `afterVoiceSaved`（147 行）→ 同一 `useVoiceRecord` | ✅ 完成（结构 + 冷编译 + 轴 5/6） | 三函数作为类方法迁入（`afterVoiceSaved` 为**私有**收尾）；`saving` 状态一并迁入；**撤销**上一子步的 `onStopped` 注入（`onRecordStopped` 已在类内），新增注入 `emitSaved`/`emitClose`/`setVoiceStage`/`effLabel`/`remarkTrimmed`/`resetFormFields`（把原 `emit('saved')`、`voiceStage.value=…`、`manualLabel!=''?…:aiLabel`、`remark.value.trim()`、收尾三行清态**逐一**改为注入回调，**行为等价**） | ① `RecordSheet.uvue` **889 → 753**（**首次 <800**）；② **冷编译成功**；③ 轴 5 **无 CRITICAL**；④ **axis-5 baseline 条目按 gate 要求删除 ⇒ L-01 销项**（gate 原话："已不再超阈……请从基线删除该条"）；⑤ 轴 6 0 违规 |
| 🔎 **GAP-1 再获一个强化数据点** | ⚠️ 记录 | 我的整文件正则把**方法声明**也改成了 `this.afterVoiceSaved(contentId: string, …): void {`（**非法类成员语法**），而 **`项目 client 编译成功` 依旧报绿**！⇒ 与 GAP-1 一致：客户端编译门**连类成员的非法语法都能放过**（只有 `[vue/compiler-sfc] Unexpected token` 那类**解析级**错误才会红） | 由我自己的静态检查（grep 方法声明/互调）抓出并修复。**强化结论：客户端侧"编译 0 error"不能作为任何结构正确性证据**——必须配 轴 6（导入）+ 静态结构检查 + **真机** |
| 🛠 另一处自查捕获 | ✅ 修复 | `saving` 原声明留在组件 + 我又加了 `const saving = vr.saving` ⇒ 编译报 `Identifier 'saving' has already been declared`（**这次编译抓到了**——重复声明属解析/绑定级） | 已在组件删除原声明并留注释 | 

### 8.12 L-02a（方案 A 子步 3）：`TabIndex` 照片路径 / 挂载 / 照片详情 → `usePhotoAttach`（2026-09-25 · 第十四轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **L-02a** `PhotoPathEntry` + 照片五态（`localPhotoPath`/`eventPhotoIds`/`showPhotoDetail`/`photoDetailPath`/`photoDetailEvents`/`photoDetailLoading`）+ 两张**非响应式索引表**（`photoUrlById` 服务端票据 URL、`localPhotoPathMap` O(1) 本地查表）+ 7 方法 → `usePhotoAttach::PhotoAttach` | ✅ 完成（结构 + 冷编译 + 轴 6/7） | `photoPathOf`/`lookupEventPhotos`/`attachEventPhotos`/`onPhotoTap`/`closePhotoDetail`/`jumpFromPhoto` 直迁（注释与「2026-08-31 重写」实锤记录**逐字保留**）；`attachPhotos()` 更名 **`attachAll(days, pending)`** —— 原读组件 `days.value`/`pendingEvents.value` 两个 ref，改为**入参**（渲染管线核心留在组件，时序零改动）；新增 `addLocalPath`/`addEventPhoto` 把 `handleBatch` 里「数组 push + O(1) 查表 set」两处**同语义写入**收纳为一个方法 | ① `TabIndex.uvue` **1046 → 899（−147）**；② **冷编译 `项目 client 编译成功`**（ready in 48708ms）；③ 轴 6（导入）**0 违规**；④ 轴 7（类成员）**0 违规** |

### 8.13 L-02b（方案 A 子步 4）：`TabIndex` 卡片操作 / 拆分 → `useEventOps` → 🎯 **L-02 销项**（2026-09-25 · 第十五轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **L-02b** 拆分四态（`showSplitPanel`/`splitEventId`/`splitItems`/`splitLoading`）+ 13 方法（`onCardOps`/`doConfirm`/`confirmPending`/`ignorePending`/`patchEventById`/`removeEventById`/`getPrevL1Id`/`doMerge`/`doSplit`/`toggleSplitItem`/`confirmSplit`/`cancelSplit`/`splitTimeText`）→ `useEventOps::EventOps` | ✅ 完成（结构 + 冷编译 + 轴 5/6/7） | 注入四个**取值函数**（不能传快照）：`getCurrent`（组件 `currentEvents` 是 `let`、会被整组浅替换）+ `render`（原 `renderFromEvents`）+ `refresh`（原 `refreshSilently`）+ `getDays`（原 `days.value`）；组件只留 `onCardOps`/`confirmPending`/`ignorePending` 三个模板入口薄包装 | ① `TabIndex.uvue` **899 → 738**（累计 **1046 → 738，−308**）；② **冷编译成功**（36.8s）；③ 轴 5 **无 CRITICAL**；④ **axis-5 baseline 条目按 gate 要求删除 ⇒ L-02 销项**；⑤ 轴 6/7 **0 违规** |
| 👁 **观察（登记 · 非本波修）** | 📌 交功能波 | 模板内**已无**拆分面板与照片详情浮层 ⇒ `showSplitPanel`/`splitItems`/`splitLoading`/`confirmSplit`/`cancelSplit`/`toggleSplitItem`/`splitTimeText` 与 `showPhotoDetail`/`photoDetailPath`/`photoDetailEvents`/`photoDetailLoading`/`closePhotoDetail`/`jumpFromPhoto` 均为**模板不可达的 UI 状态**——用户仍可经卡片「⋯」面板触发 `doSplit`（其会置 `showSplitPanel=true`），但**无任何节点渲染该面板** ⇒ 属**功能缺口**（非结构债），已从结构域移交功能修复波 | 本波**只做纯移动**、不改行为，故按原样搬迁并留此登记（避免"搬迁即修"越界） |

### 8.14 🚨 GAP-2：**类成员**是第二道编译假绿面 → 修 P0 回归 + 新增轴 7（2026-09-25）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **缺陷（P0 回归，已修）** | ✅ 修复 | B5.2f-2e 把 `saving` 迁入 `useVoiceRecord.uts` 时，**方法体写了 `this.saving.value` 却漏声明 `saving` 字段** ⇒ 真机 `submitVoice` 必 `undefined.value` 崩溃 | **两道门同时失守**：① HBuilderX 编译报绿（GAP-1：编译只覆盖语法/解析，不覆盖符号解析）；② **轴 6 抓不到**——它只查「用了模块导出却没 import」，`this.<名>` 是**类成员访问**、不是跨模块符号。修复＝补 `saving = ref(false)`（恢复迁移前的并发保存守门） |
| **新门禁：轴 7（`audit_class_members`）** | ✅ 完成（含负向探针） | `scripts/audit_class_members.py`：`client/**/*.uts|*.uvue` 的类，声明集＝类体**深度 0** 的字段/方法；覆盖**两个面**—— ① `this.<名>` 不在本类（GAP-2 原发形态）；② `const x = new Cls(…)` 之后 `x.<名>` 不在 `Cls`（实例指涉，L-02 抽 composable 后组件大量经实例调用）；**`extends` 类整体跳过**、同名变量非「全部赋值皆 `new Cls(`」则跳过（挡遮蔽误报，实测 `uploader_batch.uts` 的 `held`）；接入 `audit_harness` 新轴 `class_members`（含 `all`）+ `review_agent.check_audit_axes`（快/全量均跑） | ① 全仓 **76 类 0 误报**、3 个 `extends` 类跳过；② **反向探针两例**（删 `saving` 字段 → 报 `this.saving`；改 `pa.attachAll`→`pa.attachAllZZZ` → 报 `pa.attachAllZZZ`）**均 EXIT=1** 且**字节级还原**；③ 教训已登记 `docs/lessons.md`（GAP-2） |

### 8.15 L-18/L-19/L-20 样式外置 → 🎯 **轴 5 filesize 棘轮清零**（2026-09-25 · 第十六轮）

| 条目 | 状态 | 证据 | 备注 |
|---|---|---|---|
| **L-18 / L-19 / L-20** `TabAi.uvue` / `favorites.uvue` / `manage.uvue` 的 `<style>` 块 → `client/styles/{tab-ai,favorites,portrait-manage}.css` | ✅ 完成（结构 + 冷编译 + 产物取证） | 沿用 **B5d/B5.2e 已证机制**（整块 `<style>` 纯移动到 `client/styles/*.css`，uvue 侧只留 `@import`）——**零 script/运行时改动**（比抽 composable 风险更低） | ① **974 → 469 / 957 → 500 / 891 → 466**（三处均 <800）；② **等价性证明**：`当前 uvue + 还原 css` 与 `git show HEAD:<file>` **归一化后字节全等**（TabAi 工作区为 CRLF、git blob 为 LF，故须按行尾归一后比较——差值恰等于行数）；③ **冷编译成功**（ready in 67.8s）；④ **产物取证**：`GenComponentsTabAiTabAiSharedData.style.bytes` 含 `ai-root`/`ai-header`、`GenPagesFavoritesFavoritesSharedData.style.bytes` 含 `page-root`/`nav-title`、`GenPagesPortraitManageSharedData.style.bytes` 含 `page`/`serif` ⇒ `@import` 被真正处理（非静默丢弃） |
| 🎯 **轴 5 基线 allowlist 清空** | ✅ 完成 | 三条条目按 gate 口径删除后：`audit_harness filesize` → `无新增超阈文件 ✓（**存量超阈 0 个**已冻结）`、**无 CRITICAL** | ⇒ 本波**全部巨文件类棘轮项清零**（`contents.py`/`detail.uvue` 早前已销项；`RecordSheet`/`TabIndex`/`TabAi`/`favorites`/`manage` 于本波销项），台账 §2 的 L-01/02/18/19/20 全部 ✅；**注意**：`L-03b/L-07 events.py` 530 行 <600 阈，本来就不是棘轮项 |

### 8.1 棘轮状态（2026-09-24 第二轮执行后，第五轮更新 · 含 B5b/B10-q）

- **2026-09-25 更新（L-01 / L-02 / L-18 / L-19 / L-20 全部销项后）**：轴 5 存量超阈 **4 → 3 → 0（allowlist 清空）**——`RecordSheet` 随 **L-01**（B5.2f-2e，753 行）、`TabIndex` 随 **L-02**（L-02b，738 行）、`TabAi`/`favorites`/`manage` 随 **L-18/19/20**（§8.15 样式外置，974→469 / 957→500 / 891→466）销项；`audit_harness all` **无 CRITICAL**；新增**轴 7 类成员解析**（`class_members`，见 §8.14）并接入 `review_agent` 快/全量；**`review_agent --full` 实跑 EXIT 0**（Docker/Qdrant 已起）：`syntax` 376 文件 ✅ / `lint` ✅ / `tests` ✅（pytest 主套件 + `-m rag` 分组，**覆盖率 84.59% ≥ 60**）/ `api_smoke` ✅ / `research` ✅ 18 场景 —— 承 §8.3 B10-h 的"覆盖率待环境验证"**就此解除**。
- `audit_harness all`：**无 CRITICAL**（INFO 16）；轴 5 存量超阈 **7 → 6 → 5 个已冻结**（`contents.py` 条目随 B2 拆分**销项移除**；`client/pages/detail/detail.uvue` 随 **B5d** 样式外置**销项移除**，1167→577 行；`RecordSheet`/`TabIndex` 随 **B5.2e** 样式外置**下调计数**——2208→1354 / 1740→1065，条目**保留冻结**）；轴 6 `soft_delete_filter` 总数 **44 处未上升**，且**新增 per_file 双 gate**（总数与分布任一上升即 CRITICAL）；**轴 4 零调用能力导出存量 9 个已冻结**（B10-d 露出 `AuthError`/`getRecorder`/`lastTempFile`/`stopPeriodicSync`；B10-f 补递归露出 `WALK_SPEED_MS`；**B10-q 剔注释后新露出 4**：`AGG_CHECK_ON_DEVICE`/`invalidateTimelineCache`/`isIgnoredEvent`/`parseErrorString`），引用计数已**剔除注释**（B10-q），且新增"僵尸豁免清算"。
- 门禁接入（**已消除三类"恒绿假门禁"**）：
  - `review_agent` 现含 `structure`（轴 5/6，B1）+ **`audit_axes`（轴 1–4，B10）**，快/全量均跑、秒级；
  - CI fast-gate：原「client tsc 试点」为**真恒绿**（`continue-on-error` + 末尾无条件 `exit 0`，且 `client/tsconfig*.json` 不存在、tsc 不认 `.uts`）→ **退役**，替换为阻断式 `audit_harness client`（B12/D13-1）；
  - CI full-gate：新增阻断式 `audit_harness all`（B10/D14-1，无 `continue-on-error`）。
- 基线收缩触发：`contents.py` 条目**已移除**（B2 落地）；B3′ 收敛落地后**可下调** `soft_delete_filter` 计数（本批未下调——收敛只改 helper 结构、未减少调用点）。
- hook 自动覆盖，**未使用 `--no-verify`**。

---

## 9. Phase A 逐功能域深度审计结果（2026-09-24 · **本节覆盖 §2–§4 的浅层条目**）

> **性质**：只读筛查产物，本波**不改运行时行为**。本节是 14 域深审的**权威汇总读数**；逐条明细与可复现证据在 `audit/_PHASEA_ROLLUP.md` 与 `audit/domain-01..14-*.md`（本节只做汇总与结论，不复制 249 条明细）。
> **与 §2–§4 的关系**：§2–§4 为**浅层模式统计**（行数/grep 计数）；本节为**逐域深度审计**（真实重复/死码证明/依赖边/注册点/端云对照），**凡冲突以本节为准**。

### 9.0 总览

| 指标 | 值 |
|---|---|
| 域数 | 14（①认证设备 ②上传媒体 ③内容记录 ④事件聚合 ⑤检索RAG ⑥回响胶囊消息 ⑦画像访谈纠错 ⑧同步离线 ⑨微信 ⑩护栏安全 ⑪观测错误 ⑫客户端壳UI ⑬脚本部署 ⑭harness工具） |
| 条目总数 | **249** = 结构类 **185** + 功能与安全缺陷 **64** |
| 严重度 | P0 **17** ｜ P1 **111** ｜ P2 **121** |
| 状态 | 确认（有直接代码证据）219 ｜ **候审 30**（启发式待人工/运行时复核） |
| 优先处置清单 | P0+P1 = **128** 条 |

**分类规则**（可复现）：**结构类**＝巨文件/重复/死码/边界依赖/声明漂移/门禁缺口/注册表与扩展成本（含扩展风险型 P0）；**功能与安全缺陷**＝有运行时正确性/安全性后果者（数据丢失/错值/500/丢操作/跨用户/伪造/污染/fail-open/幂等失效/软删口径错误/未接线不可达）→ **本波只登记，交功能波**。

**各域条目分布**：①21 ②14 ③15 ④18 ⑤17 ⑥13 ⑦23 ⑧17 ⑨15 ⑩12 ⑪14 ⑫16 ⑬30 ⑭24。

### 9.1 P0 清单（17 条，深审新增）

| # | 编号 | 类别 | 位置 | 一句话 |
|---|---|---|---|---|
| 1 | D02-2 | 结 | `api/media.py:51`/`schemas/content.py:9`/`api/contents.py:130`/`storage.py:406`(+`wechat/service.py:251`) | 媒体键前缀白名单 **4 套独立字面量** + `wechat/` 命名空间漏网 → 微信原件经 `/media/{key}` 恒 401、`create_content` 恒 422 |
| 2 | D04-1 | 功 | `event_aggregation/pipeline.py:95,161`+`agg_runner.uts:91` | 端云 L1 日界时区未对齐（云侧生产 tz=0/UTC）→ 沪区 00:00–07:59 照片落前一日 |
| 3 | D04-2 | 功 | `agg_preprocess.py:23-93`×`client/utils/agg/pipeline.uts:36-49` | 端云预处理去重分叉（云侧生产无去重）；夹具用 Python 复制品垫背使双跑失去鉴别力 |
| 4 | D04-15 | 结 | `scripts/gen_agg_fixtures.py:158-165`+`client/utils/agg/fixtures.uts` | 双跑夹具 13 用例无一覆盖 `approx`/`corrected` 分支 → B3/B4/B5 漂移全在覆盖外 |
| 5 | D05-3 | 结 | `vector_store.py:337-398`×`rag/pg_fallback.py:50-66` | 过滤器翻译**双实现 + 未知键静默丢弃（无 else）**→ 漏隔离类键可跨用户召回 |
| 6 | D07-1 | 功 | `deploy/Dockerfile.backend:39`+`docker-compose.yml:156-159` | 镜像无 `docs/` → 容器内 `get_schema()` 抛 `FileNotFoundError`（画像/访谈首调 500） |
| 7 | D08-1 | 功 | `services/sync.py:155-185`×`api/contents.py:620-638` | 软删双轨：push delete 不写 `contents.deleted_at` → 离线删除后仍可见、30 天后突然物理消失 |
| 8 | D08-2 | 功 | `api/contents.py:626-638`×`workers/cleanup_job.py:87-95` | REST 删除不写 `DeletedLog` → 30 天清理永不选中（UI 承诺未落地） |
| 9 | D08-3 | 功 | `services/sync.py:206-220`×`client/utils/play.uts:647` | SFV 的 `value` 不投影回权威表 → 离线改备注上云成功但永不可见 |
| 10 | D09-1 | 功 | `api/wechat.py:99`+`service.py:340,357` | 回调未传 `user_id` → 不下载媒体/不建 Content/不产记忆（F6 主链未跑） |
| 11 | D09-2 | 功 | `service.py:251`+白名单 4 处 | `wechat/` 键不在白名单 → 微信图片原件恒 401 |
| 12 | D10-1 | 功 | `dashscope.py:187`↔`api/contents.py:297`/`photo_content.py:180` | 护栏 fail-closed verdict **无 `action` 键** → 两调用点两 if 均不成立、**原文入库** |
| 13 | D10-2 | 功 | `llm_ops/guard_managed.py:132-139` | 托管护栏**空响应判 `pass=True`**（唯一漏网 fail-open） |
| 14 | D11-1 | 结 | `api/capsules.py:39-42,51,97,100,162,184` | `CAPSULE_001..004` **就地定义、未登记**进 `core/errors.py`（违反唯一真源） |
| 15 | D12-1 | 结 | `client/design_tokens.json`×`client/**/*.uvue` | 令牌机制覆盖率 **0%**（763 处色字面量、660 处抄写命中）——同 L-12，不重复立项 |
| 16 | D12-5 | 结 | `scripts_ardot2uvue.py:27`+`gen_design_data_v4.py:20` | 生成链 `ROOT_DIR`/`DESIGN_DIR` 硬编码本机路径、**不可复跑**；手写/生成边界无标记 |
| 17 | D14-1 | 结 | `scripts/review_agent.py:328-330`+`ci.yml:48,370` | 审计轴 1–4（契约/客户端/模型/导出）**未进任何自动门禁**（仅轴 5+6 接入） |

### 9.2 功能与安全缺陷（64 条 · **本波不修，交功能波**）

> 📌 **施工期新增 1 条（2026-09-25 · 交功能波受理，不并入下面 64 条的深审计数）**：`TabIndex.uvue` 模板（1–209 行）**已无**拆分面板与照片详情浮层，而脚本仍持有 `showSplitPanel`/`splitItems`/`splitLoading`/`confirmSplit`/`cancelSplit`/`toggleSplitItem`/`splitTimeText` 与 `showPhotoDetail`/`photoDetailPath`/`photoDetailEvents`/`photoDetailLoading`/`closePhotoDetail`/`jumpFromPhoto` ⇒ 卡片「⋯」可触发 `doSplit`，但**无节点渲染该面板**（用户点“拆分这张卡”无可见结果）= **功能缺口**。详见 `docs/决策台账.md` §4.15 **15.4** 与 §8.13。

**P0（10 条）**：D04-1、D04-2、D07-1、D08-1、D08-2、D08-3、D09-1、D09-2、D10-1、D10-2（见 §9.1）。
**P1（36 条）**：D01-6/7、D02-4/5/6、D03-6/7、D04-4/5/6/13、D05-5/14、D06-5、D07-3/4/5/7/8/9/10/11/12/16、D08-5/6/7/8/9、D09-3/4/5/7/8、D10-5/6/7/8/9、D11-4。
**P2（18 条）**：D01-16/17、D03-15、D05-17、D06-8/9/10、D07-17、D08-13/14/15、D09-12、D10-10/12。

**给功能波的三个必修簇**（按后果排序，均属"MVP 允许缺失但不允许阉割"范畴）：
1. **端云聚合同源（域④）**：D04-1/2 + 夹具覆盖缺口 D04-15 → 双跑门禁当前**无鉴别力**，端云"同源契约"实际已分叉。
2. **软删 30 天承诺（域⑧）**：D08-1/2/3 → 用户可见路径上"彻底清除"与"离线改可见"两条承诺均未落地，且出现错序。
3. **护栏 fail-safe（域⑩）+ 微信主链（域⑨）**：D10-1/2（放行）、D09-1/2（主链不通）→ 分别违反"fail-safe 默认拒发"与 F6 交付。

> **登记口径**：本节 64 条即本波对功能波的**移交清单**；`refactor-ledger.md` §7「本波不覆盖」中的"功能性/安全缺陷"即指此表。

### 9.3 与旧条目对账·修正（4 处，**以本节为准**）

| # | 旧条目 | 深审结论 | 处置 |
|---|---|---|---|
| 1 | §1「软删手抄 44 处/21 文件」 vs 域③「49 处」 | **口径差异，二者皆真**：`backend/app` = **44/21**（旧台账准确，生产口径）；含 `tests`(4)/`scripts`(1) 另计 5 处 → 全 `backend/` 49/25 | 保留 44/21 为生产口径，另注 5 处非生产 |
| 2 | §3 L-11「有 `test_agent_schema_alignment.py` 守卫」 | **守卫为幻影**：全仓 `*alignment*` 0 命中、git 全历史 0 命中（仅 3 处文档引用它）→ B1 镜像豁免**无自校验**，且 D14-5 另指其"整文件含'镜像'即全量豁免"仍留假阴性口子 | L-11 已就地更正；豁免正当性**待补真实对拍测试或删声明** |
| 3 | §8 B3「验证即达标，无缺口」 | **不成立**：`test_authz_gate.py:47` 登记名 ≠ 实际符号 → capsules loader 哑条目（D06-2） | B3 已降级为"门禁覆盖有盲点"；三 helper 收敛升为待办 |
| 4 | §3 L-12「P1 候审」 | **升 P0 确认**：令牌机制覆盖率 0%（D12-1），见 §9.1 #15 | 已就地升格；**同一条不重复立项** |
| 附 | AGENTS「第四窗 8 文件在途＝勿碰」 | 深审 14 域一致记录**在途 = 0**、该批均已入库 | 勿碰名单**可解除**（已回填 §1） |
| 5 | 域⑦ D07-13「`validate()` 硬编码 `l0_count!=51`/`l1_count!=193`」（原判 硬编码枚举 P1） | **改判为非缺陷（P2 保留）**：实读确认 `validate()` **仅被测试调用**（`backend/tests/test_profile_annotator.py:48`），生产路径 `get_schema()` **不调它**（生产 4 处调用点见 `annotate.py:52` / `interview.py:144` / `profile_annotator.py:78,136`）⇒ 它是**测试期契约断言**（校验枚举集 JSON 恰为 51 个 L0 / 193 个 L1 维度）；这两个数字**就是规格契约本身**，不是"需同步维护的散落常量"；无生产调用是**有意设计**（不在请求路径上付校验成本） | 保留；若希望数字版本化，改为带注释的命名常量即可（纯可读性，无行为收益） |

### 9.4 B2 就绪：`contents.py` 拆分最小方案（域③专章）

- **现状**：902 行，4 router **物理交错混居**（非顺序分块）——`router` / `profile_sensitive`(≈454-514) / `favorites`(≈641-689) / `trash`(≈690-814) / waveform(≈815-902)。
- **天然拆法**：转 `api/contents/` 子包（沿用 `upload/` 先例），移出 `_routers` / `serializers` / `profile_sensitive` / `favorites` / `trash` / `waveform` **六文件 ≈382 行**，留守 `__init__.py` **≈520 行（<600 阈值 ⇒ 棘轮解除）**。
- **硬约束（不改会静默失效）**：`upload_photo` 与 `create_content` **必须留在 `__init__.py` 顶层命名空间** —— 两条测试 monkeypatch 打在 `app.api.contents` 上，移走即静默失效。
- **验证**：pytest（contents / content_upload / photo_content / aggregation）+ `audit_harness openapi` 路径无消失。
- **共享私有符号**：`_to_out` / `_attach_thumb` 等被多 router 用 → 随子包内 `serializers` 模块安置并兼容再导出。

### 9.5 跨域扩展成本（Top 6，全表见汇总 §5）

| 排名 | 域 | 扩展场景 | 需改处数 | 根因 |
|---|---|---|---|---|
| 1 | ④事件聚合 | 新增一层聚合 / 换算法 | **19 处**（后端 11+端 6+脚本 1+门禁 1） | 无任何注册表，硬编码 4 层假设 |
| 2 | ⑤检索 RAG | 替换向量库 / 新增召回 | **14 处** / 8 处 | 无 provider 接口、无单点扩展位 |
| 3 | ⑫客户端壳 UI | 新增 1 个 Tab | **6 文件 / 12+ 点** | 无单一注册表（VALID_TABS+4 ref+模板+if 链+TabBar+自由串） |
| 4 | ③内容记录 | 新增一类内容类型 | **≥11 处 + 2 契约注释** | 契约/前缀/展示维度无注册表（覆盖度≈1/4） |
| 5 | ⑨微信 | 新增一类消息类型 | **9 处** | 白名单/字段抽取/扩展名/审核/映射散落 |
| 6 | ⑧同步离线 | 新增一种可同步实体 | **后端 6 处 + 端 3 软点** | SFV 通用路径 vs 事件专用路径两套并行 |

> **共同根因**：契约/注册表维度普遍缺位 —— 仅"处理器维度"类注册表（`CONTENT_HANDLERS`、`_ADAPTERS`）到位，**校验/前缀/展示/通道维度均散落硬编码**。

### 9.6 端云一致性结论（域④ + 域⑧）

- **聚合（域④）**：共享参数 **8 项数值零漂移**，但 **语义/行为漂移 7 处（B1–B7）**；**B1（L1 时区）/B2（预处理去重）是生产路径真实分叉**，且被夹具的"固定 tz=480 + Python 复制品去重"掩盖 ⇒ **门禁参数 ≠ 生产参数，"双跑全绿"不代表端云同源**。另有 3 项参数未登记进契约表，`AGG_CONFIG["night"]` 等为装饰性死配置。
- **LWW（域⑧）**：4 项规则 **3 项不一致** —— ① tie-break 客户端**零 LWW 判定**（盲信服务端 `updated_at` 直接覆盖）；② 字段级能力**无本地载体**（客户端实体级镜像、`value` 丢弃）；③ 软删 **端口径分裂 + 服务端内部双轨**。`push_ops` 比较基准是"设备时钟 vs 设备时钟"，服务端时钟不参与判定。

### 9.7 A7 待用户拍板（优先级与批次切分）

**A. 结构类批次（本波可直接做，行为等价）**

| 批次 | 内容 | 依赖 | 风险 | 状态（2026-09-24 第二轮后） |
|---|---|---|---|---|
| B2 | L-03 `contents.py` 子包拆分（方案已定 §9.4）+ L-03b `events.py` | B1 | 中（测试护航） | ✅ **已完成**（`contents.py` → `api/contents/`，902→524 行；`events.py` 530 行 <600 阈、非棘轮项，仍登记未拆）。见 §8.2 |
| B2′ | B2 引发的门禁覆盖回归（`glob` 漏子包） | B2 | 低 | ✅ **已完成**（`rglob` + 子包覆盖自检） |
| B3′ | L-09 三 helper 收敛（**同时修正门禁登记名 D06-2**） | B2 | 中（AST 门禁护航） | ✅ **已完成**（`load_owned_entity` 泛化 + 哑条目修正 + `test_ownership_loaders_all_exist`） |
| B10 | 门禁加固：D14-1 轴 1–4 进自动门禁、D14-3/5 棘轮假阴/假阳、D14-2 | B1 | 低（纯工具） | ✅ **已完成**（`audit_axes` + CI `audit_harness all` + 双 gate + 豁免收窄；负向探针证明非空转）。D04-15/D11-2/D12-9 见下 |
| B12 | 脚本/部署可移植性（域⑬）：D13-1/2/3/17/18/25/26、硬编码路径 6 处 | — | 低 | ⚠️ **基本完成**（D13-1 假门禁退役 / D13-3 模板补齐+接门禁 / D13-26 README / 4 条硬编码路径已修；**D13-2/17/18/25 仍只登记**——改即改部署行为或属一次性件） |
| B11 | 声明漂移清零（结构类 P2 群） | — | 极低 | 🔄 **大部分完成**：已处理 **D04-17 / D13-28 / D13-14 / D14-2**（修）＋ **D11-5**（棘轮化闭环）＋ **D07-13**（改判为非缺陷）＋ **第七轮**（见 §8.5）：**D05-9 ✅**（rerank 模型名单点化，5 处）· **D02-7 ✅**（`photos/` 键布局单点化）· **D04-3 ✅**（`night` 接线 + 删冗余 `gps_speed`）· **D12-7 ✅**（失效证据书面作废）· **D02-9 ⏸ 登记未改**（改即改 MIME/受理格式 = 行为变更 → 功能波）。**仍待处置**：无（`D07-14` 已于第七轮 ⏸ **登记未改**——属 UI 文案变更；`D09-10` ✅ 修复并加防漂移门禁；均见 §8.5）——**B11 结构类项已全部处置完毕**（4 修 + 1 加门禁 + 2 登记待功能波） |
| B10-c | 错误码**反向棘轮**（registered-but-never-raised） | B10-b | 低 | ✅ **已完成**（见 §8.3 D11-5；双向判定 + `DEAD_CODE_BASELINE` + 内存级负向探针） |
| B10-d | 轴 4 `EXPORT_RE` **假阴性**修正 + 零调用能力导出棘轮 | B10 | 低 | ✅ **已完成**（见 §8.3；`zero_cap` 由被掩盖的 0 → 露出 4，含 **3 枚深审未涵盖的新发现**；基线冻结 + 僵尸豁免清算 + 双负向探针） |
| B10-e | filesize / dup 基线**僵尸豁免清算**（D14-4 的另一半） | B10 | 低 | ✅ **已完成**（见 §8.3；filesize "已不再超阈" → CRITICAL、dup `per_file` 归零 → CRITICAL、存量下降 → WARN 要求记账；先对账确认无误红 + 双负向探针 + 基线精确还原） |
| B10-f | 轴 4 **覆盖面补全**（D14-16：非递归漏子目录 + `export type/interface/enum/default` 漏扫） | B10 | 低 | ✅ **已完成**（见 §8.3；导出 298 → **332**，露出 `WALK_SPEED_MS`（＝深审 D04-12）；子目录负向探针真红且行号正确） |
| B10-g | 契约快照**再生脚本 + 一致性断言**（D14-7） | B10 | 低 | ✅ **已完成**（见 §8.3；新增 `scripts/gen_openapi.py` + `review_agent` 的 `openapi_snapshot` 检查；实证**现有快照与后端逐字节一致**，此前无人能证；负向探针真红且能定位） |
| B10-h | 门禁**静默转绿** + **覆盖率双阈值**（D14-19 / D14-8） | B10 | 低 | ✅ **已完成**（缺 ruff/pytest **默认阻断**、`--allow-missing-tools` 才可见放宽；覆盖率阈值收敛为单一 `COV_THRESHOLD=60`；内存级探针验证；**阈值 50→60 的实际覆盖率待 Docker 可用后确认**） |
| B10-i | 门禁域**剩余项** | B10 | 低 | ✅ **已完成（4 项）**（见 §8.4）：**D14-23** CI schema-drift 去 `continue-on-error`（定时漂移即红）· **D14-22** 新增 `scripts/gate_report.py` 统一机读 schema + 兜底写入 · **D14-12** 新增 `scripts/gate_proc.py` 收敛 subprocess 双实现 · **D14-6** 判定**已被 B10-g 字节级 `openapi_snapshot` 覆盖**（反向探针证实，无需改码） |
| B10-j | 轴 2 **注释判定**假阳性/假阴性（D14-14 / D14-15） | B10 | 低 | ✅ **已完成**（见 §8.3；改为字符级注释区间判定：消除块注释/多行 HTML 注释两类**误报**，并修掉字符串 `http://` 导致的**漏报**；注释内 `.ts` 提及单独计为 INFO） |
| B10-k | 退出码口径**统一**（D14-18） | B10 | 低 | ✅ **已完成**（见 §8.3；`review_agent` 落地 0/1/2 三态 + `_classify_failure()` 纯函数分流 + 报告 `env_blocked_checks`；5 例探针全对） |
| B10-l | 密钥模式集**唯一来源**（D14-9） | B10 | 低 | ✅ **已完成**（见 §8.3；`scripts/secret_patterns.py` 并集 16 条，两处共用；新增 `pragma: allowlist secret` 显式行内豁免；并集无缺失、四类形态全覆盖） |
| B10-m | `audit_security` **崩溃 + 白名单失效**（既有缺陷） | B10 | **中**（安全审计此前从未跑通） | ✅ **已完成**（见 §8.3；修崩溃（`db/models` 拆包）+ 白名单按路径段匹配 + 统一合成值抑制；首次跑通后**仅剩 1 项真实 ❌：最近备份距今 493.0h（≈20.5 天）违反 RPO≤24h —— 交运维/用户处置**） |
| B10-n | UTF-8 兜底同语义多实现统一（D14-11） | B10 | 低 | ✅ **已完成**（见 §8.3；新增 `scripts/gate_io.py::force_utf8()`，**16 文件 / 20 处**全迁移；`scripts/` 全目录 ruff 通过、17 脚本 py_compile 通过、残留仅 gate_io 自身） |
| B10-o | 退出码口径单一来源 + `test_agent` 姊妹假绿（D14-18 残余 / D14-19 姊妹 / D14-13 半） | B10 | 中（**又一个"报告全绿但测试从未跑"**） | ✅ **已完成**（见 §8.3；`scripts/gate_exit.py` 单一来源 + `test_agent` 缺依赖改环境错误（退出码 2）+ `audit_harness` 排除集合并；探针 4 例 + 源码断言） |
| B10-p | 修 B10-n 引入的导入回归（Docker 起后全量门禁抓到） | B10 | 中（**曾致 pytest 收集中断**） | ✅ **已完成**（见 §8.3；5 个"会被按包导入"的脚本改双路径导入；五处按包导入冒烟 + 21 passed + research ✅） |
| B9 | 端口/边界收口（B9a 事件域门面 / B9b rag→llm_ops / B9c 存储注册表+COS 唯一构造点） | — | 中 | ✅ **已完成（三项子批）**：`44a837c` / `32b4a89` / `8286327`。**D05-16 改判为已收敛**：`correction._get_store()` 用的是共享 `get_qdrant_client()`（P2-04 已修），残余仅"裸调用未走 `VectorStore` 门面"——那只是无收益的间接层，且改走门面会**变更 `corrections` collection 的 schema**（新增 image_vec/sparse）⇒ 属**行为变更**，本波不做，登记。D07-21（函数级导入 classifier、无真实循环）为 P2，登记不改。残余：rag 的 3 条非 LLM 适配器引用、config/env 两处部署面校验 |
| B5 | 客户端拆分 + 令牌收敛（L-01/02 + L-12/D12-1/3） | B1 + 编译门 | **高** | 🔄 **进行中（编译门已恢复）**：**B5a `play.uts` ✅**（`000101f`，653→6 域模块）· **B5b `sync_client.uts` ✅**（`40fd672`，705→5 模块）· **B5c `uploader.uts` ✅**（`eaf0163`，628→6 模块）· **B5d `detail.uvue` 样式外置 ✅**（`dc9c75c`，1167→577，**轴 5 销项**）· **B5.2e `RecordSheet`/`TabIndex` 样式外置 ✅**（`284f912`，2209→1354 / 1742→1065，baseline 下调计数）。**剩余**：① **B5.2f** script 抽 composable——**第 1 步已完成**（见 §8.6：波形缓存 5 份副本 → `useWaveform`，5 文件合计 −91 行）；**仍需**继续抽（`RecordSheet` script 1097 / `TabIndex` 851 / `TabSearch` 379），L-01/L-02 仍 >800，**验收口径含真机**而当前 `adb` **无设备** ⇒ **未闭环**；② **L-12 令牌收敛**（P0，7391 行样式 vs `design_tokens.json` **0 引用**）⇒ ✅ **已拍板另立「令牌波」**（台账 §5.10 拍板 10.4） |
| — | **②子批：门禁缺口补强**（D11-2 错误码门禁跨模块盲区 + D11-1 漏登记、D12-9 轴 2 三向缺一向；D04-15 见下） | B10 | 低 | ✅ **已完成**（D11-2/D11-1/D12-9 已修并含反向探针；**D04-15 判定为阻塞**——被功能波 D04-1/2/3/4/5 阻塞，见 §8.3） |

**B. 功能与安全缺陷（64 条）→ ✅ 已定：另开「功能修复波」**（2026-09-24 用户确认「另开功能波」，不并入本波 / 不并入 4b；见 `docs/决策台账.md` §5.10 拍板 10.1）。

**C. 需用户拍板的三件事 → ✅ 三件均已拍板（2026-09-24）**
1. ✅ **ORM/alembic 为权威**，`backend/sql/schema.sql` 降级为"CI 建库快照 + 漂移检测输入"（台账 §5.10 拍板 10.3；补破坏性漂移迁移需专用窗口，本波不执行）。
2. ✅ **64 条功能缺陷另开「功能修复波」**（同上 10.1；本波只登记）。
3. ✅ **B5 不再顺延**——编译门已恢复，B5a–B5.2e 已完成，剩余 **B5.2f（script 半）**。

**D. 新增拍板（2026-09-24）**：**L-12 令牌收敛 → 另立「令牌波」**（判为复杂：规模 7391 行/32 文件 + 令牌值自身漂移 26/56 缺失 + App 端样式不继承需构建期注入 + 验收须目视真机；见台账 §5.10 拍板 10.4）

### 9.8 深审方法与边界

- **只读保证**：14 域审计 + 汇总**未修改任何业务代码**（各域报告一致记录 `git status` 仅新增审计目录）。
- **检索工具限制**：本机 `Grep`(rg) 不可用时改用 PowerShell `Select-String`（语义等价、含行号），命令附于汇总 §4。
- **未覆盖**：`agent/`（全波排除）、`client/uni_modules/**`、`client/unpackage/**`、`scripts/realdevice/evidence/**`（二进制/证据）、生产 crontab（仓库外）。
- **候审 30 条**：语义判定类（D01-16/17、D04-13、D05-14、D06-5、D08-13、D10-8/9、D11-4/8/12、D14-16/17/24 等）落地前需人工/运行时复核。