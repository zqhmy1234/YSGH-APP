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
  - `MVP完成度评估_20260827/` — 完成度评估九份（**02=用户故事目录（状态唯一来源）、07=08-27 基线汇报、08=收尾完成汇报**）
- `AGENTS.md` / `feature_list.json` / `progress.md` / `init.sh` / `session-handoff.md` — harness 文件；配套权威：`docs/决策台账.md`（术语消歧+决策+待拍板唯一登记簿）、`docs/lessons.md`（错误与教训单一来源）、`docs/lessons-主题索引.md`（按根因族复盘索引）
- `docs/parallel-dev/` — **开发期并行开发任务卡（「开发 Wave 1–4」，Agent A–L，2026-08-25~26，已结束）**：00 总纲（文件域所有权/波次/完成定义）→ 02–12 任务卡 → 13 集成规则 → `_B5a_B5d修正后待办.md`。**MVP 修正（2026-08-26 拍板）：桌面端（F9/B5e/Zvec/Windows 相关）已移出 MVP 归二期，并行开发只覆盖 Android+云端+微信**
- `docs/parallel-dev-收尾/` — **收尾波次任务卡（「收尾 Wave 1–4」，2026-08-27~28，已全收口）**：00 总纲（17 项范围+远期待办 §7）→ 01–12 任务卡 → 13 集成 → 14 INT → 15 真机 SOP → 16/21/22 归档（4a/4b）→ 17–20 附表。**19 号跟踪表 §4＝缺陷台账（20 单：D-01~D-16、D-18~D-22；D-17 未启用；D-20 系补验修复单 f726942；D-22=08-29 P-2 诊断坐实 O-2 升单）与环境事件（O-1；O-2→D-22）唯一来源**；各卡是历史记录，勿当待办入口。⚠️「开发 Wave 4」（J/K/L）≠「收尾 Wave 4」（4a/4b 归档），全库术语表见 `docs/决策台账.md` §0
- `skills/infisical-secrets/SKILL.md` — Infisical 密钥链路 skill（所有外部 API Key 统一从这里取，2026-08-19 打通）
- `skills/hbuilderx-uniappx-runloop/SKILL.md` — 客户端开发主循环 skill（HBuilderX CLI 编译→真机运行→验证 + UTS 编译错误速查表 + 环境排查 + **收尾 Wave 3 真机教训：adb reverse 铁律 / EMUI 纯净模式 / 云打包 CLI 参数 / D-18/D-19 原生探测坑**；**改 client/ 代码前先读**，2026-08-24 沉淀、08-28 补录）
- `skills/android-media-e2e/SKILL.md` — 真机 E2E 测试 skill（测试照片注入 scan_file / 权限 / 截屏像素定位 + **收尾 Wave 3 沉淀：语音短录音链 / 情绪三层默认值 / 云打包验证**；F1 链路真机验收流程，2026-08-24 沉淀、08-28 补录）
- `DSH插件选型与安装方案.md` — 本机 DSH 环境文档（与产品无关）

## 当前状态（2026-08-29 · 收尾 Wave 1–4 全收口，下一步＝4b 修复批次）

> **一句话**：收尾 17 项全部完成（代码侧 13 + 真机 7 清单全达终态 + 4a/4b 文档收口）。**下一步=4b 修复批次**（真机挖出的缺陷修复+复验，非文档工作）。基线 develop，最新提交见 `git log --oneline -8`。

- **完成度快照（终版，详见 `忆述光华_交付文档/MVP完成度评估_20260827/08_收尾波次完成汇报_20260828.md`）**：功能代码 ~90%（代码侧缺口清零）｜内测可达度 60–70%（三座大山不变：外部凭证/真实数据/合规三申请）｜用户故事 **✅46/🟡7/❌0**（A 级真机 **32 条**）｜性能门禁 18 项 **达标 10/部分 2/未达标 6**（30s 计时 ✅ 实测 6.0s≤30s）。剩余 🟡7=**US-20/21/31/32/33/44/52**（卡点：D-06 录音中断×2、D-16 修复复验×1[文案库卡点已 08-29 拍板解除]、企微/微信凭证×3、内容合规×1）。
- **真机 7 清单终判**：01 蜂窝链路 ✅（US-46/47/48→A）/ 02 录音中断 ❌（D-06，来电待补 P6）/ 03 首批 30s ✅ / 04 L2/L3 归并 ✅（US-06/07→A）/ 05 转写情绪 🟡（US-17/18/19→A，S2 待校准）/ 06 编译冒烟 🟡（①②中文 IME 转人工）/ 07 云打包 🟡（链路 A + D-18/D-19）；补验：US-42（D-20 修复后）/12/25/40/41 →A。证据 `scripts/realdevice/evidence/` 50 文件。**08 附录/progress/handoff 中"4b 终版 ✅41/A27"为补验前时点值，已被本条取代。**
- **4b 修复批次（下一步）**：批次1＝D-18（WorkManager 探测恒 false）/D-19（FGS manifest 未注册）原生能力对，重打包复验；批次2＝D-16（情绪"平静"伪造；✅08-29 R1-c 已修待复验）/D-07（短录音不落 COS 判死）/D-08（弃段）语音链＋**D-22（O-2 坐实，纠错恒 mixed）并批建议待拍板**；批次3＝S2 情绪校准＋D-06 机型适配；**散单待并批**：D-05/D-10/D-14/D-21（均客户端向，建议随批次2）＋D-09 遗留/D-12 改进（`docs/决策台账.md` §1.6，待排期确认）。修复方向/验收口径见 tracker 19 §4。
- **⚠️ 已知环境项**：O-1 uvicorn 搜索嵌入 exit 1（08-29 P-2 诊断：**版本漂移证伪**，非原生 AV，疑外部终止/内存压力——取证包+stage2 条件复现见 `docs/P2诊断_O1O2_20260829.md`，tracker 19 §5）；客户端编译门待他窗 **UTS 5.24 迁移**落地（develop 干净基线在 5.24 下编不过，此前「编译通过」均为脏工作区假象，lessons 08-29）；EMUI 纯净模式拦 adb install 且零错误提示（先关纯净模式再装）。
- **📐 数字口径规范**：用户故事统计**两维**——状态（✅/🟡/❌）×证据级（C/B/A）**不可合并**；跨文档数字回源 `02_用户故事全目录与打通核实.md` 表格（08-29 经用户授权全表同步至终值 ✅46/🟡7/❌0，旧统计行笔误已订正）。汇报"打通数变化"须带**基线时点+逐条列出移动**。**改任何全局数字必须五处同步：AGENTS 本节 + 08 文档（需授权）+ progress.md 头部速览 + session-handoff.md 速览 + feature_list.json 相关 evidence**——多窗口漂移是本项目的最大文档债（教训见 lessons 08-28 20:53；术语族定义见 `docs/决策台账.md` §0）。

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
2. 通读本文件
3. 读 `docs/决策台账.md`（术语+决策+待拍板对齐）与 `忆述光华_交付文档/忆述光华_开工总结.md`（全局）
4. 跑 `./init.sh` 验证环境健康
5. 读 `feature_list.json` 看当前特性状态
6. `git log --oneline -5` 看最近提交
7. 需要外部 Key 时按 `skills/infisical-secrets/SKILL.md` 操作：`infisical secrets --env=dev`（列名）/ `infisical secrets get <NAME> --plain --silent`（取单条）/ `infisical run --env=dev -- <命令>`（注入运行）
8. **客户端开发（client/）**：先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`（编译/真机/UTS 避坑）；真机相册链路验收按 `skills/android-media-e2e/SKILL.md`（测试照片注入/权限/验证）

基线验证失败必须先修复，再开新范围。

## Working Rules

- **一次只做一个特性**：从 `feature_list.json` 选一个未完成特性
- **必须有验证**：未跑验证命令不许声称完成
- **更新产物**：会话结束前更新 `progress.md` 和 `feature_list.json`；决策/口径/待拍板有变化同步 `docs/决策台账.md`
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
6. 仓库保持 `./init.sh` 可立即运行

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
