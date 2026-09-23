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

## 2. P0 · 巨文件（阻碍改动，最优先）

| # | 位置 | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次 |
|---|---|---|---|---|---|---|---|---|
| L-01 | `client/components/RecordSheet/RecordSheet.uvue` **2208 行**（script 1095 / style 857 / tpl 251） | 巨文件 | P0 | 记录面板是本应用最高频交互；拆分后单点改动不再触碰 2 千行 | 高（交互核心，回归面大） | 纯移动+兼容 | 冷编译 0 error + 真机记录链路 | B5 |
| L-02 | `client/components/TabIndex/TabIndex.uvue` script **848 行** | 巨文件 | P0 | 时间轴主页逻辑与模板纠缠 | 高 | 纯移动 | 冷编译 + 真机 | B5 |
| L-03 | `backend/app/api/contents.py` **902 行**（手抄软删模式 8 处） | 巨文件+重复 | P0 | 内容域为后端最热 API；拆分+走 helper 降回归面 | 中（有 contents 测试族护航） | 纯移动+兼容再导出 | pytest contents/content_upload/photo_content + `audit_harness openapi` | B2 |

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
| L-11 | `audit_harness.py:280` 轴 3 把**镜像表**判为 CRITICAL：`KnowledgeCollection`/`MemoryCategory`（`models/memory_agent.py`）实为**刻意镜像**——docstring 明载"表若只存在于迁移、不进 `Base.metadata`，`--autogenerate` 会生成 DROP TABLE"，且有 `test_agent_schema_alignment.py` 守卫 | 门禁缺口 | P1 | 当前每跑一次轴 3 就有 1 条假 CRITICAL → **假阳性会污染台账**（先例 44 条假死路由） | 低 | 新增工具 | 自校验 + `alembic check` 仍绿 | B1 |
| L-12 | 客户端 `.uvue` **style 块合计 7391 行 / 32 文件**，而 `client/design_tokens.json` 存在 → 设计令牌未收敛到样式 | 重复（候审） | P1 | 视觉一致性/改主题成本 | 中 | 行为等价替换（仅样式） | 冷编译 + 目视 | B5 |

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

- `agent/`（独立改写批次）、`research/`（评测语料）；功能性/安全缺陷（§2.15、RAG 重校、推送真实通道、图搜评测）→ 交功能波。
- 架构边界变更（如需调整依赖方向/拆服务）→ 另开 `architecture-decisions` ADR。

## 8. 执行结果（2026-09-24 回填）

| 批次 | 条目 | 状态 | 证据 / 提交 | 备注 |
|---|---|---|---|---|
| B1 | L-10 结构轴 + L-11 镜像豁免 | ✅ 完成 | `6acd826`；`audit_harness all` → **无 CRITICAL**（原 1 条假阳性已消） | 新增轴 5 体积 / 轴 6 重复模式 + `review_agent` 接入 `structure` 检查；基线 `scripts/audit_harness_baseline.json`；负向探针（清空基线 → 体积 7 条 / dup 1 条 CRITICAL）证明非空转 |
| B3 | L-09 归属校验覆盖 | ✅ 完成（**验证即达标，无代码改动**） | `pytest backend/tests/test_authz_gate.py -q` → **6 passed** | AST 门禁（`0efd838`）确认所有按 id 路由端点均经归属 helper、无漏网 —— **无缺口可补**。残余的"三 helper 命名/家收敛"（`deps.load_alive_content` / `messages.load_owned_message` / `capsules._load_owned_capsule`）属**质量改进**、非正确性缺口，登记为后续项（动它需同批改多个消费点，宜单独窗口） |
| B6 | L-17 声明漂移 | ✅ 完成（**验证即达标，无代码改动**） | `audit_harness client` → **无注释内未注册页面引用** | 该两处注释原文已含「对齐**原** …」，B1 的「原」标注抑制生效；连同 §3.1 三则（09-23 已修）＝漂移清零 |
| B7 | L-14/15/16 脚本可移植性/凭据卫生 | ✅ 完成 | `09708d7`；ruff 0 / py_compile 0 / 无凭据路径实测 exit 2 / `backup_pg.ps1` PowerShell 解析 0 错 | 个人绝对路径清理；PGBin 探测 + 显式报错；`check_schema_drift.py` 移除内联 `postgres:admin` 默认凭据 |
| B4 | AG8 `alembic check` 漂移 | ⏸ **仅登记（未改）** | 实跑 `python -m alembic check`（DATABASE_URL=yishu_app@localhost/yishu）：漂移**量大且含破坏性项**（大量 `modify_type` TEXT↔String、多表 `remove_fk` / `remove_index`、索引改名） | 按 B4 预设口径「不确定则只登记不删」——**自动生成迁移会 DROP 外键/索引**，属可删数据/破坏约束的静默事故。**待拍板**：`schema.sql`（CI 建库源）与 ORM/alembic 谁是权威，再定"补 drop 迁移"或"ORM 补声明对齐" |
| B2 | L-03 `contents.py` / L-07 `events.py` 拆分 | ⏸ **顺延（本会话未执行）** | 仅完成勘察：`contents.py` 实为 **4 router 混居**（`contents` / `profile_sensitive`（454-514）/ `favorites`（641-689）/ `trash`（690-814）/ waveform（815-902）） | 顺延理由（工程判断，非偷懒）：① 天然拆法＝转 `api/contents/` 子包（沿用 `upload/` 先例）；② 部分抽取（如只移 profile_sensitive）后 **902→~845 仍 >600**，棘轮不解除、纯增文件，收益为零；③ 完整 4-router 迁移≈900 行移动 + 多处 `_to_out`/`_attach_thumb` 共享符号与测试 monkeypatch 面，须**专用验证窗口**；④ 棘轮已冻结该文件，**不产生新债**。L-07 `events.py` 530 行**低于 600 阈值**，非棘轮项、优先级更低 |
| B5 | L-01/02/04/05/06/08/12 客户端拆分 | ⏸ **顺延（用户拍板）** | — | 编译门（HBuilderX GUI + Docker）不可用 → 用户拍板顺延，避免"未验证改动堆进最脆弱链路" |

### 8.1 本会话新增/变更的棘轮状态

- `audit_harness all`：**无 CRITICAL**（INFO 14）；轴 5 存量超阈 **7 个已冻结**；轴 6 `soft_delete_filter` **44 处未上升**。
- 门禁接入：`review_agent` 新增 `structure` 检查（快/全量均跑，秒级）；既有 hook 自动覆盖，**未使用 `--no-verify`**。
- 基线收缩触发：B2 拆分落地后删除 `contents.py` 条目；B3 收敛落地后下调 `soft_delete_filter` 计数。