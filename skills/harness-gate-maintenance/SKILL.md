---
name: harness-gate-maintenance
description: >-
  忆述光华 harness 与门禁维护手册：提交门禁 `review_agent.py`（快/全量分层、各检查轴）、
  客户端编译门 `client_compile_gate.py`、出包前置门 `preflight_pack.ps1`、
  `audit_harness` 基线「只允许收缩」、**新增门禁必须先做负向探针自校验**、
  环境错误用 `[环境错误]` 前缀（退出码 2）、运维类问题降级 WARN、
  STATE.md 与 state 漂移治理、lessons 强制登记闭环。
  **改门禁 / 加检查轴 / 动 harness 文件（AGENTS·STATE·progress·feature_list·handoff·init）前先读。**
official: false
---

# harness 与门禁维护（忆述光华）

## 何时读

- 要**新增/修改/删除**一个门禁检查（`scripts/review_agent.py` 的 `checks` 字典）
- 门禁**报红**要定位（先看结构化清单，别重跑全量）
- 要动 **harness 文件**（`AGENTS.md` / `STATE.md` / `progress.md` / `feature_list.json` / `session-handoff.md` / `init.*`）
- 要改**出包前置门** `deploy/scripts/preflight_pack.ps1` 的判据

## 门禁全景（2026-10-11）

| 检查 | 何时跑 | 判据来源 | 失败后果 | 备注 |
|---|---|---|---|---|
| `syntax` | 快/全量 | `py_compile` | 阻断 | 快模式只查本次提交 |
| `lint` | 快/全量 | `ruff` | 阻断 | `agent/` 临时豁免（AG7 撤销后收回） |
| `secrets` | 快/全量 | `secret_patterns.py` | 阻断 | **2026-10-11 起含 `client/`**（原被 `_skip_path` 漏扫） |
| `todos` | 快/全量 | 正则 | 报告 | 不阻断 |
| `structure` | 快/全量 | `audit_harness.py` 轴5/6 | 新增超阈阻断 | 体积/重复棘轮，基线冻结 |
| `audit_axes` | 快/全量 | `audit_harness.axes_findings` | CRITICAL 阻断 | 契约/客户端/模型/导出四轴 |
| `env_template` | 快/全量 | `deploy/scripts/check_env_template.py` | 缺口阻断 · 缺依赖降级 | |
| `openapi_snapshot` | 快/全量 | `gen_openapi.py --check` | 漂移阻断 · 缺依赖降级 | 契约字节级 |
| `port_consistency` | 快/全量 | 本仓文本 | 不一致阻断 | **2026-10-11 新增**（8010/8300 口径） |
| `harness_state` | 快/全量 | `STATE.md` + `feature_list.json` | **从不阻断（WARN）** | **2026-10-11 新增**；state 漂移可见化 |
| `client_compile` | **仅 `--full`** | `client_compile_gate.py` | 编译失败阻断 · 缺 HBuilderX 降级放行 | 20~56s |
| `audit_security` | **仅 `--full`** | `audit_security.py` | 密钥/传输/存储域阻断 · **backup 域只告警** | 原为孤儿脚本 |
| `tests` | `--full`（除非 `--skip-tests`） | `test_agent.py` | 失败阻断 · 覆盖率阈值 60 | |
| `lessons` | 通过后 | `lessons.py` | 上次失败未登记 ⇒ 阻断 | 程序化强制 |

**退出码**（单一来源 `scripts/gate_exit.py`）：`0` 通过 / `1` 违规阻断 / `2` **环境错误**（工具不可用，未真正判定）。

## 铁律（每条都有代价）

1. **新增门禁必须先做「负向探针自校验」**：清空基线 / 注入样例，确认**能命中**。
   假阳性比缺陷更危险（先例：`audit_harness` 曾误报 44 条假死路由；镜像表假 CRITICAL 污染台账）。
2. **基线只允许收缩**：`scripts/audit_harness_baseline.json` 每条带 `reason`/`target`；
   基线里引用的**证据文件必须入库可复现**（` .cowork-temp/` 是 gitignore，不能当证据）。
3. **环境不可用 = `[环境错误]` 前缀**（退出码 2），**永远不要静默转绿**——
   静默绿会让门禁失去公信力（先例：`openapi_snapshot` 在缺后端依赖时曾恒红，真因只是缺 pydantic）。
4. **运维类问题降级 WARN，不阻断提交**：如备份 dump 新鲜度（RPO≤24h）取决于"有没有定期跑脚本"，
   与本次提交的代码无关；若阻断 ⇒ **每次提交恒红** ⇒ 开发者只能绕过 ⇒ 门禁失效。
   做法：按域区分（密钥/传输/存储阻断，backup 只告警）。
5. **客户端编译门不进 pre-commit**（单次 20~56s），只在 `--full` 与出包前置门跑；
   缺 HBuilderX 的机器**降级放行并显式标注 `[降级放行]`**（不是静默绿）。
6. **state 漂移用「WARN + 每次可见」治，不用阻断治**：阻断会逼人删检查；
   WARN 让漂移在当场可见（`harness_state` + `init.*` 的 STATE 新鲜度行）。
7. **HBuilderX CLI 的坑**：`cli.exe launch ... --compile true` 在 HBuilderX **已启动**时
   会委派给现有实例、**管道里只有一行版本号**（拿不到编译日志）⇒ 必须用
   **文件重定向 + 产物时间戳**双判据（见 `client_compile_gate.py`）。
8. **PowerShell 脚本含中文必须存为 UTF-8 BOM**（Windows PowerShell 5.1 否则按 ANSI 解码 ⇒ 语法错误）；
   bash 脚本相反（无 BOM）。

## 常用命令

```bash
python scripts/review_agent.py                      # 快速门禁（commit 时自动跑，秒级）
python scripts/review_agent.py --full               # 全量（含测试 + 客户端编译门 + 数据安全审计）
python scripts/review_agent.py --full --skip-tests  # 只跑静态层（CI 快速用）
python scripts/client_compile_gate.py               # 单独跑编译门（改动 client/ 后建议先跑）
python scripts/audit_security.py                    # 数据安全四域（--json 机读）
python scripts/audit_harness.py all                 # 结构/契约/客户端各轴（基线对比）
pwsh -File deploy/scripts/preflight_pack.ps1 -AllowTunnel   # 出包前置门
python scripts/lessons.py add --error ... --root-cause ...  # 踩坑登记（门禁强制）
```

## 门禁报红的定位口径

1. 先看输出里 `[失败用例（先看这里）]` 与结构化报告：
   `python -c "import json;d=json.load(open('.cowork-temp/test-report.json',encoding='utf-8'));print(d['failed_tests'])"`
2. 机读键固定为 `failed_tests` / `blocking_sections` / `details`（**别猜键名**）
3. **禁止**靠"重跑全量套件"定位用例名（≈2 分钟/次）
4. 报告留档：`.cowork-temp/review-report.json` + `.cowork-temp/test-report.json`

## harness 文件的职责边界（避免第二次漂移）

| 文件 | 唯一职责 | 更新时机 |
|---|---|---|
| `STATE.md` | **唯一**「当前状态」：正在做什么 / 下一步 / 阻塞 / 关键数字 | **每会话结束前必改** |
| `AGENTS.md` | 规则、技术栈、DoD、门禁、**指向 STATE.md 的指针** | 规则变化时 |
| `progress.md` | 追加式时间线（**以最后一条为准**；不再放"当前状态"） | 每次会话结束追加 |
| `session-handoff.md` | 交接：§1 起的长期纪律 + §0 上次交接 | 交接时 |
| `feature_list.json` | 能力清单（约定只 1 条 `in-progress`） | 特性状态变化时 |
| `docs/决策台账.md` | 决策 / §5 待拍板 / §7 未关闭缺陷 | 有决策或销项时 |
| `docs/lessons.md` | 踩坑（带 commit 时间戳） | 每次修复后（门禁强制） |

> 外部审计：`node <harness-creator>/scripts/validate-harness.mjs --target .`（2026-10-11 已达 **100/100**）。
> 它的判据是**英文关键词**（`Current State` / `Blockers` / `One feature at a time` / `Stay in scope` /
> `Last Updated` / `Recommended Next Step` …）⇒ 相关文件里保留了**双语机器可读标记块**；
> 改动那些文件时**不要删掉英文键名**，否则分数会掉。
