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
  - `忆述光华_深度开发设计/01-05e` — 9 份深度设计定稿（B1 画像 / B2+02b RAG / B3 事件聚合 / B4 同步 / B5a-e 语音·护栏·纠错·后台·Windows）
- `AGENTS.md` / `feature_list.json` / `progress.md` / `init.sh` / `session-handoff.md` — harness 文件
- `docs/parallel-dev/` — **并行开发总纲与任务卡（2026-08-26 起，多窗口并行开发的唯一入口）**：`00_总纲_harness.md`（新 Agent 必读：仓库现状/文件域所有权/波次/完成定义）→ 各波次任务卡 `02`-`12` → `13_集成规则与冲突回退` → `_B5a_B5d修正后待办.md`。**MVP 修正（2026-08-26 拍板）：桌面端（F9/B5e/Zvec/Windows 相关）已移出 MVP 归二期，并行开发只覆盖 Android+云端+微信**
- `skills/infisical-secrets/SKILL.md` — Infisical 密钥链路 skill（所有外部 API Key 统一从这里取，2026-08-19 打通）
- `skills/hbuilderx-uniappx-runloop/SKILL.md` — 客户端开发主循环 skill（HBuilderX CLI 编译→真机运行→验证 + UTS 编译错误速查表 + 环境排查 + **Wave 3 真机教训：adb reverse 铁律 / EMUI 纯净模式 / 云打包 CLI 参数 / D-18/D-19 原生探测坑**；**改 client/ 代码前先读**，2026-08-24 沉淀、08-28 补录）
- `skills/android-media-e2e/SKILL.md` — 真机 E2E 测试 skill（测试照片注入 scan_file / 权限 / 截屏像素定位 + **Wave 3 沉淀：语音短录音链 / 情绪三层默认值 / 云打包验证**；F1 链路真机验收流程，2026-08-24 沉淀、08-28 补录）
- `DSH插件选型与安装方案.md` — 本机 DSH 环境文档（与产品无关）

## 当前收口状态（2026-08-28 · Wave 1–4 全部收口）

> **一句话**：收尾 17 项全部完成（代码侧 13 + Wave 3 真机 7 清单全达终态 + 4b 文档收口）。**下一步=4b 修复批次**（Wave 3 挖出的新缺陷，非文档收口）。基线 develop，最新相关提交见 `git log --oneline -8`。

- **完成度快照（终版，见 `忆述光华_交付文档/MVP完成度评估_20260827/08_收尾波次完成汇报_20260828.md`）**：功能代码 ~90%（代码侧缺口清零）｜内测可达度 60–70%（三座大山不变：外部凭证/真实数据/合规三申请）｜用户故事 **✅46/🟡7/❌0**（A 级真机 **32 条**，含 US-42/12/25/40/41 真机补验）｜性能门禁 18 项 **达标 10/部分 2/未达标 6**（30s 计时门禁 ✅ 实测 6.0s≤30s）。
- **真机 7 清单**：01 蜂窝链路 ✅（US-46/47/48→A）/ 02 录音中断 ❌（D-06，来电待补 P6）/ 03 首批 30s ✅（门禁过线）/ 04 L2/L3 归并 ✅（US-06/07→A）/ 05 转写情绪 🟡（US-17/18/19→A，S2 待校准）/ 06 编译冒烟 🟡（①中文 IME 转人工）/ 07 云打包 🟡（链路 A + 原生能力 D-18/D-19）；4a/4b 真机补验：US-42 导出 D-20 修复后 A 级、US-12/25/40/41 A 级（D-21 滚动/ O-2 裁决观察项另计）。证据：`scripts/realdevice/evidence/` 50 文件。
- **4b 修复批次（下一步工作，缺陷单全 19 条见 `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4）**：批次1=D-18（WorkManager 探测恒 false）/D-19（FGS manifest 未注册）原生能力对，重打包复验；批次2=D-16（情绪"平静"伪造）/D-07（短录音不落 COS 判死）/D-08（弃段）语音链；批次3=S2 情绪校准 + D-06 机型适配。
- **⚠️ 已知环境项**：O-1 uvicorn 搜索嵌入时刻瞬时原生崩溃（两次复发，疑 numpy 2.5.2/torch 兼容性，tracker 19 §5 有记）——修复批次优先排查；EMUI 纯净模式会拦 adb install 且零错误提示（先关纯净模式再装）。
- **📐 数字口径规范（重要）**：用户故事统计是**两维**——状态（✅端到端打通/🟡半通/❌未实现）与证据级（B/C/A 真机实测）**不可合并**。跨文档数字**必须回源核实**：用户故事状态以 `忆述光华_交付文档/MVP完成度评估_20260827/02_用户故事全目录与打通核实.md` 表格为唯一来源（该文档 line 119 的"✅42"是笔误，以表格逐条为准）；汇报"打通数变化"时须**明确基线时点 + 逐条列出 🟡→✅ 与 B/C→A 的移动**，避免"数字没变"的误读。

## 技术栈（已拍板，勿重开决策）

```
前端    uni-app x 双轨（Vue UI 层 + 自研 UTS 插件系统能力层）→ Android 主端
后端    FastAPI + RQ/Redis + PostgreSQL（28 表 10 域）+ Qdrant（named vectors）+ COS（STS 直传）
检索    BGE-M3 文本塔（dense+sparse）+ Qwen3-VL 图片塔（API）+ LLM 路由 + 双层 Rerank + 溯源
AI      FunASR/SenseVoice 双通道 + SetFit 分类 + qwen-flash 画像标注 + 百炼 Qwen3Guard 护栏
端侧    SQLite（XView）+ 自研 DAO（迁移/加密/对账）+ ST-DBSCAN 四层事件聚合（UTS 原生）
Windows Electron + Python sidecar（PP-OCRv5 20MB + JSON-RPC 2.0 v1 + electron-updater）
微信    企业微信「微信客服」+ 消息回调幂等
同步    字段级 LWW + 软删除 30 天 + 离线队列 + WiFi 原图/蜂窝缩略图
可观测  Sentry 云版双通道
```

## Startup Workflow

开始写代码前：

1. 确认工作目录 `pwd`（应为 D:\GuangH-App）
2. 通读本文件
3. 读 `忆述光华_交付文档/忆述光华_开工总结.md` 了解全局
4. 跑 `./init.sh` 验证环境健康
5. 读 `feature_list.json` 看当前特性状态
6. `git log --oneline -5` 看最近提交
7. 需要外部 Key 时按 `skills/infisical-secrets/SKILL.md` 操作：`infisical secrets --env=dev`（列名）/ `infisical secrets get <NAME> --plain --silent`（取单条）/ `infisical run --env=dev -- <命令>`（注入运行）
8. **客户端开发（client/）**：先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`（编译/真机/UTS 避坑）；真机相册链路验收按 `skills/android-media-e2e/SKILL.md`（测试照片注入/权限/验证）

基线验证失败必须先修复，再开新范围。

## Working Rules

- **一次只做一个特性**：从 `feature_list.json` 选一个未完成特性
- **必须有验证**：未跑验证命令不许声称完成
- **更新产物**：会话结束前更新 `progress.md` 和 `feature_list.json`
- **不越界**：不修改与当前特性无关的文件
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

## Definition of Done

特性完成当且仅当：

- [ ] 目标行为已实现
- [ ] 验证真实跑过（测试/lint/类型检查，或文档要求的手工验收）
- [ ] 证据记录在 `feature_list.json` 或 `progress.md`
- [ ] 仓库可从标准启动路径重启

## End of Session

1. 更新 `progress.md`
2. 更新 `feature_list.json`
3. 记录未解决风险/阻塞
4. 安全状态后提交（描述性 commit message）
5. 仓库保持 `./init.sh` 可立即运行

## Verification Commands

```bash
./init.sh
```

外部 Key 可用性检查（输出含明文，注意屏幕安全，勿外发）：

```bash
infisical secrets --env=dev --silent
```

## Pre-Commit Review Gate（提交前审核，强制）

**任何 commit 之前必须通过代码质量审核 Agent，修复到通过为止，禁止带阻断项提交。**

**快/全量拆分（2026-08-26，用户拍板——原每次 commit 全量跑 5 分钟）**：

1. **快速门禁（commit 时，秒级）**：`python scripts/review_agent.py`（git hook 自动触发；手动跑亦可）——只检查本次提交涉及的文件：Python 语法编译 / ruff lint / 密钥扫描 / TODO 统计 / lessons 强制登记。
2. **全量门禁（完成验收 / 集成 / CI 前必跑）**：`python scripts/review_agent.py --full`——仓库级语法/lint/密钥扫描 + 全量测试（test_agent：pytest + API 冒烟 + 原型验证，覆盖率阈值 50%）。**每个 Agent 完成声明 DoD 前、以及集成 Agent merge 后，必须跑一次 `--full`。**
3. 退出码 0 = 通过可提交；退出码 1 = 存在阻断项 → 修复后重跑，直到通过
4. 报告输出：`.cowork-temp/review-report.json` + `.cowork-temp/test-report.json`（证据留档）
5. 修复流程：按报告逐项修复 → 重跑 `python scripts/review_agent.py` → 全绿才 `git commit`
6. hook 安装：`git config core.hooksPath .githooks`（仓库内 hook，见 .githooks/pre-commit）

**禁止绕过**：`--no-verify` 不允许使用；审核报告必须为 passed 状态。

**测试 Agent 单独使用**：`python scripts/test_agent.py [--cov-threshold N] [--only api|research]`
（本地开发循环中可只跑测试不跑全审核；commit 前仍必须走 review_agent 快速门禁，完成前走全量门禁。）

## 环境陷阱与经验

> ⚠️ 已迁移至 [docs/lessons.md](docs/lessons.md)「环境陷阱与经验」专区（2026-08-20，单一来源）。
> 踩坑必登记：`python scripts/lessons.py add --error ... --root-cause ...`（review_agent 强制）。

## Escalation

- **架构决策**：查交付文档（开发决策清单已收敛 32 项），否则问用户
- **需求不清**：查交付文档（MVP v3 / 转达稿），否则问用户
- **反复测试失败**：更新 progress，标记人工审查
- **范围歧义**：重读 `feature_list.json` 的 definition of done

## 关键约束（来自开发决策清单）

- UTS 插件 = 写原生 Kotlin，POC 关卡在 M0 W1（失败 → 切原生 Kotlin/Flutter）
- 用户操作优先：手动合并/拆分/确认后，自动算法永不覆盖
- 软删除全局 30 天；画像丢 = 数月积累不可逆（备份 RPO≤24h/WAL≤5min）
- 护栏 fail-safe：百炼不可用时默认拒发而非放行
- MVP 不接：GraphRAG、Agentic RAG 默认开、iOS、鸿蒙、GPU 自部署
