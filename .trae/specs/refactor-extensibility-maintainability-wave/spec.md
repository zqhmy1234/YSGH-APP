# 易扩展/易维护重构波（Extensibility & Maintainability Refactor Wave）Spec

> 路由：`staff-engineer-mode` → 主 specialist = **dependency-and-code-hygiene**（维护/重构台账），次 = **architecture-decisions**（可扩展性/模块边界）。`dev-code-review` 作为**每批提交前**的评审门（无 diff 时不产生动作）。
> 纪律来源：本仓 harness（`AGENTS.md` / `docs/决策台账.md` / `docs/lessons.md` / `scripts/review_agent.py` / `scripts/audit_harness.py` / `docs/团队Git规范.md`）。

## Why

项目已到 MVP 内测前夜，功能性代码缺口大体清零，但**结构债与代码卫生已成本轮"改得动"的约束**：早前「波D 后端结构重构」只处理了 3 个 services 巨文件，而**新一批巨文件与跨模块重复仍在**（`backend/app/api/contents.py` 902 行、`api/events.py` 530 行、`client/components/TabSearch/TabSearch.uvue` 763 行、`client/utils/sync_client.uts` 705 行、`client/utils/uploader.uts` 628 行）；同时存在已登记但未闭环的债（AG8 `alembic check` 常年红、`决策台账 §2.14` 建议的 openapi 路径门禁未做、§3.1 声明漂移未清）。这些是"易扩展、易维护"的直接阻力，也是**在服务器/凭证未到位期间仍可本地验证**的一块高价值工作。

本波目标：以**逐功能域静态筛查 → 可拍板台账 → 小批行为等价重构 + 门禁棘轮**的方式，系统性降低结构债，并防止新债回潮；本波**只做结构与质量，不改运行时行为**。

## What Changes

- 建立**逐功能域（14 域）重构台账**：先只读静态筛查，产出一份含 位置/类型/严重度/收益/风险/可回滚性/验证手段/批次归属 的 Refactor Ledger（**只立项不改码**）。
- 定义**行为等价重构批次**执行契约：小批、独立提交、可 revert、每批过门禁（`review_agent` 快速+全量、`test_agent`、客户端冷编译门、`audit_harness` 各轴）。
- 给 harness 加**防退化棘轮**（先基线下冻结现状、只拦新增）：文件体积轴、重复模式（归属校验/软删过滤）轴、契约路径"只增不减"断言、声明漂移扫描。
- 清理已登记**声明漂移**（`shell.uvue:7` 陈旧注释、`search_api.uts:183` 陈旧 mock 叙述、`feature_list.json` 同条自相矛盾证据）。
- 范围：`backend/app/`、`client/`、`scripts/`、`deploy/`。**`agent/` 明确排除**（属独立改写批次，354 处 lint 违规会淹没信号，见 `决策台账 §4.12`）。
- **BREAKING**：无。本波严格行为等价；仅"注释/清单宣称"与新增门禁属可见变化，运行时不改。

## Impact

- Affected specs（能力契约，必须保持等价）：F1–F9 全部用户可见行为；`docs/OpenAPI契约.md` + `docs/openapi.json`（路径集合只增不减）；客户端页面入口路由（`client/pages.json`）不变。
- Affected code：
  - 后端 `backend/app/api/*`、`services/**`、`core/*`、`db/*`、`workers/*`
  - 客户端 `client/utils/**`、`components/**`、`pages/**`、`uni_modules/**`
  - 脚本/部署 `scripts/**`、`deploy/**`
  - harness `scripts/audit_harness.py`、`scripts/review_agent.py`、`docs/决策台账.md`、`docs/lessons.md`

---

## 决策（ADR-lite）

**决策问题**：如何在**不引入行为回归、且部分工作受外部阻塞（服务器/凭证）**的前提下，系统性偿还结构债并提升可扩展性？

**Decision**：采用"**先只读全量台账 → 用户拍板优先级 → 小批行为等价重构 + 棘轮**"的重构波，覆盖 `backend/app + client + scripts + deploy`，`agent/` 排除。

**Forces（力）**

| # | Force | Rationale |
|---|---|---|
| F-1 | 变更成本受结构债约束 | 902/763/705 行级巨文件 + 跨文件重复，抬高每次改动风险 |
| F-2 | 内测期不容行为回归 | 已到内测前夜，任何功能回归代价最高 |
| F-3 | 多窗并行 / 在途未提交 | `AGENTS.md` 明载"第四窗 8 文件在途未提交=新勿碰名单"，触碰即事故 |
| F-4 | 已有多门禁但缺"结构轴" | `review_agent` 管 lint/密钥/TODO，`audit_harness` 管契约/客户端/模型/导出，**无文件体积与重复模式轴** |
| F-5 | 已有成功先例 | 波D（`1ec8a15/b37dac7/2f14af0` 拆 services 巨文件 + `0efd838` AST 归属门禁）可复用其"拆+兼容导出+测试护航"手法 |
| F-6 | 外部阻塞压缩可做面 | 云打包/上机/真机受服务器与凭证阻塞 → 应优选**本地可验**域（后端测试、脚本、harness、声明/注释） |

**Alternatives considered**

| 备选 | 结论 | 理由 |
|---|---|---|
| 一次性大爆炸重构 | ❌ 拒 | 违反"可理解/可回滚/可回归"铁律，无法 review，回归不可归因 |
| 只做 lint/格式化自动修 | ❌ 拒 | 不解决结构债与可扩展性（注册表/边界/端口抽象） |
| 照搬外部目标架构重写 | ❌ 拒 | 偏离既有契约与既有纪律；重写=最大风险 |
| 把 `agent/` 一并纳入 | ❌ 拒 | 属独立改写批次（AG1–AG8），354 违规会淹没真信号 |
| 边审边改（不先出台账） | ❌ 用户未选 | 缺全局优先级与收益/风险分级，易在低价值项上耗散 |

**Consequences**

| Positive | Negative |
|---|---|
| 结构债与可扩展性**量化、可排序、可拍板** | 台账先行会占用一轮"纯阅读"成本 |
| 小批 + 门禁 → 回归可归因、可 revert | 单批吞吐受限（刻意求稳） |
| 棘轮使"清完又回潮"不可能 | 新轴需先基线自校验，否则假阳性会污染台账 |
| 全程本地可验，不被外部阻塞 | 客户端批次仍依赖编译门（HBuilderX GUI + Docker）可用性 |

**Reversibility（可逆性）**：每批独立提交、单独 revert；台账为只读产物无副作用；棘轮以"基线白名单"实现，撤销白名单或回退该提交即恢复现状。**重估触发**：若单批回归无法在半天内归因，则把批次粒度再减半；若编译门持续不可用，则客户端批次整体顺延、只做后端+harness。

**Responsibility**：优先级拍板 = 用户；本地检查路径 = `python scripts/review_agent.py [--full]`、`python scripts/test_agent.py`、`python scripts/audit_harness.py`、客户端 `cli.exe launch app-android --compile true`；登记路径 = `docs/决策台账.md` + `python scripts/lessons.py add`。

---

## 功能域地图（逐功能筛查单元）

| 域 | 后端 | 客户端 | 边界/契约关系 | 主要验证手段 |
|---|---|---|---|---|
| 认证与设备 | `api/auth`、`services/auth/*`、`core/security`、`core/ratelimit` | `utils/auth`、`utils/device_id`、`pages/account` | 上游=客户端登录态；契约=`/auth/*` + `errors.py` 错误码 | pytest（auth 族）+ 契约对拍 |
| 上传与媒体 | `api/upload`、`api/media`、`api/thumbnails`、`services/upload/*`、`services/external/{storage,media_url}`、`file_magic` | `utils/uploader`、`utils/upload_pipeline`、`utils/upload_protocol` | **第四窗在途**（勿碰清单）；契约=`upload/protocol.py` | pytest（upload 族）+ 编译门 |
| 内容与记录 | `api/contents`、`services/pipeline*`、`classifier`、`ai_tagging`、`ner`、`photo_content` | `utils/text_recorder`、`components/RecordSheet` | 契约=内容详情/PATCH/DELETE | pytest（contents/pipeline/classify） |
| 事件聚合与时间轴 | `api/events`、`api/event_items`、`services/events/*`、`services/event_aggregation/*` | `utils/agg/*`、`utils/timeline`、`utils/agg_runner` | 端云**同源契约**（`agg_config` 对照）；关系=共享内核 | pytest（aggregation/events）+ 端云双跑 |
| 检索与 RAG | `api/search`、`services/rag/*`、`embedding`、`vector_store`、`llm_ops/rerank` | `utils/search_api`、`components/TabSearch` | 契约=`/search/*`；降级=pg_fallback | pytest（rag/search/image_search） |
| 回响/胶囊/消息 | `api/echo`、`api/capsules`、`api/messages`、`services/echo`、`notify` | `components/{EchoSheet,CapsuleSheet,MessageDetailSheet}`、`pages/messages` | 契约=`/echo`、`/capsules`、`/messages` | pytest（echo/capsule/notify） |
| 画像/访谈/纠错 | `api/interview`、`api/corrections`、`services/{profile*,interview,correction}` | `pages/interview`、`pages/portrait`、`utils/event_ops` | 契约=`/corrections`、`/interview` | pytest（profile/interview/correction） |
| 同步与离线 | `api/sync`、`services/{sync*,events/sync}` | `utils/{sync_client,event_sync,queue_store,retry}` | 契约=字段级 LWW + 软删 30 天 | pytest（sync/reconcile） |
| 微信域 | `api/wechat`、`services/wechat/*` | — | 端口反转已做（`ports.py`） | pytest（wechat族）+ 实网待凭证 |
| 护栏与内容安全 | `llm_ops/{guard*,moderate,parsing}`、`external/{content_safety,sensitive_words}` | `utils/{sentry,log}`（观测侧） | fail-safe 默认拒发 | pytest（content_safety/guard） |
| 观测与错误 | `core/{errors,middleware}`、`services/errors` | `utils/{sentry,log}` | 错误码登记表 + `audit_harness` | `test_error_registry` |
| 客户端壳与 UI | — | `pages/shell`、`components/{TabBar,TabAi,TabIndex,TabProfile}`、`pages.json` | 入口路由稳定性 | 冷编译 0 error |
| 脚本与部署 | `backend/scripts/*`、`scripts/*` | `deploy/**` | 覆盖度低、零外部依赖 | 自跑 + 手工核 |
| harness 工具 | `scripts/audit_harness.py`、`review_agent.py`、`test_agent.py`、`lessons.py`、`check_schema_drift.py` | — | 门禁自身正确性 | **已知样本自校验**（防假阳性） |

## 批次执行契约（dependency-and-code-hygiene）

- **分类**：routine 结构重构（小批保守）｜棘轮新增（门禁）｜死码/冗余（先证死再用）｜声明/注释漂移（文本级）。**架构边界变更**另走 `architecture-decisions`，不在本波默认范围内除非台账单独立项。
- **可逆性**：每批独立 commit（描述性 message + 批次号），可单独 `git revert`；不与他窗在途文件混提。
- **棘轮**：新轴**先基线冻结现状（白名单）**，只拦新增；违规数只降不升；每条白名单须带原因与销项触发。
- **死码**：删除前须 grep 引用 + 确认调用方 + 可回滚；公开导出零调用 = CRITICAL（沿用 `audit_harness exports` 口径）。
- **Codemod/批量改**：先抽样输出、限定路径域、跑受影响测试。

---

## ADDED Requirements

### Requirement: 逐功能域重构台账（Refactor Ledger）

系统 SHALL 产出一份逐功能域的重构台账，作为本波**唯一排期事实源**；台账为只读筛查产物，**不含任何业务代码改动**。

#### Scenario: 台账覆盖面完整
- **WHEN** 14 个功能域审计完成
- **THEN** 每个域均有条目，且每条含：位置(文件:行)、类型(巨文件/重复/死码/边界/声明漂移/门禁缺口)、严重度(P0/P1/P2)、收益、风险、可回滚性、验证手段、建议批次

#### Scenario: 与既有登记去重对账
- **WHEN** 台账合并去重时
- **THEN** 必须与 `docs/决策台账.md §7`、`docs/待办处理排期_20260923.md`、`docs/后端重构与性能优化计划_20260910.md`、`docs/审计_未关闭缺陷_20260923.md` 逐条对账，**已登记项标注来源而不重复立项**

#### Scenario: 未验证不臆断
- **WHEN** 条目证据不足
- **THEN** 标注为"候审（待验）"，不得写成结论；审计工具/结论须先用已知样本自校验（先例：`audit_harness` 曾误报 44 条死路由）

### Requirement: 行为等价重构批次

系统 SHALL 以**小批、行为等价、可回滚**的方式执行台账项，每批通过门禁后方可提交。

#### Scenario: 批次行为等价
- **WHEN** 执行任一批次
- **THEN** 受影响测试绿 + 全量 `pytest` 不红；涉 API 出参时契约 `audit_harness openapi` 仍"只增不减"；涉客户端时冷编译 0 error；**不得改变任何用户可见行为**

#### Scenario: 发现缺陷只登记
- **WHEN** 重构过程中发现功能性/安全缺陷（如 `决策台账 §2.15` 空串绕过签名密钥）
- **THEN** 本波**不修**，登记为缺陷单/台账待办，交由功能波处理（保持本波行为等价边界）

#### Scenario: 避开在途文件
- **WHEN** 动手编辑任一文件前
- **THEN** 先核 `git status` 与 `AGENTS.md`「新勿碰名单」（第四窗在途 8 文件：`media.py`/`media_url.py`/`events`/`config`/`errors`/`main`/`content-schema`/`storage`），命中则跳过并在台账注明

### Requirement: 门禁棘轮（防退化）

系统 SHALL 为结构债建立可执行棘轮，先基线冻结现状、只拦新增，使已清债务不可回潮。

#### Scenario: 文件体积棘轮
- **WHEN** 新增或修改文件超过阈值（建议 services/api 类 ≤600 行、`.uvue`/`.uts` ≤800 行）
- **THEN** 门禁报 CRITICAL 并阻断；存量超阈文件进基线白名单，销项时逐个移除

#### Scenario: 契约路径只增不减
- **WHEN** `docs/openapi.json` 相对基线路径集合出现"消失"
- **THEN** 门禁报 CRITICAL（落实 `决策台账 §2.14` 的建议）

#### Scenario: 归属校验一致性
- **WHEN** 新增/修改按 id 路由的资源访问端点
- **THEN** 必须经既有归属 helper 且带软删过滤（沿用 `0efd838` AST 门禁），否则阻断

### Requirement: 声明漂移清零

系统 SHALL 清除"文档/注释宣称与实际不符"的漂移项，并纳入扫描。

#### Scenario: 三处已知漂移
- **WHEN** 清理完成
- **THEN** `client/pages/shell/shell.uvue:7` 陈旧注释、`client/utils/search_api.uts:183` 陈旧 mock 叙述、`feature_list.json` 同条自相矛盾证据 均已修正，且 `audit_harness client` 无新增漂移

## MODIFIED Requirements

### Requirement: 提交前审核门禁（Pre-Commit Review Gate）

原门禁（快速=hook/`review_agent`；全量=`--full`）**保持**，本波**增补**结构轴：提交前除语法/lint/密钥/TODO/lessons 外，须通过 `audit_harness` 新增的结构轴（体积/重复/契约路径/声明漂移），违规按棘轮规则判定。

#### Scenario: 结构轴接入提交门
- **WHEN** 任一重构批次提交
- **THEN** `review_agent`（或显式 `audit_harness`）结构轴无"新增"CRITICAL，方可通过

## REMOVED Requirements

无。本波不删除任何既有能力或契约（`agent/` 相关 AG7 撤销豁免亦不在本波范围）。

---

## Fitness Functions（架构不变量，可测）

| # | 属性 | 指标 | 阈值/规则 | 度量源 | 频次 | 失败响应 | 本地检查路径 |
|---|---|---|---|---|---|---|---|
| FF1 | 依赖方向 | services 反向 import api 的边数 | = 0 | 静态 AST/grep | 每批+CI | 阻断 | `audit_harness`（新增轴） |
| FF2 | 契约兼容 | openapi 路径集合相对基线消失数 | = 0 | `audit_harness openapi` | 每批+CI | 阻断 | `python scripts/audit_harness.py openapi` |
| FF3 | 行为等价 | 全量 pytest + 客户端冷编译 | 全绿 / 0 error | `test_agent` / 编译门 | 每批 | 阻断 | `python scripts/test_agent.py` |
| FF4 | 文件体积 | 超阈文件数（存量白名单外） | 新增 = 0 | `audit_harness`（新增轴） | 每批 | 阻断 | `python scripts/audit_harness.py` |
| FF5 | 零调用导出 | 能力类导出零调用数 | = 0 | `audit_harness exports` | 每批 | 阻断 | `python scripts/audit_harness.py exports` |
| FF6 | 归属一致性 | 按 id 访问缺归属/软删过滤的端点 | = 0 | AST 门禁 `0efd838` | 每批 | 阻断 | `pytest`（authz 族） |
| FF7 | 声明漂移 | 死路径/失实宣称命中数 | 新增 = 0 | `audit_harness client` + 人工 | 每批 | 阻断 | `python scripts/audit_harness.py client` |

## 风险登记（Risk Register）

| # | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R-1 | 触碰他窗在途未提交文件（§F-3） | 中 | 高 | 编辑前 `git status` + 命中"勿碰名单"即跳过并登记 |
| R-2 | 客户端编译门不可用（HBuilderX GUI + Docker） | 中 | 高 | 先探门；不可用则客户端批次顺延，只做后端+harness+文本 |
| R-3 | 棘轮假阳性污染台账 | 中 | 高 | 新轴**先用已知样本自校验**，再基线冻结 |
| R-4 | 巨文件拆分引入隐性行为差异 | 中 | 高 | 纯移动 + 兼容再导出（沿用 upload/ 子包先例）+ 测试护航 |
| R-5 | 范围蔓延（重构波漂成功能开发） | 高 | 中 | 严格行为等价；发现缺陷只登记（`决策台账 §2.15` 即此类） |
| R-6 | AG8 遗留表清理误删数据 | 低 | 高 | 先证零引用 + 备份 + 可回滚迁移；不确定则只登记不删 |
| R-7 | 台账体量过大不可拍板 | 中 | 中 | 按"收益/风险"折叠为 P0/P1/P2 + 批次建议，明细可下钻 |

## Follow-up（最多 2 项）

1. **`architecture-decisions`**：若台账出现"需改动模块边界/依赖方向"的条目（如 api 层职责过载），另开 ADR 立决，不在本波默认执行。
2. **`testing-and-quality-gates`**：若新增结构轴与现有 CI/门禁编排冲突，另议门禁接线方式。