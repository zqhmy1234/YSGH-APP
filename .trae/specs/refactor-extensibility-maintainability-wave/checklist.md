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