# Checklist

> 验证对象：易扩展/易维护重构波。**判据一律以代码/命令输出为证据**（`docs/**` 的"已完成"宣称不作为证据）。
> **本会话执行范围**：Phase A（A1–A7）+ B1 / B3 / B4 / B6 / B7 / B8；**B2 与 B5 顺延**（理由见 `refactor-ledger.md` §8）。
> 勾选附证据；未执行项显式标 **N/A 并注明原因**，不静默通过。

## A. 方法与本波边界

- [x] 本波范围为 `backend/app/` + `client/` + `scripts/` + `deploy/`，且 **`agent/` 未被改动** —— 证据：`git show --stat 6acd826 09708d7` 仅 `scripts/**`；`git status` 无 `agent/` 变更
- [x] 本波**未改变任何用户可见行为** —— 证据：改动集＝`scripts/{audit_harness,review_agent}.py` + 新基线 JSON + `scripts/{agg_load_real_photos,backup_pg,check_schema_drift}`；无 API/页面/文案语义变化（`--full` 门禁 tests ✅）
- [x] 重构过程中发现的**功能性/安全缺陷未被就地修复**，而只被登记 —— 证据：AG8 漂移走"只登记"分支并登记 `决策台账 §5.9`；`§2.15` 未触碰
- [x] 每条台账项含 位置/类型/严重度/收益/风险/可回滚性/验证手段/批次归属 八字段，且"候审"与"确认"分离 —— 证据：`refactor-ledger.md` §2/§3/§4 表头
- [x] 台账已与 `决策台账 §7`、`待办处理排期_20260923`、`后端重构与性能优化计划_20260910`、`审计_未关闭缺陷_20260923` 对账，无重复立项 —— 证据：`refactor-ledger.md` §5 对账表

## B. 门禁与棘轮

- [x] `scripts/audit_harness.py` 新增结构轴可运行，且**首次运行以已知样本自校验通过** —— 证据：负向探针（清空基线）→ 体积 **7 条** / 重复 **1 条** CRITICAL；真实轴 → **无 CRITICAL**。**未出现** 假阳性类问题（并修掉 1 条既有镜像假 CRITICAL）
- [x] 新增轴采用**基线白名单冻结存量、只拦新增**；每条白名单带 reason/target —— 证据：`scripts/audit_harness_baseline.json`（7 条体积 + 重复模式，均带 reason/target）
- [x] `audit_harness openapi`：路径集合**消失数 = 0** —— 证据：`67/67 完全对齐`、`后端路由全部已在契约中 ✓`、`契约无幽灵路径 ✓`
- [x] `audit_harness exports`：能力类导出零调用数 **= 0** —— 证据：`无零调用的能力导出 ✓`
- [x] 归属校验 AST 门禁：按 id 路由端点缺归属/软删过滤数 **= 0** —— 证据：`pytest backend/tests/test_authz_gate.py` → **6 passed**
- [x] 新增/修改文件均未超阈值，或已进白名单并登记销项触发 —— 证据：轴 5 `无新增超阈文件 ✓（存量超阈 7 个已冻结）`；`contents.py` 条目带 target

## C. 分批执行

- [x] 每个批次为**独立提交**，可单独 `git revert`；提交 message 含批次号 —— 证据：`6acd826`（B1）、`09708d7`（B7），均含批次号与 `Refs: spec/...`
- [x] 每批提交前 `review_agent` 快速门禁通过；完成声明前 `review_agent --full` 通过 —— 证据：hook `[pre-commit] 审核通过`；`--full` → `✅ 审核通过`（syntax 362 / lint / secrets / structure / tests / research 全 ✅）
- [x] 每批受影响测试绿 + 全量 `pytest` 不红 —— 证据：基线 `848 passed 4 skipped`（82s）；`--full` 的 tests 段 ✅
- [x] 客户端批次冷编译：编译门不可用 → **批次已顺延并登记**，未以"未验证"冒充完成 —— 证据：用户拍板"不可用，顺延"；`refactor-ledger.md` §8 B5 行
- [x] 巨文件拆分采用**纯移动 + 兼容再导出** —— **N/A**：本会话未执行任何巨文件拆分（B2 顺延），此项留待 B2 专用窗口验证
- [x] 编辑前已核 `git status` 与"勿碰名单"，**未触碰在途未提交文件** —— 证据：会话起始 `git status --short` 仅 `?? .codebuddy/` + `?? .trae/specs/`；全部提交按 pathspec 限定

## D. 收口与留痕

- [x] 每批踩坑已 `python scripts/lessons.py add` 登记 —— 证据：登记"基线 schema 不匹配致棘轮崩溃"1 条（`docs/lessons.md`）
- [x] `docs/决策台账.md` 已回填本波进度与口径/待拍板变化 —— 证据：新增 `§4.13`（本波决策）与 `§5.9`（schema.sql vs ORM 权威待拍板）
- [x] `progress.md` 已按实际结果更新 —— 证据：末尾追加 `2026-09-24 速查卡`（CRLF/无 BOM 保持）。**`feature_list.json` 无需变更**：本波不改任何特性状态（无功能开发）
- [x] 仓库仍可从标准路径重启：`./init.sh` 可运行 —— 证据：`bash ./init.sh` → `=== 初始化通过 ===`（18 份交付文档 + harness 文件齐备）
- [x] 未使用 `--no-verify` 绕过任何门禁 —— 证据：两次 commit 均经 hook `[pre-commit] 审核通过`

## E. Phase A 深度审计复核（2026-09-24 · **修订前述 A 部分的勾选依据**）

> **背景**：本清单原 A 部分在上一轮**未完成深审**的情况下被勾选（上一轮 7 个并行 subagent 中途全部挂掉，实际只做了行数/grep 计数的浅层筛查）。本轮以**抗断连策略**（串行小批量、每批 2 域、单域一 agent、独立报告文件）重启并**完成 14 域深审**。

- [x] **14 功能域**逐域完成深度审计，每域一份独立报告 —— 证据：`audit/domain-01-auth-device.md` … `audit/domain-14-harness-tools.md`（14 份齐备，`LS` 已核）
- [x] 每域报告含 rubric 八字段表 + **逐条可复现证据**（读码行号 / Grep 搜索式与结果），"候审"与"确认"分离 —— 证据：各报告表头含 位置/类型/严重度/收益/风险/可回滚性/验证手段/批次建议/状态；候审 30 条已标注
- [x] 审计产出**统一汇总**，与既有登记对账去重 —— 证据：`audit/_PHASEA_ROLLUP.md`（249 条 = 结构 185 + 功能 64；P0 17/P1 111/P2 121）；含 §4 差异专章、§5 扩展成本表、§6 端云一致性
- [x] 结论已**并入台账并覆盖浅层条目** —— 证据：`refactor-ledger.md` **§9**（§9.1 P0 清单 / §9.2 功能缺陷移交清单 / §9.3 旧条目修正 / §9.4 B2 最小方案 / §9.5 扩展成本 / §9.6 端云一致性 / §9.7 A7 待拍板）
- [x] **旧条目修正 4 处**已落实（不能以"上轮已勾选"为准）—— 证据：① 软删 44/21 口径确认为生产口径（另有 tests/scripts 5 处）；② L-11 镜像守卫 `test_agent_schema_alignment.py` 为**幻影**（全仓+git 全历史 0 命中）已就地更正；③ B3「验证即达标」**证伪**（门禁哑条目 D06-2），ledger §8 与 tasks.md 均已降级；④ L-12 由 P1 候审**升 P0 确认**（令牌机制覆盖率 0%）
- [x] 全部深审**未修改任何业务代码**（本波行为等价边界）—— 证据：各域报告一致记录 `git status --short` 仅 `?? .codebuddy/` + `?? .trae/.../audit/`
- [x] 审计发现的**功能性/安全缺陷 64 条只登记不修**，并分离为**移交功能波的清单** —— 证据：ledger §9.2（含 P0 10 条 + 三个必修簇），§7「本波不覆盖」已指向该表
- [ ] **A7 提请用户拍板**：结构类批次（B2/B3′/B9/B10/B11/B12/B5）优先级 + 功能缺陷归属波次 + `schema.sql` vs ORM 权威 —— **待用户确认**（ledger §9.7）
- [x] 在途文件复核：AGENTS「第四窗 8 文件勿碰名单」在本时点**均已入库**（在途=0），名单可解除 —— 证据：14 域报告一致记录 + `git status --short`

## F. Phase B 第二轮执行（2026-09-24 · 深审后新增批次）

- [x] **B2 巨文件拆分**：`backend/app/api/contents.py` → `api/contents/` 子包（`git mv` 保历史）—— 证据：`__init__.py` **902 → 524 行**（<600，轴 5 棘轮销项，存量超阈 7→6）；子模块 serializers 48 / profile_sensitive 89 / favorites 111 / trash 115 / waveform 115
- [x] 拆分**行为等价**：AST 逐语句比对原 902 行 → **31/31 顶层语句文本完全一致**；`MAX_PHOTO_BYTES`/`enqueue_unique` 与 6 个核心端点留在 `__init__.py`（monkeypatch 面）；唯一可观测差异＝favorites logger 通道名变化（未改代码）
- [x] B2 **门禁覆盖回归已修**（自查发现）：`test_authz_gate.py` `glob("*.py")` → `rglob("*.py")` —— 证据：候选 20+ → **29**；已扫到子包模块 `contents/{__init__,favorites,trash,waveform}.py`；新增 `test_scan_covers_subpackages` 防回归
- [x] **B3′ 归属 helper 收敛 + 门禁哑条目修正**：`deps.load_owned_entity` 泛化 + 三处委托（错误码/文案/HTTP 逐字保留）；`OWNERSHIP_LOADERS` 修正真名 `_load_owned_capsule`、删 3 幻影预留项 —— 证据：新增 `test_ownership_loaders_all_exist`；自校验 `'load_owned_capsule' in live → False`
- [x] **B10 门禁加固**：`audit_axes`（轴 1–4）接入 `review_agent`（快/全量）；CI full-gate 加阻断式 `audit_harness all`；重复模式总数+per_file 双 gate；镜像豁免收窄；体积阈值由基线驱动 —— 证据：负向探针 → `exports` **CRITICAL** + `review_agent` **退出码 1**，已还原
- [x] **B12 部分**：**D13-1 恒绿假门禁退役**（原 CI client tsc 步骤：`continue-on-error: true` + 末尾**无条件 `exit 0`**，且 `client/tsconfig*.json` 不存在、tsc 不认 `.uts`）→ 替换为**阻断式** `audit_harness client`；D13-26 deploy/README 补齐；硬编码路径清 4 处（env + 显式默认 + 缺失报错）
- [x] **B11 安全子集**：D04-17 类型标注 / D13-28 docstring / D13-14 注释一致性 / D14-2 死配置（随 B10）
- [x] 每批受影响测试绿 + **全量 `pytest backend/tests` 不红** —— 证据：**850 passed / 4 skipped**（较基线 848 = 新增 2 条门禁自检，零回归）；`audit_harness openapi` **67/67 完全对齐、无幽灵路径**
- [x] 未以"未验证"冒充完成：**B9（端口/边界收口）、B10-b（门禁缺口补强）、B11 残余漂移、B12 剩余项、B5（编译门）** 均显式标注**待执行/顺延**并给出原因（ledger §8.2/§9.7）
- [x] **B10-c 错误码反向棘轮**（D11-5）：新增 `test_no_new_dead_error_codes`（双向判定）+ `DEAD_CODE_BASELINE`（3 枚带原因/销项触发）—— 证据：实测 `registered − raised = 恰好 3 枚`（67 注册 / 64 使用）；**内存级负向探针**（模拟新增 `ZZZ_PROBE_999` → 判为基线外新增）证明非空转，且**不改任何业务文件**
- [x] **D07-13 改判非缺陷**（复核修正深审结论）：实读 `validate()` **仅测试调用**、生产 `get_schema()` 不调它 ⇒ 属测试期契约断言，51/193 即规格契约 —— 证据：调用点实扫（`test_profile_annotator.py:48` vs `annotate.py:52`/`interview.py:144`/`profile_annotator.py:78,136`）；已回填 ledger §9.3 第 5 条
- [x] **B10-d 轴 4 假阴性修正**：`^\s*export` 的 `\s` 含换行 ⇒ 声明行排除失配 ⇒ 每个导出把自身计 1 引用 ⇒ `zero_cap` **应为 4 却恒为 0**（轴 4 长期假绿）—— 证据：298 导出中 56 个声明行偏移；修为 `^[ \t]*` 后露出 4 枚（含 3 枚深审未涵盖的新发现）；负向探针（前导空行新增导出 → CRITICAL，**行号正确**）已还原
- [x] **B10-e 基线僵尸豁免清算**（D14-4 的另一半，口径同轴 4）：filesize 对"已不再超阈"的基线条目报 CRITICAL；dup 对 `per_file` 已归零条目报 CRITICAL —— 证据：**先逐条对账**（6 filesize + 21 dup per_file 全 OK、total 44=44）确认不引入误红；双负向探针（阈值抬高 ⇒ 6 条僵尸 CRITICAL；塞不存在文件 ⇒ 1 条 CRITICAL）均 EXIT=1，基线按原文**精确还原**（断言 True）
- [x] 未使用 `--no-verify` 绕过门禁 —— 证据：提交经 hook `[pre-commit] 审核通过`
- [x] **B10-f 轴 4 覆盖面补全**（D14-16）：`glob("*.uts")` → **`rglob("*.uts")`**（子目录不再漏扫）+ `EXPORT_RE` 补 `default`/`type`/`interface`/`enum` —— 证据：先量化（补递归 +29 导出、其中零调用 1＝`WALK_SPEED_MS`＝深审 D04-12；补 7 类 +5 导出、零调用 0）；修后导出 **298 → 332**、存量 5、无新增；**子目录负向探针**（追加零调用导出）→ CRITICAL 且行号正确、已还原
- [x] **B10-g 契约快照再生 + 断言**（D14-7）：新增 `scripts/gen_openapi.py`（写入/`--check`）并接入 `review_agent` 的 `openapi_snapshot`（0 放行 / 1 阻断 / 2 环境降级）—— 证据：`--check` 证明现有快照与后端**逐字节一致**（239518 字符，此前无法证明）；负向探针（塞幽灵路径）→ `[FAIL]` 且精确指出「快照有而后端无（1）」、EXIT=1、门禁阻断；再生还原后与 HEAD 逐字节一致
- [x] **B10-h 静默转绿 + 覆盖率双阈值**（D14-19 / D14-8）：缺 ruff/pytest 改为**默认阻断**（`--allow-missing-tools` 才可见放宽）；覆盖率阈值收敛为单一 `COV_THRESHOLD = 60` —— 证据：内存级探针（monkeypatch `run`、**不改文件**）证明 argv 阈值 = 60、缺 ruff/缺 pytest 默认 `ok=False`、放宽路径 `ok=True` 且带 `[放宽]` 标记
- [ ] ⚠️ **待环境验证（如实标注，未以未验证冒充完成）**：`COV_THRESHOLD` 由 50 调为 60 后**实际覆盖率是否 ≥60 无法在本机确认**（Docker/Qdrant 未起，全量测试不可跑）—— 须 Docker 可用后跑一次 `--full`；CI 本就以 60 为准
- [x] **B10-j 轴 2 注释判定精确化**（D14-14 / D14-15）：`_comment_ranges()` 字符级区间（跳过字符串字面量）+ `_in_comment(offset)` —— 证据：纯函数对照六例证明**旧实现既误报**（块注释 / 多行 HTML 注释内页面引用被判死路由）**又漏报**（字符串 `http://x` 之后被判为注释 ⇒ 真死路由漏报）；修后六例判定全部正确；文件级负向探针（追加真实死路由）→ CRITICAL 且行号正确、已还原
- [x] **B10-k 退出码口径统一**（D14-18）：`review_agent` 落地 0 通过 / 1 违规 / **2 环境错误**（`ENV_ERR_PREFIX` 打标 + 纯函数 `_classify_failure()`，违规优先）+ 报告 `env_blocked_checks` —— 证据：纯函数探针 5 例全对（含"违规+环境错误 → 违规优先"）；env 分支消息均以 `[环境错误]` 开头；CLI 实跑 `✅ 审核通过` 且退出码 0
- [x] **B10-l 密钥模式集唯一来源**（D14-9）：`scripts/secret_patterns.py`（并集 16 条）被 `review_agent` 与 `audit_security` 共用 + `pragma: allowlist secret` 显式行内豁免 —— 证据：探针证明同源、**并集缺失 = `[]`**、四类形态全覆盖；放宽后当场暴露 4 处合成值误报并逐行标注；静态全绿、`test_guard_managed` 9 passed
- [x] **B10-m `audit_security` 崩溃 + 白名单失效**（既有缺陷，实跑坐实）：修 `db/models` 拆包直读崩溃（`HEAD` 版本同样崩 ⇒ 非本轮引入）、`_ALLOW_PATHS` 改按路径段匹配、统一合成值抑制 —— 证据：**首次真正跑通**，`blocking` = **1**；⚠️ **真实发现交运维/用户**：**最近备份距今 493.0h（≈20.5 天）违反 RPO≤24h**（此前因崩溃从未被评估）
- [x] **B10-n UTF-8 兜底统一**（D14-11）：新增 `scripts/gate_io.py::force_utf8()`，16 文件 / 20 处全迁移（6 种形态）—— 证据：`scripts/` 全目录 ruff 通过；17 脚本 `py_compile` 通过；残留 `reconfigure` 仅 `gate_io.py` 自身；门禁工具实跑全绿
- [x] **B10-o 退出码口径单一来源 + `test_agent` 姊妹假绿**（D14-18 残余 / D14-19 姊妹 / D14-13 半）：`scripts/gate_exit.py` 被两工具共用；`test_agent` 缺依赖由**静默 True**改为**环境错误（退出码 2）**；`audit_harness` 排除集合并 —— 证据：探针 `classify_failure` 4 例全对（含违规优先）、`R.ENV_ERR_PREFIX == GE.ENV_ERR_PREFIX`、`T.gate_exit is GE`、源码断言无 `[skip] 缺依赖`；三工具实跑正常
- [x] **B9a 事件域门面收口**（D04-16）：API 不再直连算法包 —— 证据：`44a837c`；ruff 通过；`test_event_sync + test_agg_reference` → **23 passed**；grep 复核 API→`event_aggregation` 直连 = 0
- [x] **B9b rag 的 LLM 调用走 `llm_ops` 门面**（D05-13）：`rewrite_query` + 新增 `llm_ops.image_caption` —— 证据：`32b4a89`；5 个测试文件 **79 passed**；rag→`external.dashscope` 直连 = 0
- [x] **B9c 存储注册表 + COS 唯一构造点**（D02-3 / D02-13）：新增后端只改 1 行；API 不再直读 COS 私有字段 —— 证据：`8286327`；**探针 8 项断言全过**（含 fake 容量守卫仍生效、源码断言唯一构造点）；4 个测试文件 **83 passed**

## G. Phase B 第三/四轮（编译门恢复后 · 客户端 B5a/B5b）

- [x] **B10-p 修 B10-n 引入的导入回归**（Docker 起后全量门禁当场抓到）：5 个"会被按包导入"的脚本 `from gate_io import …` 在其路径下 `sys.path` 无 `scripts/` → `ModuleNotFoundError`（pytest 收集中断）—— 证据：改双路径导入后「按包导入」冒烟五处全过、受影响测试 **21 passed**、`test_agent --only research` ✅；教训已登记（`py_compile`/`ruff` 查不出导入期缺模块）
- [x] **编译门恢复**（用户手动启动 HBuilderX）：冷编译 `& D:\HBuilderX\cli.exe launch app-android --project "D:\GuangH-App\client" --compile true` → `项目 client 编译成功`；**B5 由"顺延"解除**
- [x] **B5a `play.uts` 按域拆分**（L-04）：653 行 / 6 域 → 6 域模块（`play_echo` 70 / `play_interview` 125 / `play_messages` 182 / `play_favorite` 53 / `play_trash` 63 / `play_content` 181）；10 调用点改写 —— 证据：逐字节等价（6 模块体 == `HEAD:play.uts` 对应切片；导出 28=28；2 私有 helper 未外泄）+ 残留 grep = 空 + **冷编译成功（0 error）**；提交 `000101f`
- [x] **B5b `sync_client.uts` 按职责拆分**（L-05）：705 行 → 5 模块（`sync_types` 77 / `sync_queue` 84 / `sync_local` 86 / `sync_pipeline` 291 / `sync_schedule` 101）；6 调用点改写（`App.uvue` 保相对路径）—— 证据：逐字节等价（5 模块体按原位置拼接 == `HEAD:sync_client.uts` 63–705，非空行 639 逐行一致、非空白行零丢弃；导出 22=22）+ 残留 import grep = 0 + **冷编译成功（ready in 170871ms，0 error）**；表面增量如实登记（6 常量 + 4 函数由私有改 `export`，`sync_local` 空导出若漏必编译失败、已复跑修正）
- [x] ⚠️ **UTS 平台约束坐实**（影响拆分策略）：`client/utils/*.uts` **不支持** `export { … } from './x.uts'` 再导出（Rollup 视目标为 JS ⇒ `[plugin:uts] Expression expected`）∴ "门面兼容再导出"**不可行**，一律改为调用点按模块直接导入 —— 证据：B5a 首版门面编译失败实测；B5a/B5b 均以调用点改写落地
- [x] **B10-q 轴 4 注释计数假阴性**（B10-d 残层 · 由 B5b 当场暴露）：`audit_exports` 裸 `\bname\b` 全文本计数**不剔注释** ⇒ 注释里提到导出名也算引用 ⇒ 真死码被掩盖；修为复用轴 2 的 `_comment_ranges()`/`_in_comment()` 剔除注释 + 预读缓存 —— 证据：**A/B 量化**零调用能力导出 **4 → 9**（新露出 `AGG_CHECK_ON_DEVICE`/`invalidateTimelineCache`/`isIgnoredEvent`/`parseErrorString`，逐枚 grep 证实代码零引用）；4 枚按棘轮冻结进 baseline；**反向探针**（导出仅在自己 doc 注释被提及）→ A 漏判（`zero_cap=4`）／B 捕获（`zero_cap=10`）且门禁 `[CRITICAL] 新增` @ `:6`、EXIT=1，已删探针；常规态 `无新增 ✓ / 存量 9 / 无 CRITICAL`；ruff 通过
- [x] **B5c `uploader.uts` 按分节拆分**（L-06）：628 行 → 6 模块（`uploader_types` 58 / `pending` 55 / `net` 59 / `queue` 120 / `photo` 93 / `batch` 185）；8 调用点改写 —— 证据：区间**连续覆盖 59–628**、拼接**逐行一致**（非空行 516 vs 516）；**13 个原私有符号自动提升 export**（脚本自动推导，原有 14 个 export 全保留，共 27）；残留精确 import grep = 0；**冷编译成功（51s，0 error）**；⑤ 唯一语义细节已分析：末尾 `onNetworkRestored` 钩子随 `maybeUploadHeldOnWifi` 落入 `uploader_batch`，启动链 `shell → TabIndex → uploadBatch` 仍开屏即注册 ⇒ **行为等价**；⑦ 轴 5 附带读数：导入 1→2 条使 `RecordSheet`/`TabIndex` 各 **+1 行**（WARN 非阻断），另 `TabIndex`/`detail`/`favorites` 基线本就落后实际 1~2 行（**先于本轮**，按"棘轮只许收缩"不上调，待 B5.2d 销项）
- [ ] ⚠️ **B5c 真机上传待补验**（如实标注，未以冷编译冒充真机验收）：`adb devices` **无设备** ⇒ L-06 要求的"真机上传"未跑
- [x] **B5d（金丝雀）`detail.uvue` 样式外置**（L-08）：`<style>` 591 行纯移动到 `client/styles/detail.css`，uvue 只留 3 行 `@import` ⇒ **1167 → 577 行**（轴 5 销项）—— 证据：css 内容**逐行一致**；**冷编译成功**；**产物取证**（`GenPagesDetailDetailSharedData.style.bytes` 含真实类名 `page-root`/`detail-scroll` ⇒ `@import` 被真正处理）；baseline 条目已删、`audit_harness all` 无 CRITICAL
- [x] **B5.2d 能力前置探针**（先验证平台能力再动手）：实测 ① ucss **支持** `<style>` 内 `@import '@/styles/x.css'`（**无需 scss 插件**，本机无 scss 插件）② `.uts` 组合式函数（`import { ref } from 'vue'`）**可**被 `<script setup lang="uts">` 引入并编译 —— 证据：单次冷编译同时验证两者；教训已登记（"编译通过 ≠ 样式生效，须查产物 bytes 类名"）
- [ ] ⚠️ **B5d 视觉复验待真机**（如实标注）：样式外置已由产物类名证明"被编译"，但**实际渲染效果未在真机看过**
- [x] **B5.2e（样式半）`RecordSheet`/`TabIndex` 样式外置**：沿用 B5d 已证机制，整块 `<style>` 纯移动 ⇒ `RecordSheet` 2209→**1354**（style 858→`record-sheet.css`）、`TabIndex` 1742→**1065**（style 680→`tab-index.css`）—— 证据：两 css **逐行一致**（846 vs 846 / 655 vs 655）；**冷编译成功**；**产物取证**（`RecordSheet.style.bytes` 含 `scrim`/`grab`、`TabIndex.style.bytes` 含 `header-title`）；baseline 计数**下调**（2208→1354 / 1740→1065）、`audit_harness all` 无 CRITICAL
- [ ] ⚠️ **B5.2f（script 半）未做**：`RecordSheet`(script 1097)/`TabIndex`(851) 的 script 抽 composable **需真机验收**（`adb` 无设备）⇒ L-01/L-02 **未闭环**；`favorites.uvue` 基线 974 vs 实际 975 为**先于本轮**的记账漂移（未上调，保持 WARN）
- [x] **B5.2f script 半已闭环（结构）**：`RecordSheet.uvue` 2209→**753**、`TabIndex.uvue` 1046→**738**（均 <800、均已销 axis-5 条目）；`TabSearch.uvue` 无需处理（本就未超阈）—— 证据：L-01/L-02 两销项（§8.11–§8.13）；两组件各子步均冷编译 `项目 client 编译成功`；**真机运行验收待设备**（如实标注，未以冷编译冒充）
- [x] **B5.2f-1 波形缓存 5 份同构副本 → `useWaveform` 组合式函数**（第八轮）：新增 `client/composables/useWaveform.uts::WaveformCache`，5 个组件（`TabIndex`/`TabSearch`/`detail`/`favorites`/`theme-detail`）改用它并移除多余 `fetchWaveform` import —— 证据：**等价性证明**（5 份块归一化后均为同一组 17 条逻辑行、composable 覆盖全部，缺失 `[]`）；**冷编译成功**（**首轮失败被当场抓到**：我的 import 重写脚本 `", ".join` 误写成 `" ".join` ⇒ detail 多符号 import 丢逗号 ⇒ `Unexpected token, expected ","`，已修）；行数合计 **−91**；轴 5 baseline **下调**（TabIndex 1065→1046 / favorites 974→957）、`audit_harness all` 无 CRITICAL
- [x] **B5.2f-2b `RecordSheet` 自定义标签助手 → `client/utils/custom_labels.uts`**（第十轮）：`readStoredCustomLabels`/`persistCustomLabels`/`customOn`+4 类名助手（原 `isCustOn` 读组件 `manualLabel` → 改入参 `manual`）；组件留 3 ref + 3 改状态函数；**刻意不与内建标签对齐选中语义**（自定义只看 `manual=='custom:'+名称`）—— 证据：`RecordSheet` 1135 → **1084（−51）**；4 模板调用点改带参；冷编译 ✅；轴 6 扫描 0 违规；baseline 下调 1135→1084
- [x] 🚨 **GAP-1 新发现（已补门禁）**：HBuilderX「项目 client 编译成功」**只覆盖语法/解析、不覆盖符号解析**——RecordSheet 有 **8 处符号完全没 import**（`useRecordAnimations` 2 + `custom_labels` 6）却连续两次报"编译成功"（对照：import 丢逗号的**语法**错误会被 `Unexpected token` 抓到）—— 补位：新增 `scripts/audit_client_imports.py`（剔注释/字符串误报，26→10→8；`.uvue` 只对 `<script>` 剔除以免模板漏报）+ `audit_harness` 新轴 **`client_imports`** + 接入 `review_agent.check_audit_axes`（快/全量均跑）；**端到端反向探针**：删 1 行 import → `review_agent` **❌ 阻断**并点名 `RecordAnimations`/`DotPt` → 字节级还原 → 复跑 ✅
- [x] **B5.2f-2c `RecordSheet` 模板取值封装 → `client/utils/record_ui.uts`**（第十一轮）：4 个无状态纯函数（`fmtRecTime`/`recSecFromMs`/`cellCls`/`imgCls`；注释与拍板记录逐字保留），`setInfoTime` 留组件 —— 证据：`RecordSheet` **1089 → 1054（−35）** 并**消除**上轮 import 造成的 +5 增长 WARN；冷编译 ✅；轴 6 0 违规；baseline 下调 1084→1054
- [x] 🧭 **方案 A 已拍板并由用户确认**（2026-09-24）：L-01/L-02 剩余部分走「整块 view-model 进 composable」
- [x] **B5.2f-2d（方案 A 子步 1 · 第十二轮）`RecordSheet` 语音录制状态机 → `useVoiceRecord`**：13 状态 ref + 6 状态机方法（163 行）迁入 composable（注入 `anim`/`onStopped`/`resetAiLabel`）；组件退化为薄绑定层（12 顶层绑定 + 6 薄包装）—— 证据：`RecordSheet` **1054 → 889（−165）**；冷编译 ✅；轴 6 0 违规；baseline 下调 1054→889
- [x] 🛠 **同轮修掉 4 个自己制造的缺陷**：轴 6 抓到"瘦身 import 误删 `recorderState`/`stopRecord` 但 `closeForm()` 仍用"；类内误留 `function`；内部互调缺 `this.`；改写**旧索引切割**错位（`Unexpected token (885:0)`）；另有自写"未注入标识符体检"抓出 `aiLabel` 漏注入 —— 教训：生成式改写必须配「静态体检 + 真实编译 + 轴 6」三道
- [x] **B5.2f-2e（方案 A 子步 2 · 第十三轮）语音收尾三函数迁入 → 🎯 L-01 销项**：`onRecordStopped`/`submitVoice`/`afterVoiceSaved`（147 行）+ `saving` 迁入 `useVoiceRecord`；撤销 `onStopped` 注入、新增 `emitSaved/emitClose/setVoiceStage/effLabel/remarkTrimmed/resetFormFields` 六项注入（逐一等价替换）—— 证据：`RecordSheet` **889 → 753（首次 <800）**；冷编译 ✅；轴 5 无 CRITICAL；**axis-5 baseline 条目按 gate 要求删除 ⇒ L-01 销项**；轴 6 0 违规
- [x] 🔎 **GAP-1 再强化**：非法类成员语法 `this.afterVoiceSaved(...): void {` **编译仍报绿**（客户端编译门连类成员非法语法都放过；而 `saving` 重复声明属绑定级、被抓到）⇒ **"编译 0 error"不能作为结构正确性证据**
- [x] 🚨 **GAP-2 新发现并补门禁 + 修 P0 回归**（2026-09-25）：复核 f333430 时发现 `useVoiceRecord.uts` **方法体写 `this.saving.value` 却漏声明 `saving` 字段**（2e 迁入漏声明）——**编译报绿（GAP-1）+ 轴 6 抓不到（它只查跨模块 import，`this.<名>` 是类成员访问）** ⇒ 两道门同时失守、真机 `submitVoice` 必崩；修复＝补 `saving = ref(false)` —— 证据：新增 `scripts/audit_class_members.py`（**轴 7**，覆盖 ①`this.<名>` ②`const x = new Cls(…)` 后 `x.<名>`；`extends` 类跳过、同名变量非「全部赋值皆 `new Cls(`」跳过）+ 接入 `audit_harness` 新轴 `class_members`（含 `all`）与 `review_agent.check_audit_axes`（快/全量）；**全仓 76 类 0 误报**（3 个 extends 跳过）；**反向探针两例**（删 `saving` → 报 `this.saving`；`pa.attachAll`→`pa.attachAllZZZ` → 报 `pa.attachAllZZZ`）**均 EXIT=1 且字节级还原**；教训已登记 `docs/lessons.md`；台账 §8.14
- [x] 🧭 **L-02（`TabIndex` 1046 → 800）——已销项**：分两子步按方案 A 抽 composable —— **L-02a** 照片路径/挂载/详情（含两张非响应式索引表 + 7 方法）→ `usePhotoAttach`（`attachPhotos()` 更名 `attachAll(days,pending)` 入参化）⇒ **1046 → 899**；**L-02b** 拆分四态 + 13 方法 → `useEventOps`（注入 `getCurrent`/`render`/`refresh`/`getDays` 四个取值函数）⇒ **899 → 738（累计 −308）** —— 证据：两子步**各跑一次冷编译**均 `项目 client 编译成功`（48.7s / 36.8s）；轴 6/7 0 违规；**axis-5 baseline 条目按 gate 要求删除 ⇒ L-02 销项**（存量超阈 4 → 3）；台账 §8.12/§8.13
- [x] 👁 **L-02b 顺带发现（登记交功能波，非本波修）**：模板内**已无**拆分面板与照片详情浮层 ⇒ `showSplitPanel`/`splitItems`/`splitLoading`/`confirmSplit`/`cancelSplit`/`toggleSplitItem`/`splitTimeText` 与 `showPhotoDetail`/`photoDetail*`/`closePhotoDetail`/`jumpFromPhoto` 均为**模板不可达 UI 状态**（用户可经「⋯」触发 `doSplit`，但无节点渲染该面板）= **功能缺口**；本波只纯移动、按原样搬迁并留登记 —— 证据：全仓 grep 这些符号仅命中 `TabIndex.uvue` 自身声明/使用，模板（1–209 行）零引用
- [x] ⚠️ **B5.2f 结构半已闭环（`RecordSheet` 753 / `TabIndex` 738，均 <800）**；**运行期（渲染/波形/圆点/轮盘/自定义标签/语音录制+保存全链路）仍待真机**（不得以冷编译冒充运行正确）—— 证据：L-01 见 §8.11（`RecordSheet` 退 baseline）、L-02 见 §8.12/§8.13（`TabIndex` 退 baseline）；`adb devices` 无设备
- [x] **B5.2f-2a `RecordSheet` 圆点+轮盘动画 → `useRecordAnimations`**（第九轮）：新增 `client/composables/useRecordAnimations.uts`（`DotSpec`/`DotPt`/`RecordAnimations`；原模块级初始化移入构造器；`dotCls` 改入参 `recording`）；组件删 2 块 → `ra` + 顶层绑定 `dotPts` + `dotCls` 薄包装（**模板零改动**），轮盘 4 调用点改写 —— 证据：**冷编译成功**；残留旧调用名 **= 0**；`RecordSheet` 1354 → **1135（−219）**；baseline 下调 1354→1135
- [x] **决策已拍板（2026-09-24）**：**L-12 令牌收敛 → 另立「令牌波」**（判为复杂：规模/令牌值漂移/App 端样式不继承需构建期注入/验收须目视真机；台账 §5.10 拍板 10.4）；**64 条功能缺陷 → 另开「功能修复波」**（拍板 10.1）；本波 §7 边界已同步
- [x] 未使用 `--no-verify` 绕过门禁 —— 证据：本批提交经 hook `[pre-commit] 审核通过`

## B11 声明漂移残余（2026-09-24 · 第七轮）

- [x] **D05-9** rerank 模型名三方不一致 → 权威值 `bge-reranker-v2-m3`，改 **5 处陈旧值**（`rerank.py` docstring/:57 fallback、`warm_hf_models.py:25`、`ci.yml:289` 注解、`审计_未关闭缺陷` S3）；`:57` 删 fallback 字面量（单点化到 config，行为等价）—— 证据：`test_rag` **31 passed**；残留 `bge-reranker-base` 仅剩删改说明注释
- [x] **D02-7** `photos/` 键布局两套 → 新增 `services/media_keys.py::photo_object_key` 单点化，`protocol._final_key` 委托 + `photo_content` multipart 路径改走同布局 —— 证据：`derive_thumbnail_key` 为 prefix 级（布局无关）；5 个测试文件 **80 passed**；测试注释同步
- [x] **D04-3** `AGG_CONFIG["night"]`/`["gps_speed"]` 死配置 → `night` **接线**到 `st_dbscan`（值相同 ⇒ 行为等价）+ **删冗余 `gps_speed`** —— 证据：**内存级非空转探针**（改配置 22/30 → 22:45 也归前一天、23:45 变当天 ⇒ 确已接线；还原复原值）；聚合 3 文件 **31 passed**；⚠️ 接线暴露 `agg_types↔st_dbscan` **模块级循环依赖**（既有问题，已登记，本批用函数内局部 import 规避）
- [x] **D12-7** `uvue_gen/audit_report.md` 死证据（引用不存在的对拍器）→ **文首加失效声明**（对拍器不存在/对象已退役/不作验收依据/现行走真机）—— 证据：全仓+全历史 0 命中该对拍器；原文保留供对照
- [ ] ⏸ **D02-9 登记未改**（照片扩展名三表不一致）：收敛必改下发 MIME 或受理格式 ⇒ **行为变更**，越出本波边界；已在 `media_url`/`upload_meta`/`file_magic` 三处加交叉说明（漂移已书面化），修复移交**功能波**（§5.10 拍板 10.1）
- [x] **D09-10** `wechat_messages.status` 三方枚举/默认值不一致 → ORM 定义 **`WECHAT_MESSAGE_STATUSES`**（取值唯一来源）+ schema.sql **注释**对齐（**未改 DDL 默认值**——属漂移迁移，按 §5.10 拍板 10.3③ 待专用窗口）+ **新增防漂移门禁测试** —— 证据：`test_wechat.py` **23 passed**；**反向探针**（临插 `record.status = "zzz_probe_d0910"`）→ 该测试 **FAIL** 且精确列出未声明取值（探针还暴露首版门禁正则 `[a-z_]+` **漏数字**的盲点，已修为 `[a-z0-9_]+`）；探针**字节级还原**（`git status` 干净）
- [ ] ⚠️ **登记（新发现）**：`wechat_messages.status` 的 **DDL 默认 `'processing'` ≠ ORM 默认 `"processed"`**——收敛需改 DDL ⇒ 属漂移迁移，待专用窗口（§5.10 拍板 10.3③）
- [ ] ⏸ **D07-14 登记未改**（三套 dimensions 读路径）：实读澄清**云侧并无重复**（`display_dimensions` 已单点化；`export._profile_out` 是原始导出、非展示口径）；真正缺口是**跨语言平行实现**（客户端 `flattenDimensions` 用**英文 dim id** 作标签）⇒ 目标"不再泄漏英文 dim id"需先建 dim id→中文标签映射 = **UI 文案变更**，越出本波边界。已在两处 docstring **交叉说明**、仅注释未改逻辑 —— **B11 结构类项至此全部处置完毕**（4 修 + 1 加门禁 + 2 登记待功能波）

## B10-i 门禁自身可靠性（2026-09-24 · 第六轮）

- [x] **D14-23** CI `schema-drift-weekly` **永绿**（`continue-on-error: true` + 仅 schedule）→ **去掉 `continue-on-error`**（本 job 仍仅 schedule/dispatch 触发、不进 push/PR，但**定时检出漂移即红=告警信号**，口径对齐同文件 `pip-audit-weekly`）—— 证据：`yaml.safe_load` 解析通过、目标 step 已无 `continue-on-error`、`if` 仍限定 `schedule/workflow_dispatch`
- [x] **D14-22** 机读报告 schema 不统一 + 写盘无兜底 → 新增 `scripts/gate_report.py`（`SCHEMA_VERSION`/`COMMON_KEYS`/带兜底 `write_report`），三工具接入、**自有键全保留** —— 证据：`COMMON ok=True`；两工具实跑报告均含 `schema_version/generated_at/passed`；不可写路径 → `[WARN]` 且 `EXIT=0`（**原实现会崩**）
- [x] **D14-12** subprocess 双实现（超时 300/600、env、OOM 分支各异）→ 新增 `scripts/gate_proc.py` 单一实现；`review_agent` 删本地 `run`、`test_agent` 改 2 行委托 —— 证据：行为探针（缺命令→127 / 超时→124）+ 源码断言（`review_agent` 无本地 `def run`、`test_agent` 无 `subprocess.run(`）+ 两工具实跑 ✅
- [x] **D14-6** 轴 1"字段级漂移未覆盖" **判定已被 B10-g 覆盖**（**无需新增代码**）—— 证据：反向探针（临时给 `SyncPushResult` 加字段）→ 轴 1 仍报"路径与方法集完全对齐 ✓"、而 `openapi_snapshot` **`[FAIL]` 且明示"差异在字段级"**；探针已完全还原（`gen_openapi --check` 复绿）
- [x] 未使用 `--no-verify` 绕过门禁 —— 证据：本批提交经 hook `[pre-commit] 审核通过`