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