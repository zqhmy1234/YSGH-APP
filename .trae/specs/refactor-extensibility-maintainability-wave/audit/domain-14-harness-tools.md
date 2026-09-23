# 域14 · harness 工具（门禁链）深度审计

## 覆盖范围与实读清单

实际读过（路径 + 行数）：

| 文件 | 行数 | 读法 |
|---|---|---|
| `scripts/audit_harness.py` | 564 | 全文精读（含轴 1–6 + 入口 + docstring） |
| `scripts/audit_harness_baseline.json` | 83 | 全文精读 |
| `scripts/review_agent.py` | 417 | 全文精读 |
| `scripts/test_agent.py` | 336 | 全文精读 |
| `scripts/lessons.py` | 200 | 全文精读 |
| `scripts/check_schema_drift.py` | 372 | 全文精读 |
| `scripts/audit_security.py` | 141 | 全文精读（重复逻辑对照） |
| `.githooks/pre-commit` | 17 | 全文精读 |
| `.github/workflows/ci.yml` | 547 | 全文精读（4 job 全） |
| `backend/app/db/models/memory_agent.py` | 1–45 | 定点读（镜像声明依据） |
| `docs/决策台账.md` | 95–134 | 定点读（§4.8–4.13 工具落成记录） |
| `.trae/.../refactor-ledger.md` | 106 | 全文精读（§8 B1 执行结果） |

**未读到 / 不存在**：

- `scripts/structured_report*.py` —— **全仓（含 `scripts/`、`backend/`、git 全历史）0 命中，文件不存在**（见 D14-20）。域⑭ 任务书把它列为范围项，实为幻影。
- `backend/tests/test_agent_schema_alignment.py` —— ledger/决策台账 L-11 引作"漂移守卫"，**仓库与 git 全历史均无此文件**（见 D14-21）。
- `scripts/regen_openapi*`（契约再生脚本）—— 不存在（见 D14-7）。

`git status --short`（本域审计时点）：

```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```

分支 `develop`，在途仅本波 spec 目录（未跟踪）与 `.codebuddy/`。**本域审计对象（scripts/*、.githooks/*、.github/*）全部干净、无在途改动**，与 AGENTS「第四窗媒体票据在途」不重叠。

## 门禁链全景（先立事实，再列条目）

```
提交时（本地）  .githooks/pre-commit ─→ review_agent.py（fast）
                                      ├─ syntax   (staged .py)
                                      ├─ lint     (ruff, staged .py, 排除 dirty)
                                      ├─ secrets  (staged, TEXT_EXTS)
                                      ├─ todos    (报告不阻断)
                                      ├─ structure ─import→ audit_harness.structure_findings()
                                      │                       = _filesize_findings()[0] + _dup_findings()[0]   ← 仅轴5+轴6
                                      └─ lessons  (仅在"其余全绿"时追加判)
CI fast-gate    review_agent.py --full --skip-tests  (+ client tsc 试点，非阻断)
CI full-gate    review_agent.py --full --skip-tests → test_agent.py --cov-threshold 60 → pytest -m rag
CI weekly       check_schema_drift.py（continue-on-error）｜ pip-audit-weekly
手动/台账        audit_harness.py openapi|client|models|exports|filesize|dups|all    ← 轴1–4 仅此一条路径
                audit_security.py（四域数据安全，未接任何门禁）
```

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D14-1 | `scripts/review_agent.py:328-330`；`.github/workflows/ci.yml:48,370` | 门禁缺口 | P0 | 轴1–4（契约/客户端/模型/导出）真正进闸，漂移回潮即红 | 低（纯接线） | 新增工具 | `grep audit_harness` 于 ci.yml/review_agent 确认仅 structure 被引用 | 独立小批 | 确认 |
| D14-2 | `scripts/audit_harness.py:406` vs `audit_harness_baseline.json:10-13` | 声明漂移 | P1 | 基线阈值不再是死配置，改阈值即刻生效 | 极低 | 行为等价替换 | 改 baseline thresholds 后跑 `audit_harness filesize` 看是否变化 | B1 补 | 确认 |
| D14-3 | `scripts/audit_harness.py:489-496` | 门禁假阴性 | P1 | 堵住"此处减 2、彼处增 2"绕过 | 低 | 行为等价替换 | 构造 per_file 再分配（总数不变）→ 当前不 CRITICAL | B1 补 | 确认 |
| D14-4 | `scripts/audit_harness.py:429-447` | 门禁假阳/假阴 | P1 | 白名单失效/文件改名不再误红也不留僵尸豁免 | 低 | 行为等价替换 | 把白名单文件重命名 → 当前转 CRITICAL | B1 补 | 确认 |
| D14-5 | `scripts/audit_harness.py:288-289,305-306` | 门禁假阴性 | P1 | 镜像豁免逐表可控，防整文件误豁免 | 低 | 行为等价替换 | 在任一 models 文件加注释"镜像"→ 该类被豁免 | B1 补 | 确认 |
| D14-6 | `scripts/audit_harness.py:111-113,123-125` | 门禁缺口 | P1 | 覆盖字段级契约漂移 | 中 | 新增工具 | 改一个 response model 字段 → 当前 axis1 全绿 | 独立小批 | 确认 |
| D14-7 | `scripts/audit_harness.py:22-23` | 门禁缺口 | P1 | "报漂移"同时给"一键再生"，门禁可闭环 | 低 | 新增工具 | `ls scripts/regen_openapi*` 不存在 | 独立小批 | 确认 |
| D14-8 | `scripts/review_agent.py:254` | 口径不一致 | P1 | 全量门禁覆盖率阈值唯一（60） | 低 | 行为等价替换 | review 硬编码 50 vs ci.yml:377 传 60 | B1 补 | 确认 |
| D14-9 | `scripts/review_agent.py:53-69` vs `scripts/audit_security.py:31-35` | 真实重复逻辑 | P1 | 密钥模式单一来源，消除覆盖分歧 | 低 | 行为等价替换 | 两处清单模式集不同（asr 侧仅有 sk-/AKID/PRIVATE） | B1 补 | 确认 |
| D14-10 | `scripts/review_agent.py:356-362,394-397` | 死码/有效性缺口 | P1 | 教训强制登记提示真正可见 | 极低 | 行为等价替换 | `checks` 无 "lessons" 键，print 分支不可达 | B1 补 | 确认 |
| D14-11 | `audit_harness.py:58-62`／`review_agent.py:40-43`／`test_agent.py:26-29`／`lessons.py:29-30`／`check_schema_drift.py:47-50`／`audit_security.py:27-28` | 真实重复逻辑 | P2 | 6 份同语义 UTF-8 兜底收敛 | 极低 | 行为等价替换 | 六处逐字同构（reconfigure utf-8/replace） | B1 补 | 确认 |
| D14-12 | `scripts/review_agent.py:81-91` vs `scripts/test_agent.py:42-59` | 真实重复逻辑 | P2 | subprocess 包装单一实现 | 低 | 行为等价替换 | 两函数同语义（超时/env/编码各异） | B1 补 | 确认 |
| D14-13 | `scripts/review_agent.py:94-110` vs `audit_harness.py:172-180,409-418` | 真实重复逻辑 | P2 | 排除清单单一来源 | 低 | 行为等价替换 | 三处各自硬编码 `unpackage/node_modules/.hbuilderx/.wt` | B1 补 | 确认 |
| D14-14 | `scripts/audit_harness.py:221,232-237` | 门禁假阳性 | P2 | 深路由/块注释不再误判 | 低 | 行为等价替换 | 正则仅匹配 2 段；`in_comment` 仅查行内前缀 | B1 补 | 确认 |
| D14-15 | `scripts/audit_harness.py:253-261` | 门禁假阳性 | P2 | 注释内 `.ts` 提及不再误红 | 低 | 行为等价替换 | `.ts` 正则无注释过滤 | B1 补 | 确认 |
| D14-16 | `scripts/audit_harness.py:333-336,354` | 覆盖盲区 | P2 | 导出面覆盖 type/interface/enum/默认导出/子目录 | 低 | 新增工具 | `glob("*.uts")` 不递归；`EXPORT_RE` 仅 4 词 | B1 补 | 候审 |
| D14-17 | `scripts/audit_harness.py:294-296` | 门禁假阳性 | P2 | 仅测试使用的模型/导出不再误红 | 低 | 行为等价替换 | 扫描面 `backend/app`，tests 在 `backend/tests` | B1 补 | 候审 |
| D14-18 | `review_agent.py:411-413`／`test_agent.py:209-211`／`audit_harness.py:35-39`／`check_schema_drift.py:316,368` | 退出码口径不一致 | P1 | "环境错误"语义统一，CI 可区分"红"与"无法判定" | 中 | 行为等价替换 | 四工具 0/1/2 各异；review 吞掉被调工具 exit 2 | B1 补 | 确认 |
| D14-19 | `scripts/review_agent.py:246-247,259-260` | 门禁静默降级 | P2 | 环境缺失降级留痕为告警而非"通过" | 低 | 行为等价替换 | ruff/pytest 缺失 → `return True` 通过 | B1 补 | 确认 |
| D14-20 | `scripts/structured_report*.py`（不存在） | 声明漂移 | P2 | 澄清域⑭范围幻影，防误引 | 极低 | — | 全仓 0 命中（Get-ChildItem 全类 + git 历史） | 登记 | 确认 |
| D14-21 | `refactor-ledger.md:45`／`docs/决策台账.md:111` 引 `backend/tests/test_agent_schema_alignment.py` | 声明漂移/自校验缺失 | P1 | 镜像豁免的依据可核（补测试或删声明） | 低 | 新增工具 | 该文件 git 全历史不存在；`memory_agent.py:12` 亦引用它 | B1 补 | 确认 |
| D14-22 | `scripts/audit_harness.py:553-558` | 报告口径未统一 | P2 | 门禁机读产物统一 schema | 低 | 行为等价替换 | 与 review-report.json 结构不同；write_text 无异常兜底 | B1 补 | 候选 |
| D14-23 | `.github/workflows/ci.yml:444-481` | 门禁缺口 | P2 | 漂移真正可阻断（至少 develop 定时红） | 中 | 行为等价替换 | `continue-on-error: true` + 仅 schedule | 登记 | 确认 |
| D14-24 | `scripts/audit_harness.py:406,435` | 覆盖盲区 | P2 | 阈值按类型区分，降噪 | 低 | 行为等价替换 | client 一律 800（含 template/style）；行数含注释空行 | B1 补 | 候审 |

### 逐条证据

**D14-1**：读过 `review_agent.py` 全文，其唯一引用 audit_harness 处为 `check_structure`（L327-333）`from audit_harness import structure_findings`，而 `structure_findings`（audit_harness.py:513-518）仅返回 `_filesize_findings()[0] + _dup_findings()[0]`＝**轴5+轴6**。`ci.yml` 全文 547 行中 `audit_harness` **0 命中**（grep `.github` 仅命中 `check_schema_drift.py:481`）。故轴1（契约双向）/轴2（客户端漂移）/轴3（模型使用率）/轴4（导出可达）**只在人工跑 `audit_harness` 时存在**——恰是 09-23 事件记录里"已积压 17 条路径整月无人发现"的同类风险面。

**D14-2**：`audit_harness_baseline.json:10-13` 声明 `"thresholds": {"backend_py":600,"client":800}`，但 `_threshold_for`（L421-424）只读模块常量 `FILESIZE_THRESHOLDS = {...}`（L406），**从不读 baseline 的 thresholds**。改基线阈值文件 → 审计结果不变（死配置）。

**D14-3**：`_dup_findings`（L477-499）对每个 pattern 只做 `if total > int(base_total): crit`（L493）。`growth`（L489-490）虽逐文件算出，但**仅拼进 CRITICAL 文案**；若 A 文件 8→6、B 文件 0→2，total 仍 44 → 走 `else: info`（L497-498），**新增的手抄点被静默放过**。

**D14-4**：`_filesize_findings`（L433-448）对 allowlist 内文件若 `lines <= limit` 直接 `continue`（L437）——**条目已无效（文件瘦身达标/改名/删除）永不清算，也无告警**；反之文件被重命名则 allowlist 失配 → 走 `else: crit.append(...新增超阈...)`（L446-447），**存量债被误报为新增 CRITICAL**。

**D14-5**：`audit_models` 只要文件文本含子串"镜像"（L288-289）即把该文件加入 `mirror_files`，随后 `zero` 判定用 `f not in mirror_files`（L305）→ **整文件所有类被一次性豁免**；无逐表白名单、无数量上限。若他人在 models 文件里加一句含"镜像"的注释，其真实 0 引用模型即被掩盖（假阴性）。

**D14-6**：`_local_openapi_paths`/`_committed_openapi_paths`（L96-126）仅抽取 `{path: {methods}}`，`audit_openapi`（L129-164）只比路径集与方法集。**同路径的 requestBody / parameters / responses schema / components 完全不比**——这正是"改了字段但契约未同步"的常见形态，属静默盲区。

**D14-7**：`audit_harness` docstring（L22-23）自述"openapi 再生是人工步骤（既无脚本、也无 CI 断言）"；`scripts/` 目录 27 个非 py 文件清单中**无 regen 类脚本**。门禁只报不修，闭环靠人手。

**D14-8**：`review_agent.run_tests`（L251-261）写死 `"--cov-threshold", "50"`（L254）；而 `test_agent.py` 默认 60（L278）、CI 显式传 60（ci.yml:377）、docstring（review_agent L22）又称"经 scripts/test_agent.py"。同一"全量门禁"存在 50/60 两个阈值。

**D14-9**：`review_agent.SECRET_PATTERNS`（L53-69）14 条（含 ghp_/glpat-/sk_live_/AIza/通用赋值式）；`audit_security._SECRET_PATTERNS`（L31-35）仅 3 条正则（sk-/AKID/PRIVATE）。**同语义"源码无硬编码密钥"双实现且覆盖集分歧**；且 `audit_security` 未出现在 `ci.yml`/`pre-commit`（grep 命中仅 ci.yml:481 为 check_schema_drift），四域数据安全审计**不在任何门禁**。

**D14-10**：`main` 中 `checks` 字典（L356-362）键为 syntax/lint/secrets/todos/structure（+full 时 tests），**无 "lessons"**；但打印循环（L392-402）含 `if name == "lessons"` 专门分支（L394-397）——**不可达死码**。lessons 失败仅以键名出现在 `blocking`（L377），末尾行（L412）只打印键名，**用户看不到"需 `lessons.py add`"的命令提示**，削弱"程序化强制"的引导作用。

**D14-11/12/13**：逐字/逐句对照：
- UTF-8 兜底：`audit_harness.py:58-62`、`review_agent.py:40-43`、`test_agent.py:26-29`、`lessons.py:29-30`、`check_schema_drift.py:47-50`、`audit_security.py:27-28` 六处同构。
- `run()`：`review_agent.py:81-91` 与 `test_agent.py:42-59` 同语义（capture/timeout/encoding/FileNotFoundError/TimeoutExpired），差异仅在超时值与 env 来源。
- 排除清单：`review_agent._skip_path:105-110`、`audit_harness._client_sources:177`、`audit_harness._iter_sized_sources:416` 三处各自硬编码同集合。

**D14-14/15**：`page_re = r"/pages/[A-Za-z0-9_]+/[A-Za-z0-9_-]+"`（L221）**只吃两段**，遇 `/pages/a/b/c` 会截成 `/pages/a/b`（若 pages.json 注册的是三段，则误判"未注册"→ 死路由 CRITICAL）；`in_comment`（L235）只取**同一行**前缀判 `//`/`<!--`，**跨行块注释内的旧页引用不会被分流**（直接进 `dead_code` → CRITICAL）。`.ts` 正则（L253-255，L256-261 遍历）**无注释过滤**，任何注释/字符串提到 `.ts` 即 CRITICAL。

**D14-16/17**：`audit_exports` 用 `utils_dir.glob("*.uts")`（L354）**不递归子目录**；`EXPORT_RE`（L333-336）仅 `function|const|let|class`，漏 `export default`、`export {a,b}`、`type/interface/enum`。轴3 扫描面 `(BACKEND/"app").rglob`（L294-296）**不含 `backend/tests`**，轴4 扫描面 `_client_sources()` 也不含测试——"仅测试使用的模型/导出"会被判 CRITICAL（假阳性面）。

**D14-18**：退出码对照——`audit_harness` 0/1/2（L35-39，2=环境）；`check_schema_drift` 0/1/2（L316/368，2=环境）；`review_agent` **仅 0/1**（L411/413，被调 `check_structure` 走 `structure_findings` 纯计算不抛 2，故 audit_harness 的 exit 2 **无法上抛**）；`test_agent` **仅 0/1**，且环境缺失分支 `return True, "[skip]..."`（L209-211，pytest 缺依赖算通过）；`audit_security` 0/1。**"环境错误"在四个工具里三种语义（2 / 吞成 0 / 吞成通过）**，CI 无法区分"红"与"无法判定"。

**D14-19**：`check_lint`（L245-247）`if code == 127: return True, "[skip] ruff 未安装"`；`run_tests`（L259-260）pytest 缺失亦 `return True`。门禁可因工具缺失**静默由红变绿**，且无 annotation 留痕（对比 `test_agent._emit_warnings` 有 `::warning::` 机制）。

**D14-20**：`Get-ChildItem -Recurse -File -Include *.py,*.yml,*.yaml,*.md | Select-String "structured_report"` → **0 命中**；`scripts/` 文件清单（27 项）无该文件。域⑭ 任务书范围项之一为幻影。

**D14-21**：`refactor-ledger.md:45`（L-11）称镜像表"有 `test_agent_schema_alignment.py` 守卫"，`docs/决策台账.md:111`（§4.13）复述，`memory_agent.py:12` docstring 亦写"由 backend 测试 `test_agent_schema_alignment.py` 做漂移守卫"。实测：全仓文件名匹配 `*alignment*` **0 命中**，`git log --all -- "*test_agent_schema_alignment*"` **空**。→ 该守卫**从未存在**，B1 轴3 假阳性修复所持的"另有守卫"依据为幻影；镜像豁免目前**无任何自校验**。

**D14-22**：`audit_harness` 落盘 `{"critical":[],"info":[]}`（L553-558），`review_agent` 落盘 `{"mode","passed","blocking_checks","details"}`（L379-387）——**两套机读 schema**，CI 读后者的松散字段（ci.yml:414-425）。且 `Path(...).write_text` 无 `try`，磁盘/权限异常会中断审计。

**D14-23**：`ci.yml:444-481` schema-drift job：`if: schedule||workflow_dispatch`（L450）+ `continue-on-error: true`（L480）→ **PR/push 永不运行、失败永不阻挡**。与 §5.9 待拍板（schema.sql vs ORM 谁是权威）耦合。

**D14-24**：`FILESIZE_THRESHOLDS`（L406）client 一刀切 800，不区分 `.uvue`（含 template/style）与 `.uts`；行数由 `splitlines()`（L435）计，**含注释与空行**——注释多的文件更易触阈。

## 依赖方向与抽象覆盖度结论

- **FF1（反向依赖）**：本域**无**反向依赖。依赖方向单向且合理：`pre-commit → review_agent → {audit_harness(仅纯计算入口), lessons, test_agent, ruff, pytest}`；`ci.yml → review_agent / test_agent / check_schema_drift`。`review_agent:327-328` / `336-340` 用 `sys.path.insert(0, scripts)` 做**同目录兄弟导入**（合法，但依赖 cwd 无关的绝对路径，见下）。唯一结构性隐患是 review_agent 反向依赖 audit_harness 的**私有内部函数语义**（`structure_findings` 是其公开出口，属设计良好），但 audit_harness 的 `CRITICAL/INFO` 模块级全局（L68-69）与 `structure_findings` 并存——若未来有人误让 `structure_findings` 调 `audit_filesize()`（会写全局），进程内调用将污染状态（当前未发生，登记为注意项）。
- **新增一个轴的接入点清单（文件:行）**：
  1. `scripts/audit_harness.py:510` 之后 —— 新增 `audit_<axis>()` 函数（仿轴5/6 形态：`_<axis>_findings()` 纯计算 + `audit_<axis>()` 打印）。
  2. `scripts/audit_harness.py:527` —— `choices=[...]` 增加轴名（否则 `argparse` 直接拒绝）。
  3. `scripts/audit_harness.py:533-544` —— 分派块增加 `if args.axis in {"<axis>", "all"}: audit_<axis>()`。
  4. `scripts/audit_harness.py:518` —— 若该轴须进提交门禁，追加到 `structure_findings()` 的返回值。
  5. `scripts/audit_harness.py:2-42` —— 模块 docstring 的轴清单（L2/L16-19/L31-32）与退出码说明（L35-39）。
  6. `scripts/audit_harness_baseline.json:9/52` —— 新增对应 baseline 段（棘轮轴须含 `reason`/`target`，见 `_doc` L3-7）。
  7. `.github/workflows/ci.yml`（fast-gate 或独立 job）—— 若须 CI 阻断；否则只落人工。
  8. `scripts/review_agent.py:356-362`（`checks` 字典）—— **仅当**该轴不进 `structure_findings` 而自成 `check_<x>` 时需在此加一行。
  （**注意接入债务**：步骤 4 是"进提交门禁"的**唯一**开关，但步骤 8 与 4 是两套并行范式——当前 structure 走 4，其余检查走 8，**新增轴时必须二者择一，无统一注册表**。）

## 各轴自校验手段核查（任务要求逐轴回答）

| 轴 | 自校验手段 | 结论 |
|---|---|---|
| 轴1 契约 | 无。无"已知差异样本"回归；`_local_openapi_paths` 导入失败的**唯一**退路是 `--skip-openapi`（人工） | **缺失**（仅靠人工跑） |
| 轴2 客户端 | 无。历史两次假阳性（44 条假死路由 / 9→1 假 mock）靠**人工发现+改正则**，无回归用例 | **缺失** |
| 轴3 模型 | 无。L-11 声称的 `test_agent_schema_alignment.py` 守卫**不存在**（D14-21） | **缺失** |
| 轴4 导出 | 无。白名单 `EXPORT_ALLOWLIST`（L339-342）硬编码、无 reason 字段、无"新增白名单需自证非零调用"约束 | **缺失** |
| 轴5 体积 | 部分——靠 baseline 棘轮充当回归闸；**无 stale 检测**（D14-4）；B1 负向探针为**人工一次**（ledger §8 记录"清空基线 → 体积 7 条 / dup 1 条"） | **弱**（人工一次性） |
| 轴6 重复 | 同上；且只 gate 总数不 gate 分布（D14-3） | **弱** |
| 门禁本体 | `backend/tests` 78 个测试文件中**无任何**引用 `audit_harness`/`review_agent`/`lessons`/`check_schema_drift`（Select-String 0 命中）→ 工具本身**无单元/集成测试** | **缺失** |

## 已知假阳/静默丢弃场景清单（任务要求逐轴列）

**假阳性（会污染台账，"比缺陷更危险"）**

1. 轴2：跨行块注释内旧页引用 → 误判死路由 CRITICAL（L232-240）。
2. 轴2：3 段以上合法路由被截成 2 段 → 误判未注册（L221）。
3. 轴2：注释/字符串提及 `.ts` → 误判残留 CRITICAL（L253-261）。
4. 轴3：0 引用但仅被 `backend/tests` 使用的模型 → 误判 CRITICAL（L294-296 扫描面）。
5. 轴3：字符串式/反射式引用（如动态 `relationship`、mapper 名）不计入（L302 仅词边界正则）。
6. 轴4：仅被测试/脚本使用的导出 → 误判 zero_cap CRITICAL（L363-384）。
7. 轴5：白名单文件重命名 → 存量债误报"新增超阈"CRITICAL（D14-4）。
8. 轴5：注释密集文件更易触阈（L435 计全行）。
9. 轴6：`deleted_at.is_(None)` 命中**语义不同**的查询（如统计/审计用途）也计入总数（L469 单模式，无上下文判别）。

**假阴性 / 静默丢弃**

1. 轴1：字段级 schema 漂移（request/response/components/parameters）完全不覆盖（D14-6）。
2. 轴1：`docs/openapi.json` 与后端**同时删同一路径** → 两侧一致，无漂移检出（双向对拍的自然盲区）。
3. 轴3：整文件含"镜像"即豁免全部类（D14-5）。
4. 轴3：类被 `models/__init__.py` 或 `models/` 内其他文件引用——扫描面排除 models 目录与 `__init__.py`（L296）→ 经 `models/__init__` 再导出的使用不计。
5. 轴4：`export default`/`export {}`/`type`/`interface`/`enum`/子目录导出**完全未纳入**（D14-16）。
6. 轴5：存量文件在基线值下**继续增长不会阻断**（仅 WARN，L442-443）→ 债务可无限膨胀。
7. 轴6：分布挪移绕过（D14-3）——**最实际的漏网形态**。
8. 门禁：轴1–4 根本不在任何自动门禁（D14-1）＝整体静默。
9. 门禁：`check_lint`/`run_tests` 工具缺失静默转绿（D14-19）。
10. 门禁：`check_schema_drift` 仅 weekly + continue-on-error（D14-23）。
11. `lessons` 检查（lessons.py:141-174）依赖 `.cowork-temp/last-failure.json`——若该文件被清（如换机/清 `.cowork-temp`），强制登记链**静默解除**（`STATE_PATH.exists()` 为假即 `return True`，L149-150）；且断言基于"最新一条登记时间 > 失败时间"，与失败**具体项**无绑定（登记任意一条无关教训即可解锁）。

## audit_harness ↔ review_agent 调用关系与退出码口径

- **调用关系**：单向、单函数。`review_agent.check_structure`（L320-333）`sys.path.insert(ROOT/"scripts")` 后 `from audit_harness import structure_findings`，取 `_filesize_findings()[0] + _dup_findings()[0]`（audit_harness L513-518）。audit_harness 的其余 4 轴**无调用方**。`structure_findings` 设计为"纯计算、不打印、不导入后端 app"（其 docstring L514-517）——**这是正确做法**（避免 fast-gate 秒级门禁触发 `import app.main` 副作用）。
- **退出码口径一致性**：**不一致**（详见 D14-18）：
  - audit_harness 自定 0/1/**2**（2=执行环境错误，L35-39），且在 `audit_openapi` 内 **`raise SystemExit(2)`**（L133-134）。
  - review_agent **只有 0/1**，"禁止提交"语义（L25）；它**无法表达**"工具/环境故障"——既不能上抛被调工具的 2，自身出错（如 py_compile 抛非 `PyCompileError` 异常、`REPORT_PATH.write_text` 失败）会以**未捕获 traceback** 收场（非约定码）。
  - test_agent 只有 0/1，环境缺失降级为 `True,"[skip]"`（L209-211）——**"环境错误"被折叠成"通过"**。
  - check_schema_drift 0/1/2（L29-33），与 audit_harness 同构但**独立定义、无共享常量**。
  - `pre-commit`（L9）`if ! python scripts/review_agent.py` → **任何非 0 皆视为"审核不通过"**，故即便 review_agent 未来上抛 2，hook 也无法区分。
  - 结论：**"红"与"无法判定"在链路上被混同**，同一条 CI 语义（`exit != 0`）承载三种含义。

## 新增一类实体的注册点（本域视角）

- **新增一个审计轴**：8 处（见上"接入点清单"），核心 3 处强制（函数/argparse/dispatch），进闸 1 处（`structure_findings`），其余为文档/基线/CI。
- **新增一条门禁检查（review_agent）**：2 处——`check_<x>()` 函数 + `checks` 字典一行（L356-362）；`blocking`/报告/退出码由现有循环自动覆盖。
- **新增一条棘轮基线**：1 处（`audit_harness_baseline.json`）+ 保证脚本读取它（**当前 axis5 阈值不从基线读，属反例** D14-2）。
- **新增一种被扫文件类型**（如 `.mjs`/`.ps1` 纳入 secrets）：`review_agent.TEXT_EXTS`（L73-78）一处；但**同一"文本文件集"概念在 `audit_harness.CODE_SUFFIXES`（L169）与 `_iter_sized_sources`（L411-417）各有一份**，需三处同改 → 抽象未收敛。

## 本域已查无问题项（避免遗漏被误读）

1. **依赖方向**：无 services→api 之类反向依赖；review_agent 对 audit_harness 的依赖指向公开出口，方向正确。
2. **`structure_findings` 纯度**：只调 `_filesize_findings`/`_dup_findings` 两个不写全局的纯计算函数，**未污染** `CRITICAL/INFO` 全局，fast-gate 秒级假设成立。
3. **fast-gate 对 `agent/` 的处理**：语法+密钥扫描**覆盖** `agent/`、仅 lint 豁免（review_agent L121-127 与 ruff.toml 说明一致），且豁免带 `AG7` 撤销待办——设计上有意为之，非缺口。
4. **`_repo_text_candidates`（L198-217）**：已从"工作区 rglob"修为"tracked ∪ untracked-非忽略"，正确消除被忽略产物导致的假红灯（2026-09-23 修），**本项无问题**。
5. **`lessons.py` 不销毁语义**（L79-92）：表头失配改安全插入，08-29 清空事故的修复**已在位**；`CN_TZ` 固定 +8 时区（L38）正确。
6. **`test_agent` 内存/端口探测与 OOM 提示**（L49-54,62-97,282-289）：环境诊断友好，非静默；`_emit_warnings`（L126-135）确实把 deselect 显式化为 `::warning::`，**无静默漏跑**。
7. **轴2 前导斜杠归一化**（L183-189 注释 + L189 实现）与轴2 mock 开关"仅认声明形态"（L198-202）：两处历史假阳性**已修**，本项无问题。
8. **`check_schema_drift` 归一化面**（类型别名/列序/命名差异，L67-103）：覆盖合理，默认值差异降为 INFO（L263-264）符合声明。
9. **`audit_security` 自身逻辑**（除与 review_agent 重复外）：四域检查项与 fail 标记自洽，退出码 0/1 与其"金丝雀"定位一致。
10. **`pre-commit` hook**：逻辑极简、失败即 `exit 1`，无绕过缝隙（除全局 `--no-verify`，属 git 机制，AGENTS 已禁令）。