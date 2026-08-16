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
- `DSH插件选型与安装方案.md` — 本机 DSH 环境文档（与产品无关）

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

基线验证失败必须先修复，再开新范围。

## Working Rules

- **一次只做一个特性**：从 `feature_list.json` 选一个未完成特性
- **必须有验证**：未跑验证命令不许声称完成
- **更新产物**：会话结束前更新 `progress.md` 和 `feature_list.json`
- **不越界**：不修改与当前特性无关的文件
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

当前阶段（纯文档仓库）验证 = 交付文档完整性 + git 状态干净；进入代码阶段后替换为真实测试命令。

## Pre-Commit Review Gate（提交前审核，强制）

**任何 commit 之前必须通过代码质量审核 Agent，修复到通过为止，禁止带阻断项提交。**

1. 运行审核：`python scripts/review_agent.py`（git hook 自动触发；手动跑亦可）
2. 审核项：Python 语法编译 / ruff lint / **测试（test_agent：pytest 18 项 + API 冒烟 + 原型验证，覆盖率阈值 50%）** / 密钥扫描 / TODO 统计
3. 退出码 0 = 通过可提交；退出码 1 = 存在阻断项 → 修复后重跑，直到通过
4. 报告输出：`.cowork-temp/review-report.json` + `.cowork-temp/test-report.json`（证据留档）
5. 修复流程：按报告逐项修复 → 重跑 `python scripts/review_agent.py` → 全绿才 `git commit`
6. hook 安装：`git config core.hooksPath .githooks`（仓库内 hook，见 .githooks/pre-commit）

**禁止绕过**：`--no-verify` 不允许使用；审核报告必须为 passed 状态。

**测试 Agent 单独使用**：`python scripts/test_agent.py [--cov-threshold N] [--only api|research]`
（本地开发循环中可只跑测试不跑全审核；commit 前仍必须走 review_agent 全量。）

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
