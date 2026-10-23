# Session Handoff — 忆述光华（交接说明）

> **版本**：2026-10-24 交接版 ｜ **分支**：`feature/agent-closeout-20261008`（以 `git branch --show-current` 为准）｜ **HEAD**：以 `git log -1 --oneline` 为准（上一版写死的 `2881269` 已过期，**不再在本行固化管理数字**）
> ⚠️ **本文件不承担「当前状态」**——那是 [`STATE.md`](STATE.md)（正在做什么 / 下一步 / 阻塞 / 关键数字的**唯一**来源）。
> 本文件只回答两件事：**接手前必须知道的纪律红线**（§1 起，长期有效）、**上一次交接做了什么**（§0）。
> 历史交接全文见 git / `progress.md`；本文件不滚雪球。
> 🧹 **2026-10-24 重写**：原 §3「非代码阻塞」、§4「代码侧遗留」两节是**状态复述**（与 `STATE.md` 各写一份 ⇒ 必漂移），
> 已按职责边界**删除复述、改为指针**（阻塞看 `STATE.md`「阻塞」，未关闭缺陷看 `docs/决策台账.md` §7 与
> `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4，待办看 `docs/待办总账.md`）。
> **§1 / §2 / §3（长期外部前置）/ §4（真机环境）/ §5 / §6 的长期规则一条未删**，只在原处更新事实。

## 0. 上次交接（2026-10-24）

**上次交接（2026-10-14）主题**：接线缺口与性能根因修复（批次 0~3 已提交）。那批的三个头号根因仍值得记住，
因为它们是**同类错误的模板**（细节见 `docs/决策台账.md` §4.20 与 `docs/审计_接线缺口与性能根因_20261014.md`）：

- ① **坐标系系统性偏移**——客户端 GPS 与照片 EXIF 均为 WGS84，高德 regeo 只认 GCJ-02，全后端零转换 ⇒
  地名偏 300~700m 且**不报错**（实测 481m/555m）。已在 `services/external/amap.get_place` 设**单一转换点**（入库仍存 WGS84）。
- ② **编译门假通过**——产物旁证拿 `time.monotonic()` 与文件 `st_mtime` 比大小（量级不同 ⇒ 恒真），
  叠加 `cli launch --compile true` 在 HBuilderX 未启动时**不编译** ⇒ 门禁在"编译从未发生"时报 `ok`。
  已修（墙上时钟 + 识别拒绝语 + 调用前 `cli open`）。**凡"用副作用旁证成功"的判据，必须同基准比时间且配负向探针。**
- ③ **镜像/RAG 静默降级**——`if not token: return` 无日志 ⇒ 表现为"AI 说已记住、出卡，但时间轴空白"。
  已加降级计数 + 两侧**令牌指纹对账**（启动即可见）。

**10-14 → 10-24 之间发生了什么**：见 `progress.md`「历史批次速查卡」（10-15 / 10-16 / 10-19 / 10-23 / 10-24 各行）
与末尾三批全文。**本文件不复制其中的状态与数字。**

**接手第一件事**：读 [`STATE.md`](STATE.md)（正在做什么 / 下一步 / 阻塞），再跑 `pwsh -File ./init.ps1`。

## Harness Markers（机器可读 · 双语）

> 供通用 harness 工具（如 harness-creator 的 `validate-harness.mjs`）识别——其判据为英文关键词。
> **实质内容以上文中文为准**；本块只是指针，指向唯一来源，避免第二次漂移。⚠️ **英文键名不可删**。

- Last Updated: 2026-10-24
- Current State: 见 [`STATE.md`](STATE.md)（唯一「当前状态」）
- What: 「当前有效交接」——上次交接要点（2026-10-14 的三个头号根因）+ 长期纪律（git-safety / 门禁 / 环境 / 启动）+ 一行一届的历史存档
- Current Objective: 见 [`STATE.md`](STATE.md)「正在做什么」
- Next: 见 [`STATE.md`](STATE.md)「下一步」
- Recommended Next Step: 读 [`STATE.md`](STATE.md) → 跑 `pwsh -File ./init.ps1` → 再读 `docs/决策台账.md` §5 待拍板与 §7 未关闭缺陷
- Blockers: 见 [`STATE.md`](STATE.md)「阻塞」第 1–8 条（本文件不再复述）
- Files: [`STATE.md`](STATE.md) · `progress.md` · `AGENTS.md` · `docs/决策台账.md` · `docs/待办总账.md` · `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md`
- Next Session: 起服务（`pwsh -File scripts/dev/start_all.ps1`）→ 起隧道（`scripts/dev/tunnel.ps1`）→ 回写地址（`write_base_url.ps1`）→ 出包门（`preflight_pack.ps1`）→ 打包 → 真机验收

## 1. 🔴 工作区红线（新同学必读，历次亲踩）

### 1.1 git-safety 铁律

| 铁律 | 规则 |
|---|---|
| 第零定律 | **git ref 会静默失败**。每次 commit 后必三通道核对：`git rev-parse HEAD` + `git log --oneline -1` + `git for-each-ref refs/heads/<分支>`。不一致即真身未更新。 |
| 第一定律 | **cwd 每命令漂移**。每条 git 命令必 `cd /d D:\GuangH-App &&` 前缀（或 `git -C D:\GuangH-App`）。 |
| ☢️ client/ 地雷 | `client/` 任何 git 写操作会触发 AV **批量删除工作树文件**。commit client/ 后必 `git status --short -- client` 查 ` D`，发现立即 `git checkout -- client/` 还原。 |
| 禁 `git add -A` | 并行环境会扫入未知文件/密钥。一律**逐路径 add**。 |
| 超时 | 所有 git 操作给足超时（user 铁律，毁了历史担待不起）。 |

### 1.2 pre-commit 门禁（AGENTS.md 强制，禁 `--no-verify`）

`scripts/review_agent.py` 快通模式，检查：Python 语法 / ruff lint / **密钥扫描** / TODO 统计 / structure / audit_axes /
env_template / openapi_snapshot / port_consistency / harness_state / **lessons 强制登记**（完整清单见 `AGENTS.md`）。

**lessons 强制登记闭环陷阱（亲踩，曾反复卡 4 次）**：

- 任一次失败会写 `.cowork-temp/last-failure.json` 记录失败时间戳。
- 之后必须 `python scripts/lessons.py add --error "…" --root-cause "…"` 登记新教训，且**教训时间戳须晚于最后一次失败时间**，否则持续阻断 commit。
- ⚠️ 教训文本本身不得含 `password=` / `secret=` 等密钥正则字面量——否则二次触发密钥门禁；且**每次失败提交会刷新失败时间戳**，把你的教训"变旧" → 再登记一条。正确顺序：**修复到通过 → 登记新教训 → 立即提交**（三者尽量同一脚本连做）。
- **禁止删 `.cowork-temp` 状态文件绕过门禁**（那等于伪造"没失败过"）。
- 密钥扫描误报源：Android uiautomator UI 转储 XML 里密码输入框的 `password` 标志（属性值 true）。真机核验证据只提交 PNG，或先脱敏再提交。
- 密钥只走 Infisical（`skills/infisical-secrets/SKILL.md`），永不硬编码/提交。

### 1.3 改动纪律（历次踩出来的）

- **改 `client/` 前**先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`；真机验收读 `skills/android-media-e2e/SKILL.md`；出包读 `skills/apk-cloud-release/SKILL.md`；对话流读 `skills/sse-streaming-integration/SKILL.md`。
- **改门禁 / 加检查轴 / 动 harness 文件前**读 `skills/harness-gate-maintenance/SKILL.md`（8 条铁律，含「新增门禁必须先做负向探针自校验」「运维域问题不阻断提交」「PowerShell 含中文必须 UTF-8 BOM，.md 必须无 BOM」）。
- **契约变更必须同批重导快照**：`python scripts/gen_openapi.py`（人工手改 = 字段级漂移无人能证）。
- **新增 config 字段必须同批补两份模板**：`backend/.env.example` **与** `deploy/.env.production.template`（`env_template` 轴比的是后者）。
- **新增任何导出（含可观测计数器）必须同批给调用点或删掉**（轴 4 会拦零调用导出；本项目"写了没接线"已复发多次）。
- **「编译通过」不是正确性证据**：uni-app x 编译只覆盖语法/解析，不覆盖跨模块符号与类成员（GAP-1/GAP-2，见 `scripts/audit_client_imports.py` / `audit_class_members.py` 两轴）。改客户端必须配轴 6/轴 7 + 真机。

## 2. 📚 权威信息源（不要凭记忆，先读这些）

| 文件 | 用途 |
|---|---|
| `STATE.md` | **唯一「当前状态」**：正在做什么 / 下一步 / 阻塞 / 关键数字（2026-10-11 起 `AGENTS.md` 已降级为指针，本文件同） |
| `docs/决策台账.md` | 术语消歧（§0）+ 决策 + **§5 待拍板唯一登记簿** + **§7 未关闭重大缺陷唯一登记簿** |
| `docs/待办总账.md` | **待办/缺陷汇总登记簿**（2026-10-24 新建；各分册改为历史指针） |
| `docs/远期待办总账.md` · `docs/待办处理排期_20260923.md` · `docs/功能对齐计划_20261006.md` · `docs/拿key后推进计划.md` · `docs/内部阻塞项_20261019.md` | 分领域待办（新条目只写总账，勿在分册新增） |
| `progress.md` | 追加式时间线（**以最后一条为准**；历史批次已压缩为速查卡） |
| `feature_list.json` | 能力清单（用户故事维度；**并行开发**，非"同时只做一条"；in-progress 条数为**如实**状态） |
| `docs/lessons.md` + `docs/lessons-主题索引.md` | 踩坑单一来源（带 commit 时间戳）+ 按根因族复盘索引 |
| `docs/parallel-dev-收尾/19_wave3_真机补验跟踪表.md` §4/§5 | **缺陷台账 D-01~D-22** 与环境事件 O-1~O-4 的唯一来源；缺陷状态一律以台账为准，勿凭记忆报 |

**漂移门禁**：`python scripts/audit_harness.py all`（多轴：契约双向对拍 / 客户端漂移 / ORM 模型使用率 / 导出-引用可达性 /
文件体积 / 重复模式 / 类成员与跨模块符号；退出码 1＝有 CRITICAL）。**新增或改名页面、增删路由、加模型、加能力函数后各跑一次**
——轴 4/6/7 专查"能力写了但没接线"。

## 3. 🚧 长期外部前置（非代码，冻结清单；**状态不在此记账**）

> 下列项的性质是"**对象不存在就无法验收**"，不是进度状态；进度类阻塞见 `STATE.md`「阻塞」。

1. **凭证**：微信开放平台「移动应用」AppID + 企微**客服 Secret**、企微 `WECHAT_TOKEN` / `WECHAT_ENCODING_AES_KEY`、
   部署前置（M1 服务器 / M4 证书 / M6 DCloud 签名）等。**代码已写到位，填配置即通**（缺时按预期回落绑定码）。
2. **数据**：正式真值数据未采集（B/C/D 真值 + A 批负样本 + 正式文案库）——`docs/真值数据规格标准_v1.md` 是采集手册。
3. **合规 / 上架前置**：ICP 备案（**法定 20 工作日**，需中国大陆节点服务器 ≥3 月 + 域名实名 + 主体一致）、软著、
   企微认证、隐私政策备案号 in-app 展示、算法备案（beian.cac.gov.cn，待核实）。详见工作记忆「上架合规硬约束」。

## 4. 📱 真机 / 环境要点（长期有效）

- nova 11 = `DKS9K23526028855`；统一 system **adb v41**（HBuilderX 内嵌 v36 会互杀 server）；每次 cli launch 后必补 `adb reverse tcp:8000 tcp:8000`；EMUI **纯净模式**拦 install 且零提示——先关再装。
- 编译推包按 `uvue-deploy-device-ops` skill（`deploy_one.sh` 轮询版，reverse 先于启动）。
- 后端测试依赖 **Docker Desktop**（`yishu-redis` / `yishu-qdrant`）——机器重启后要拉起；`docker ps` **挂起**时 `--full` 不可信，如实标注不写成绿。
- **端口口径**：backend `8010` ｜ agent `8300`（`8399` 仅一次性本机覆盖，`8000` 为旧值）。
- 远端 GPU 机（`student03@10.20.28.21`）操作见 `skills/remote-gpu-server-devtest/SKILL.md`：fish shell / CRLF / CUDA 不可见三坑；
  **SetFit 训练必须按本机版本矩阵装环境**（用新版本训出的产物回本机会 `ModuleNotFoundError` ⇒ 分类静默降级 mixed）。
- 本机服务栈状态一律以 `pwsh -File scripts/dev/status.ps1` 为准，**不要**用"端口通"当健康判据（Redis 假活已踩过）。

## 5. 🚀 新会话启动

`AGENTS.md` Startup Workflow → `STATE.md` → 本文件 → `./init.ps1` → `git log --oneline -8`。

## 6. 历史交接存档（一行一届）

- **2026-08-25** ASR/语音会话（FunASR Flash + SenseVoice ONNX 情绪异步两段，状态四态，生产拒 mock）。
- **2026-08-26** 开发 Wave 1→3（pytest 312→420，CI #21 首绿，技术债 P0 八项，Wave 4 J/L/K 落地）。
- **2026-08-27** 收尾 Wave 1（UTS 编译基线修复 + P0-2 搜索性能 + A3/D1 补集成）。
- **2026-08-28** 收尾 Wave 3（真机 7 清单全终态，缺陷 D-01~D-21 + O-1/O-2，4a/4b 收口，终值 ✅46/🟡7/A32）。
- **2026-08-29** harness 台账整饬（决策台账 + lessons 主题索引）；R1 波 fix/4b 八枚 + 暗物质审计 O-3。
- **2026-09-01** 跨窗合流波：wrap1 UI 还原 + 5.24 迁移 17 枚合流 `cea5025`；fix/4b rebase `d09f639`；编译门重启通过。
- **2026-09-02~04** missing-pages 施工窗（W0-W5 画布还原，执行计划 v2 定稿 + 垃圾清理 `00d354a`）。
- **2026-09-11** UTS 插件修复工单（D-18/D-19 原生能力对收口推进）。
- **2026-09-13** 交接提交（`713055f` 推送 origin/develop）：harness/docs/数据/核验证据快照 + client UTS 改动；重写本交接说明。
- **2026-09-21 ~ 10-16（多届，明细见 `progress.md` 速查卡）**：内测下发准备与首版/第二版 APK 出包 → 易扩展重构波（结构棘轮 + 多轴门禁）→ 功能修复波（17 条 P0）→ 接线缺口与性能根因修复（三个头号根因）→ 合并 develop + 产品方向重定（entry 流/回顾位/搜索重做）→ 视觉调用守卫修复。
- **2026-10-24** 本次：SetFit 7 类重训（远端 GPU 机，评估 100%）+ answerability 闸门开启并量化其代价 + 出包半程（云打包已提交、产物未取回）；本文件与 `progress.md` 按职责边界重写/压缩。
