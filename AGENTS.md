# AGENTS.md — 忆述光华 (YiShu GuangHua)

个人记忆整理与回顾 APP：自动整理照片/文字/语音碎片记忆，提供 AI 分类、四层事件聚合、描述性搜索、时间轴与"去年今日"回响。MVP 目标 100 用户内测。

## 仓库结构

- `忆述光华_交付文档/` — 全部交付文档（只读参考，勿改）：
  - `忆述光华_开工总结.md` — 收口总览（先读这个）
  - `忆述光华_MVP方案_v3.md` — 功能 F1-F9 + 技术栈 + 成本
  - `忆述光华_开发决策清单.md` — 32 项决策全记录
  - `忆述光华_开发规划+分工.md` — M0-M5 里程碑 + 7 人任务卡
  - `忆述光华_测试清单.md` — 17 模块 ~140 项（P0 ~60）
  - `忆述光华_数据库Schema设计.md` — 28 表 10 域
  - `忆述光华_外部API清单与成本.md` — 17 项外部依赖 + 服务器调研
  - `忆述光华_产品部验收标准更新转达稿.md` — 产品部确认项
  - `忆述光华_深度开发设计/01-05e` — 10 份深度设计定稿（B1 画像 / B2+02b RAG / B3 事件聚合 / B4 同步 / B5a-e 语音·护栏·纠错·后台·Windows）
  - `MVP完成度评估_20260827/` — 完成度评估九份（**02=用户故事目录（状态唯一来源）、07=08-27 基线汇报、08=收尾完成汇报**）
- `AGENTS.md` / `feature_list.json` / `progress.md` / `init.sh` / `session-handoff.md` — harness 文件；配套权威：`docs/决策台账.md`（术语消歧+决策+待拍板唯一登记簿）、`docs/lessons.md`（错误与教训单一来源）、`docs/lessons-主题索引.md`（按根因族复盘索引）
- `docs/parallel-dev/` — **开发期并行任务卡（「开发 Wave 1–4」，Agent A–L，2026-08-25~26，已结束）**。⚠️ **2026-09-23 harness 清理**：波次任务卡（00–13）与归档件已删（`git log --diff-filter=D` 可回看），**仅保留 `_B5a_B5d修正后待办.md`**——其中 J-1/J-2/J-3 三项待办**未闭环**，已登记 `docs/决策台账.md` §7。**MVP 修正（2026-08-26 拍板）：桌面端（F9/B5e/Zvec/Windows 相关）已移出 MVP 归二期，并行开发只覆盖 Android+云端+微信**
- `docs/parallel-dev-收尾/` — **收尾波次任务卡（「收尾 Wave 1–4」，2026-08-27~28，已全收口）**。⚠️ **2026-09-23 harness 清理**：任务卡与归档件（00–16/17–23）已删（`git log --diff-filter=D` 可回看），**仅保留 `19_wave3_真机补验跟踪表.md`**——该表 §4＝**缺陷台账（20 单：D-01~D-16、D-18~D-22；D-17 未启用；D-20 系补验修复单 f726942；D-22=08-29 P-2 诊断坐实 O-2 升单）与环境事件（O-1；O-2→D-22；O-3 暗物质审计；O-4 跨窗合流波）的唯一来源**，**其中 D-18/D-19 仍为「已修待复验」＝未关闭**。各卡是历史记录，勿当待办入口。⚠️「开发 Wave 4」（J/K/L）≠「收尾 Wave 4」（4a/4b 归档），全库术语表见 `docs/决策台账.md` §0
- `backend/` — FastAPI 后端（`app/api` · `app/services` · `app/workers`；**端口 8010**）
- `client/` — uni-app x 客户端（`pages` / `components` / `utils`(.uts) / `styles` / `uni_modules`；**改前先读 `skills/hbuilderx-uniappx-runloop/`**）
- `agent/` — LangGraph Agent 服务（`src/main.py`；**规范端口 8300**）
- `deploy/` — 部署与出包（`ONE_SWAP.md` 换址 · `RUNBOOK.md` · `scripts/preflight_pack.ps1` 出包前置门 · `download/` 分发页）
- `scripts/` — 门禁与工具（`review_agent.py` 提交门禁 · `test_agent.py` · `client_compile_gate.py` 客户端编译门 · `dev/` 本机联调脚本 · `realdevice/` 真机证据）
- `skills/` — 领域知识（`infisical-secrets` / `hbuilderx-uniappx-runloop` / `android-media-e2e` / `apk-cloud-release`）
- `docs/` — 决策台账（§5 待拍板 / §7 未关闭缺陷）· lessons · 各批次交接件
- `.trae/specs/` — 波次任务书（design-token / functional-fix / refactor-extensibility）
- `uvue_gen/` · `design_data*/` · `design_gap_audit/` — **生成物/设计数据，勿手改**
- `review/` · `_verify_*` — 视觉与真机验收证据，**勿清**
- `skills/infisical-secrets/SKILL.md` — Infisical 密钥链路 skill（所有外部 API Key 统一从这里取，2026-08-19 打通）
- `skills/hbuilderx-uniappx-runloop/SKILL.md` — 客户端开发主循环 skill（HBuilderX CLI 编译→真机运行→验证 + UTS 编译错误速查表 + 环境排查 + **收尾 Wave 3 真机教训：adb reverse 铁律 / EMUI 纯净模式 / 云打包 CLI 参数 / D-18/D-19 原生探测坑·**5.24 迁移期多窗协调军规**；**改 client/ 代码前先读**，2026-08-24 沉淀、08-28 补录）
- `skills/android-media-e2e/SKILL.md` — 真机 E2E 测试 skill（测试照片注入 scan_file / 权限 / 截屏像素定位 + **收尾 Wave 3 沉淀：语音短录音链 / 情绪三层默认值 / 云打包验证**；F1 链路真机验收流程，2026-08-24 沉淀、08-28 补录）
- `skills/harness-gate-maintenance/SKILL.md` — **harness 与门禁维护手册**（改门禁 / 加检查轴 / 动 STATE·progress·feature_list·handoff·init 前读；含两条铁律：新增门禁必须先做**负向探针自校验**、**运维域问题不阻断提交**）。2026-10-11 沉淀
- `skills/sse-streaming-integration/SKILL.md` — **对话域 SSE 流式对接范式**（三层帧协议 / StreamingResponse 生成器与 `contextvars` 陷阱 / **断连不丢回复**的后台线程+队列 / 客户端 `enableChunked` 手写 UTF-8 解码 / cards·chips 协议）。2026-10-11 沉淀
- `skills/apk-cloud-release/SKILL.md` — **Android 云打包与出包** skill（前置静态门 → 确认后端地址可达 → HBuilderX CLI 云打包自有证书 → 四项尸检：签名指纹 / 包内地址 / 权限 / apksigner → 部署分发路径）。含 2026-10-04 实测踩出的五个坑：**签名参数走错会静默用 DCloud 共享证书（已装用户装不上新包、只能卸载重装）**、口令必须走仓库外 config、临时隧道地址重启即失效、HBuilderX 单队列并发互杀、**本机 SSL 失败不等于隧道死（须云端视角验证）**；另附「装了却用不了」必查项（内测登录开关 / RQ worker / agent 服务 / 队列积压）。参数速查与故障对照见同目录 `reference.md`。2026-10-04 沉淀

## 当前状态

> ⚠️ **本节已改为指针（2026-10-11）**：「现在我们到哪了」的**唯一来源是 [`STATE.md`](STATE.md)**。
> 历史上本节自持数字（停在 2026-08-29，落后 43 天、跨 12 批变更），且与 `progress.md` /
> `session-handoff.md` / `feature_list.json` 各写一份 ⇒ 新会话按启动流程走**必然先读到旧真相**
> （实测：会去修早已收口的「4b 修复批次」）。**任何"下一步 / 阻塞 / 全局数字"只写在 `STATE.md`，
> 其余文档只准引用。**
>
> 考古用历史快照（2026-08-29 时点，**已作废**）：收尾 Wave 1–4 全收口；用户故事 ✅46/🟡7/❌0；
> A 级真机 32 条；下一步曾是"4b 修复批次"。原文见 `git log -p -- AGENTS.md`。

## 技术栈（已拍板，勿重开决策）

```
前端    uni-app x 双轨（Vue UI 层 + 自研 UTS 插件系统能力层）→ Android 主端
后端    FastAPI + RQ/Redis + PostgreSQL（28 表 10 域）+ Qdrant（named vectors）+ COS（STS 直传）
检索    BGE-M3 文本塔（dense+sparse）+ Qwen3-VL 图片塔（API）+ LLM 路由 + 双层 Rerank（默认关）+ 溯源
AI      FunASR/SenseVoice 双通道 + SetFit 分类 + qwen-flash 画像标注 + 百炼 Qwen3Guard 护栏
端侧    SQLite（XView）+ 自研 DAO（迁移/加密/对账）+ ST-DBSCAN 四层事件聚合（UTS 原生）
微信    企业微信「微信客服」+ 消息回调幂等
同步    字段级 LWW + 软删除 30 天 + 离线队列 + WiFi 原图/蜂窝缩略图
可观测  Sentry 云版双通道
——以下归二期（08-26 拍板移出 MVP）：Windows Electron + Python sidecar（PP-OCRv5 + JSON-RPC 2.0 + electron-updater）、XView/SQLCipher 自定义基座、真分片上传
```

## Startup Workflow

开始写代码前：

1. 确认工作目录 `pwd`（应为 D:\GuangH-App）
2. **读 [`STATE.md`](STATE.md)** —— 唯一「当前状态」：正在做什么 / 下一步 / 阻塞 / 关键数字
3. 通读本文件（规则与技术栈）
4. 读 `docs/决策台账.md` —— **重点 §5 待拍板与 §7 未关闭缺陷**（新会话最容易漏掉的两节）
5. 跑 `./init.ps1`（Windows）或 `./init.sh`（bash）验证环境健康
6. 读 `feature_list.json` 看当前特性状态（**约定：只允许 1 条 `in-progress`**）
7. `git log --oneline -5` 看最近提交，并与 `STATE.md` 记录的 HEAD 对照（不一致 ⇒ 状态可能已漂移）
8. 需要外部 Key 时按 `skills/infisical-secrets/SKILL.md` 操作：`infisical secrets --env=dev`（列名）/ `infisical secrets get <NAME> --plain --silent`（取单条）/ `infisical run --env=dev -- <命令>`（注入运行）
9. **客户端开发（client/）**：先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`（编译/真机/UTS 避坑）；真机相册链路验收按 `skills/android-media-e2e/SKILL.md`（测试照片注入/权限/验证）
10. **改门禁或 harness 文件前**：读 `skills/harness-gate-maintenance/SKILL.md`（含"负向探针自校验"与"运维域不阻断"两条铁律）

基线验证失败必须先修复，再开新范围。

## Working Rules

- **一次只做一个特性（One feature at a time）**：从 `feature_list.json` 选一个未完成特性
- **必须有验证**：未跑验证命令不许声称完成
- **更新产物**：会话结束前更新 `progress.md` 和 `feature_list.json`；决策/口径/待拍板有变化同步 `docs/决策台账.md`
- **不越界（Stay in scope）**：不修改与当前特性无关的文件
- **密钥纪律**：外部 API Key/凭证只经 Infisical 获取（skills/infisical-secrets/SKILL.md），永不硬编码/提交/聊天明文；Key 值回显属违规
- **交付文档只读**：`忆述光华_交付文档/` 是定稿参考，修改需用户明确同意
- **留干净状态**：下个会话必须能直接跑 `./init.sh`

## 里程碑（M0-M5，参考开发规划 v3）

| 里程碑 | 周 | 门禁 |
|---|---|---|
| M0 | W1-2 | POC 五测 D7 结论（UTS 三件套）+ 契约发布 + 合规申请提交 |
| M1 | W3-6 | 检索 Top3≥70%；分类≥75%；护栏可用 |
| M2 | W7-12 | 管线打通≥60% + 分类精度≥80%（两段门禁） |
| M3 | W13-15 | 微信收+找 10s/3s；不丢消息 99.9% |
| M4 | W16-17 | 端间同步一致；断电续传正确 |
| M5 | W18 | 数据安全全绿 + 测试清单 P0 全过 → 种子内测 |

> 实际执行节奏以「开发 Wave → 收尾 Wave → 4b 修复批次」推进（`docs/决策台账.md` §1）；本表为门禁参考口径。

## Definition of Done

特性完成当且仅当：

- [ ] 目标行为已实现
- [ ] 验证真实跑过（测试/lint/类型检查，或文档要求的手工验收）
- [ ] 证据记录在 `feature_list.json` 或 `progress.md`
- [ ] 仓库可从标准启动路径重启

## End of Session

1. 更新 `progress.md`
2. 更新 `feature_list.json`
3. 决策/口径变化登记 `docs/决策台账.md`；踩坑登记 `python scripts/lessons.py add`
4. 记录未解决风险/阻塞
5. 安全状态后提交（描述性 commit message）
6. 仓库保持 **restartable（clean）**：下个会话能直接跑 `./init.ps1` / `./init.sh`；**Next steps** 见 `STATE.md`

## Verification Commands

```bash
./init.ps1          # Windows（PowerShell）
./init.sh           # bash
```

外部 Key 可用性检查（输出含明文，注意屏幕安全，勿外发）：

```bash
infisical secrets --env=dev --silent
```

客户端与出包相关（`client/` 改动必跑第一条）：

```bash
python scripts/client_compile_gate.py                     # uni-app x 编译门（20~56s；缺 HBuilderX 时 --strict 才判环境错误）
pwsh -File scripts/dev/status.ps1                         # 本机联调全栈状态（Docker/backend/agent/worker/隧道/出包态）
pwsh -File deploy/scripts/preflight_pack.ps1 -AllowTunnel  # 出包前置门（全绿才允许云打包）
```


## Pre-Commit Review Gate（提交前审核，强制）

**任何 commit 之前必须通过代码质量审核 Agent，修复到通过为止，禁止带阻断项提交。**

**快/全量拆分（2026-08-26，用户拍板——原每次 commit 全量跑 5 分钟）**：

1. **快速门禁（commit 时，秒级）**：`python scripts/review_agent.py`（git hook 自动触发；手动跑亦可）——本次提交涉及的文件：Python 语法编译 / ruff lint / **密钥扫描（2026-10-11 起**含 `client/` 的 `.uts/.uvue`**）** / TODO 统计 / **structure（体积与重复棘轮）** / **audit_axes（契约·客户端·模型·导出四轴）** / **env_template** / **openapi_snapshot（契约字节级）** / **port_consistency（2026-10-11 新增）** / **harness_state（STATE.md 新鲜度，WARN 不阻断）** / lessons 强制登记。
2. **全量门禁（完成验收 / 集成 / CI 前必跑）**：`python scripts/review_agent.py --full`——仓库级语法/lint/密钥扫描 + 全量测试（test_agent：pytest + API 冒烟 + 原型验证，覆盖率阈值 60%，口径单一来源 `scripts/gate_exit`/`review_agent.COV_THRESHOLD`）。**每个 Agent 完成声明 DoD 前、以及集成 Agent merge 后，必须跑一次 `--full`。**
   - **门禁报红时的定位口径（2026-09-25 立规 · 教训见 `docs/lessons.md`）**：直接读**结构化失败用例清单**——
     `python -c "import json;d=json.load(open('.cowork-temp/test-report.json',encoding='utf-8'));print(d['failed_tests'])"`；
     门禁输出本身也已把 `[失败用例（先看这里）]` 置顶。**禁止**靠"重跑全量套件"去定位用例名（≈2 分钟/次的无谓开销），也**禁止**猜报告键名（机读键固定为 `failed_tests` / `blocking_sections` / `details`）。
3. 退出码 **0** = 通过可提交；**1** = 存在阻断项（代码违规）→ 修复后重跑；**2** = **环境错误**（工具/依赖不可用，门禁未真正判定）——判定单一来源见 `scripts/gate_exit.py`。`--full` 时另含 **`client_compile`（uni-app x 编译门，2026-10-11 新增）** 与 **`audit_security`（数据安全四域；backup 域超期只告警不阻断）**；`--skip-tests` 可只跑静态层，`--skip-client-compile` 跳过编译门（缺 HBuilderX 会自动降级放行）。
4. 报告输出：`.cowork-temp/review-report.json` + `.cowork-temp/test-report.json`（证据留档）
5. 修复流程：按报告逐项修复 → 重跑 `python scripts/review_agent.py` → 全绿才 `git commit`
6. hook 安装：`git config core.hooksPath .githooks`（仓库内 hook，见 .githooks/pre-commit）

**禁止绕过**：`--no-verify` 不允许使用；审核报告必须为 passed 状态。

**测试 Agent 单独使用**：`python scripts/test_agent.py [--cov-threshold N] [--only api|research]`
（本地开发循环中可只跑测试不跑全审核；commit 前仍必须走 review_agent 快速门禁，完成前走全量门禁。）

## 环境陷阱与经验

> ⚠️ 已迁移至 [docs/lessons.md](docs/lessons.md)「环境陷阱与经验」专区（2026-08-20 起单一来源，2026-08-29 补录至条目 33）。
> 踩坑必登记：`python scripts/lessons.py add --error ... --root-cause ...`（review_agent 强制）。
> 复盘索引：[docs/lessons-主题索引.md](docs/lessons-主题索引.md)（时间线台账按根因族归组）。

## Escalation

- **架构决策**：先查 `docs/决策台账.md`（基线后的补充拍板/口径/待拍板）→ 再查交付文档（开发决策清单 32 项），否则问用户
- **需求不清**：查交付文档（MVP v3 / 转达稿），否则问用户
- **反复测试失败**：更新 progress，标记人工审查
- **范围歧义**：重读 `feature_list.json` 的 definition of done

## 关键约束（来自开发决策清单）

- UTS 插件 = 写原生 Kotlin，POC 关卡在 M0 W1（失败 → 切原生 Kotlin/Flutter）
- 用户操作优先：手动合并/拆分/确认后，自动算法永不覆盖
- 软删除全局 30 天；画像丢 = 数月积累不可逆（备份 RPO≤24h/WAL≤5min）
- 护栏 fail-safe：百炼不可用时默认拒发而非放行
- MVP 不接：GraphRAG、Agentic RAG 默认开、iOS、鸿蒙、GPU 自部署
