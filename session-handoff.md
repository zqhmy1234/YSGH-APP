# Session Handoff — 忆述光华（交接说明）

> **版本**：2026-10-11 交接版 ｜ **分支**：`feature/agent-closeout-20261008` ｜ **HEAD**：`bbb1c3e`
> ⚠️ **本文件不承担「当前状态」**——那是 [`STATE.md`](STATE.md)（下一步/阻塞/关键数字的唯一来源）。
> 本文件只回答两件事：**接手前必须知道的纪律红线**（§1 起，长期有效）、**上一次交接做了什么**（§0）。
> 历史交接全文见 git / `progress.md`；本文件不滚雪球。
## 0. 本次交接（2026-10-11）

**主题**：harness / 门禁系统性更新 + 「新包可跑」修复（环境配置漂移根治）。

- 🔴 **最重要的结论**：重构后"新包各页面不可用、数据流被切断"的**头号根因**是
  `client/utils/config.uts` 的环境三件套随代码漂移（10-09 把 `ENV` 改 `dev` 并提交，
  出包忘改回）⇒ 正式包连 `127.0.0.1:8010`。已用运行时判据 `isDevCarrier()` 根治，
  并把三件套切换脚本化（`scripts/dev/write_base_url.ps1`）。详见 `docs/决策台账.md` §4.18。
- 新增本机联调工具链：`scripts/dev/{tunnel,write_base_url,start_all,status}.ps1`
  （**`start_all`/`status` 已实测**：一键起 Docker+backend+worker+agent，全栈状态一览）。
- 门禁升级：新增客户端编译门 `scripts/client_compile_gate.py` 并接入 `--full` 与出包前置门；
  修 `check_secrets` **漏扫 `client/`** 的盲区；接入孤儿脚本 `audit_security`（备份域只告警）；
  新增 `port_consistency` 与 `harness_state` 两个检查。
- state 改造：新增 `STATE.md`；`AGENTS.md` 状态节降级为指针并补齐仓库结构/门禁口径；
  `progress.md` 去掉自相矛盾的 08-29 速览（改「以最后一条为准」）；`feature_list.json` 加策略字段。
- **未提交**：`tracked` 之外的工作区改动（本批）按 §1.1 纪律**逐路径 add**，禁 `git add -A`。
## Harness Markers（机器可读 · 双语）

> 供通用 harness 工具（如 harness-creator 的 `validate-harness.mjs`）识别——其判据为英文关键词。
> **实质内容以上文中文为准**；本块只是指针，指向唯一来源，避免第二次漂移。

- Last Updated: 2026-10-11
- Current State: 见 [`STATE.md`](STATE.md)（唯一「当前状态」）
- What: harness / 门禁系统性更新 + 「新包可跑」修复（环境配置漂移根治）
- Current Objective: 让重构后的新包在真机可用；补齐长期欠账的门禁
- Next: 打通固定地址出包链 → 云打包 → **无数据线**真机验收
- Recommended Next Step: `scripts/dev/tunnel.ps1` → `write_base_url.ps1` → `preflight_pack.ps1 -AllowTunnel` → `scripts/dev/pack.ps1`
- Blockers: ngrok 静态域名与 authtoken 待用户领取；百炼凭据（Token Plan）待续期
- Files: `client/utils/config.uts` · `scripts/dev/*` · `deploy/scripts/preflight_pack.ps1` · `STATE.md` · `AGENTS.md`
- Next Session: 起服务（`start_all.ps1`）→ 起隧道 → 回写三件套 → 出包门 → 打包 → 真机验收

## 1. 🔴 工作区红线（新同学必读，本次亲踩）

### 1.1 git-safety 铁律

| 铁律 | 规则 |
|---|---|
| 第零定律 | **git ref 会静默失败**。每次 commit 后必三通道核对：`git rev-parse HEAD` + `git log --oneline -1` + `git for-each-ref refs/heads/<分支>`。不一致即真身未更新。 |
| 第一定律 | **cwd 每命令漂移**。每条 git 命令必 `cd /d D:\GuangH-App &&` 前缀（或 `git -C D:\GuangH-App`）。 |
| ☢️ client/ 地雷 | `client/` 任何 git 写操作会触发 AV **批量删除工作树文件**。commit client/ 后必 `git status --short -- client` 查 ` D`，发现立即 `git checkout -- client/` 还原。 |
| 禁 `git add -A` | 并行环境会扫入未知文件/密钥。一律**逐路径 add**。 |
| 超时 | 所有 git 操作给足超时（user 铁律，毁了历史担待不起）。 |

### 1.2 pre-commit 门禁（AGENTS.md 强制，禁 `--no-verify`）

`scripts/review_agent.py` 快通模式，检查：Python 语法 / ruff lint / **密钥扫描** / TODO 统计 / **lessons 强制登记**。

**lessons 强制登记闭环陷阱（本次亲踩，反复卡了 4 次）**：
- 任一次失败会写 `.cowork-temp/last-failure.json` 记录失败时间戳。
- 之后必须 `python scripts/lessons.py add --error "…" --root-cause "…"` 登记新教训，且**教训时间戳须晚于最后一次失败时间**，否则持续阻断 commit。
- ⚠️ 教训文本本身不得含 `password=` / `secret=` 等密钥正则字面量——否则二次触发密钥门禁；且**每次失败提交会刷新失败时间戳**，把你的教训"变旧" → 再登记一条。正确顺序：**修复到通过 → 登记新教训 → 立即提交**（三者尽量同一脚本连做）。
- 密钥扫描误报源：Android uiautomator UI 转储 XML 里密码输入框的 `password` 标志（属性值 true）。真机核验证据只提交 PNG，或先脱敏再提交。
- 密钥只走 Infisical（`skills/infisical-secrets/SKILL.md`），永不硬编码/提交。

## 2. 📚 权威信息源（不要凭记忆，先读这些）

| 文件 | 用途 |
|---|---|
| `STATE.md`（唯一「当前状态」；2026-10-11 起 AGENTS.md 已降级为指针） | 数字唯一现行口径（用户故事 ✅46/🟡7/❌0 等） |
| `docs/决策台账.md` | 术语消歧 + 决策 + **待拍板唯一登记簿** |
| `docs/远期待办总账.md` | A 后端契约缺口 / B 客户端远期 / C 验收悬账 / P 性能 / R9 类 |
| `progress.md` | 已完成项速查卡 |
| `feature_list.json` | 特性状态 |
| `docs/lessons.md` | 环境陷阱单一来源（时间线台账 + 主题索引） |
| `docs/决策台账.md` §7 | **未关闭重大缺陷唯一登记簿**（2026-09-23 审计登记，7 条） |
| `docs/审计_未关闭缺陷_20260923.md` | 系统性缺陷审计（含撤销的假缺陷、未覆盖范围声明、harness 清理清单） |
| `docs/待办处理排期_20260923.md` | 待办按「难易 × 服务器约束」的批次排期 |

缺陷台账以 `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4（**D-01~D-22**）为权威；缺陷状态一律以台账为准，勿凭记忆报。
**漂移门禁**：`python scripts/audit_harness.py all`（**四轴**：契约双向对拍 / 客户端漂移 / ORM 模型使用率 / **导出-引用可达性**；退出码 1＝有 CRITICAL）。**新增或改名页面、增删路由、加模型、加能力函数后各跑一次**——轴 4 专查"能力写了但没接线"，首查即得 9 处（见台账 §7.8）。

## 3. 🚧 非代码阻塞（交接时仍未解，按优先级）

1. **凭证**：企微 `WECHAT_TOKEN` / `WECHAT_ENCODING_AES_KEY` 两键缺失（Infisical dev）；另 M1 服务器 / M4 证书 / M6 DCloud 签名等部署前置未齐。
2. **数据**：正式真值数据未采集（B/C/D 真值 + A 批负样本 + 正式文案库）。
3. **合规 / 上架前置**：ICP 备案（**法定 20 工作日**，需中国大陆节点服务器 ≥3 月 + 域名实名 + 主体一致）、软著、企微认证、隐私政策备案号 in-app 展示、算法备案（beian.cac.gov.cn，待核实）。详见工作记忆「上架合规硬约束」。

## 4. 🧩 代码侧遗留（以台账为准）

- **D-18 / D-19**（UTS 插件 / WorkManager 原生能力对）：代码已落地，待 R2 重打包复验（`initBackgroundTasks ok` + `aapt dump manifest` 见 `DataSyncService`）。
- **D-16 / D-22**（语音链：情绪"平静"伪造 / 纠错恒 mixed）：已修，待复验。
- **S2 情绪校准** + **D-06** 录音中断机型适配（来电场景补测需第二设备）。
- 其余 A/B/C 类缺口与待拍板项：见 `docs/远期待办总账.md` 与 `docs/决策台账.md` §5。

## 5. 📱 真机 / 环境要点

- nova 11 = `DKS9K23526028855`；统一 system **adb v41**（HBuilderX 内嵌 v36 会互杀 server）；每次 cli launch 后必补 `adb reverse tcp:8000 tcp:8000`；EMUI **纯净模式**拦 install 且零提示——先关再装。
- 改 `client/` 前先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`；真机验收先读 `skills/android-media-e2e/SKILL.md`；编译推包按 `uvue-deploy-device-ops` skill（deploy_one.sh 轮询版，reverse 先于启动）。
- 后端测试依赖 **Docker Desktop**（`yishu-redis` / `yishu-qdrant`）——机器重启后要拉起。

## 6. 🚀 新会话启动

`AGENTS.md` Startup Workflow → 本文件 → `./init.sh` → `git log --oneline -8`。

## 7. 历史交接存档（一行一届）

- **2026-08-25** ASR/语音会话（FunASR Flash + SenseVoice ONNX 情绪异步两段，状态四态，生产拒 mock）。
- **2026-08-26** 开发 Wave 1→3（pytest 312→420，CI #21 首绿，技术债 P0 八项，Wave 4 J/L/K 落地）。
- **2026-08-27** 收尾 Wave 1（UTS 编译基线修复 + P0-2 搜索性能 + A3/D1 补集成）。
- **2026-08-28** 收尾 Wave 3（真机 7 清单全终态，缺陷 D-01~D-21 + O-1/O-2，4a/4b 收口，终值 ✅46/🟡7/A32）。
- **2026-08-29** harness 台账整饬（决策台账 + lessons 主题索引）；R1 波 fix/4b 八枚 + 暗物质审计 O-3。
- **2026-09-01** 跨窗合流波：wrap1 UI 还原 + 5.24 迁移 17 枚合流 `cea5025`；fix/4b rebase `d09f639`；编译门重启通过。
- **2026-09-02~04** missing-pages 施工窗（W0-W5 画布还原，执行计划 v2 定稿 + 垃圾清理 `00d354a`）。
- **2026-09-11** UTS 插件修复工单（D-18/D-19 原生能力对收口推进）。
- **2026-09-13** 交接提交（`713055f` 推送 origin/develop）：harness/docs/数据/核验证据快照 + client UTS 改动；重写本交接说明。
