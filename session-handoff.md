# Session Handoff — 忆述光华（交接说明）

> **版本**：2026-10-14 交接版 ｜ **分支**：`feature/agent-closeout-20261008` ｜ **HEAD**：`2881269`
> ⚠️ **本文件不承担「当前状态」**——那是 [`STATE.md`](STATE.md)（下一步/阻塞/关键数字的唯一来源）。
> 本文件只回答两件事：**接手前必须知道的纪律红线**（§1 起，长期有效）、**上一次交接做了什么**（§0）。
> 历史交接全文见 git / `progress.md`；本文件不滚雪球。
## 0. 本次交接（2026-10-14）

**主题**：接线缺口与性能根因修复（批次 0~3 已提交）。

- 🔴 **本波三个头号根因**（细节见 `docs/决策台账.md` §4.20）：
  ① **坐标系系统性偏移**——客户端 GPS 与照片 EXIF 均为 WGS84，而高德 regeo 只认 GCJ-02，
     全后端**零转换** ⇒ 地名偏 300~700m 且**不报错**（实测 481m/555m）。已在
     `services/external/amap.get_place` 设**单一转换点**（入库仍存 WGS84）。
  ② **编译门假通过**（影响所有 client 改动的可信度）——产物旁证用 `time.monotonic()` 与文件
     `st_mtime` 比大小 ⇒ 恒真；叠加 `cli launch --compile true` 在 HBuilderX 未启动时**不编译**
     ⇒ 门禁在"编译从未发生"时报 `ok`。已修；**注意 HBuilderX 须先 `cli open`（门禁已自动拉起）**。
  ③ **镜像/RAG 静默降级**——`if not token: return` 无日志 ⇒ 表现为"AI 说已记住、出卡，
     但时间轴空白"。已加降级计数 + 两侧**令牌指纹对账**（启动即可见）。
- 照片定位（三段断点）与照片分类（两端断点）两条"点了没用"的链路已端到端打通；含契约放宽
  `CorrectionCreate.text` 可空（空文本**跳过向量化**、仍回写权威分类——**不塞假文本**污染层①向量库）。
- 审计基线（47 条带行号证据）：`docs/审计_接线缺口与性能根因_20261014.md`。
- **未做完**（有前置条件，不做无验证的改动）：蜂窝省流上传五步（须「编译门 **+ 真机验证条件**」）、
  `attachments` 契约三层、ShareSheet；假开关诚实化与流畅度 12 条根因待后续批次。
- **未提交**：无（本批 4 次提交均过门禁；push 留用户）。

## Harness Markers（机器可读 · 双语）

> 供通用 harness 工具（如 harness-creator 的 `validate-harness.mjs`）识别——其判据为英文关键词。
> **实质内容以上文中文为准**；本块只是指针，指向唯一来源，避免第二次漂移。

- Last Updated: 2026-10-14
- Current State: 见 [`STATE.md`](STATE.md)（唯一「当前状态」）
- What: 接线缺口与性能根因修复（照片定位/分类接线、静默降级显性化、编译门假通过修复）
- Current Objective: 让"定义了却没接线"的功能真正可用，并让门禁不再假通过
- Next: 批次 4（假开关诚实化）→ 批次 5/6（UI 流畅度 12 条根因）→ 批次 7（根因固化为门禁）
- Recommended Next Step: 读 `docs/决策台账.md` §4.20 末节 + `docs/审计_接线缺口与性能根因_20261014.md`
- Blockers: 上传管线类修复须真机验证条件；ngrok 拦截页待真机验图片；百炼凭据待续期
- Files: `docs/审计_接线缺口与性能根因_20261014.md` · `backend/app/services/external/{gcj02,amap}.py` · `backend/app/services/photo_exif.py` · `scripts/client_compile_gate.py` · `STATE.md`
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
