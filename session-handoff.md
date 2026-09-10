# Session Handoff — 忆述光华

> **性质**：现行交接（2026-09-04 再次整饬重写）。历史交接原文在 git 历史；各期详录在 `progress.md`（已完成项已压缩为速查卡）——本文件不滚雪球。目标：接手者 15 分钟上手。

## 1. 现在的位置（2026-09-01）

- **收尾 Wave 1–4 全收口（17/17）**。数字唯一现行口径 = `AGENTS.md`「当前状态」节：用户故事 ✅46/🟡7/❌0 · A 级真机 32 条 · 性能门禁 10/2/6 · 30s ✅（6.0s≤30s）· 功能代码 ~90% · 内测可达度 60–70%。
- **下一步 = 4b 修复批次**（代码工作，非文档）：
  - 批次1 **D-18/D-19** 原生能力对——✅ 08-29 代码落地（fix/4b 8cb15b4：标记资产探测+工程根 manifest+.gitignore 豁免）；验收＝R2 重打包后日志 `initBackgroundTasks ok` + `aapt dump manifest` 见 `DataSyncService`（含会话 B FGS 通知复验）；
  - 批次2 **D-16/D-07/D-08(+D-22 建议并入待拍板 §5.7)** 语音链——D-16 ✅后端/端侧已修（fix/4b f1f8a3c，pytest 89 绿；复验随 R2）；验收＝未识别不再伪造"平静"（返 null+UI 不渲染）/ <5min 录音落 COS 可回放转写 / 转写失败段保留 wav+no_speech（客户端半 09-01 rebase 已完成，随 R2 实施波落地）；
  - 批次3 **S2 情绪校准**（待 C 批真值）+ **D-06** 录音中断机型适配（来电场景补测另需 P6 第二设备）；
  - **散单 D-05/D-10/D-14/D-21（均客户端向）建议并批次2、D-09 遗留/D-12 改进随批**——✅ 已并批（§1.6）；**09-01 迁移合流+rebase 完成全部解冻**，D-21 修复已入库待 R2 复验关单。
- **O-1 已定性（P-2 诊断 08-29）**：版本漂移证伪、非原生 AV，疑外部终止/内存压力（取证包+stage2 条件复现清单在报告内）；**O-2 坐实为缺陷→D-22**（并批待拍板 §5.7）。详见 `docs/P2诊断_O1O2_20260829.md`。
- **待用户拍板 3 项**（决策台账 §5）：部署就绪包确认 / 自定义分类目标页是否入 MVP / **AI 对话页后端端点归宿（§5.8，09-01 新登记）**。08-29 午后拍板三连已销账（§1.9）：5.24 迁移归第三窗·D-22 并批·单腿暴露=登录态 /asr/channels。08-29 已连销四项：交付文档同步授权、D-15='device'、取消=永久不再提示、**文案库基调（温和克制+禁区 10 项照单+默认库转正式 v1）**。
- **三座大山等团队**（全表 08 §1.4）：凭证（企微 5 项：前 3 已就绪·08-29 负责人通报，APPID·Secret 待打包/短信/uni-push + 内测包 M1 服务器·域名/M4 证书/M6 DCloud 签名）· 数据（B/C/D 真值+A 批负样本+E/F/G 排期+正式文案库；harness 一键就绪）· 合规（企微认证/ICP/软著未提交，5–6 周串行）。

## 2. 工作区红线（当下生效！）

- **主区旧脏已清空（08-29 拍板③）**：125 项三重备份（快照分支 `salvage/main-worktree-dirty-20260829`@2bad295 + `.cowork-temp/salvage/` patch+文件拷）后 checkout 还原净 develop——**salvage 分支在残余工作认领前禁删**（内含 sms/embedding-dtype/storage/export/orphan_scan 等他窗未提交货；回捞：tracked=`git diff 2bad295^1 2bad295`，untracked 原件=`git show 2bad295^3:<路径>`——`.cowork-temp/salvage/` 暂存副本已被清理 Agent 删除勿再引用）；审计已揪出两单「修了没入库」（D-02/D-03+文案库，fix/4b 补落，tracker O-3）。**第三窗（wrap1）已合流收口 cea5025，但其 worktree 仍在活跃迭代 uvue_gen（不碰）**；**第四窗（媒体票据 Valet Key）在途勿碰名单：主区 8 文件（media.py/media_url.py 新增+events/config/errors/main/schemas content/storage 改动）+HBuilderX hens-svg 插件同步回潮目录（IDE 噪音，不碰不提交）**；任何 commit 仍 pathspec 限定，禁 `git add -A`/reset/stash。
- models.py / migrations 冻结；交付文档只读（改须用户授权，先例：08 快照同步 cbc1751）；密钥只走 Infisical；pre-commit 快速门禁不可绕（`--no-verify` 禁用）。
- 基线：develop @ cea5025+记账（DASHSCOPE merge 5ed7c5c + wrap1 UI/迁移波，origin 同步）；fix/4b **已 rebase 至新基线现 @ d09f639**（手术全兑现：热修让位零残留/守卫入 RecordSheet/.gitignore 豁免幸存/contents 双方 auto-heal；备份 fix4b-pre-rebase@acce7ef，R3 后可弃）。**编译门已重启并通过**：受影响后端 9 套件 138 全绿 + 12 页冷编译一次过（09-01 17:26，唯一 WARNING backdrop-filter=画布保留项）。**R2 云包口径：Vapor 已启用；测试依赖 Docker Desktop 在跑（yishu-redis/yishu-qdrant——机器重启后要拉）**。
- 待拍板 3 项/暗物质铁律（「已修」判定必含 git 入库检查）/salvage 分支禁删——详见台账 §1.11、tracker O-3/O-4。
## 1. 现在的位置（2026-09-04）

- **活跃分支 = `feature/missing-pages-impl`**（工作树 `.wt/missing-pages`），客户端「忆述光华」画布还原施工分支。画布真值 = `uvue_gen/*_canvas.json`；差异台账 = `_diff_ledger.md`。
- **执行计划 = `_execution_plan_20260904.md`**（2026-09-04 定稿）：**W0-W8 全部 ⬜ 待批**，建议顺序 **W0→W1→W2→W3/W4→W5/W6→W7→W8**。其 §1 侦察结论是当前「未闭环/半通」事项的权威清单（ai.uvue 整页 mock、trash.uvue 假数据、17 处占位 toast、导出/用户协议未接等）。
- **悬账（W0 回捞，最优先）**：D-16（情绪「平静」伪造，修复 `f1f8a3c`）与 D-22（纠错恒 mixed，修复 `81424fb`）的修复只存在于 `fix4b-pre-rebase` 分支（连同其余 fix/4b R1 单），**均未进 develop/当前分支**；`models.py:66 emotion: str = "平静"` 旧默认值在当前分支 HEAD 复活。「已修待复验」口径对当前分支不成立。
- **mock 开关现状**：index/search 的 `USE_MOCK_*` 已关（false）；ai/trash/storage/favorites 等整页 mock/假数据**等待治理**（W5 mock 治理批），全景见计划 §1.1。
- **2026-09-04 已完成**：工作树垃圾清理——34 个一次性补丁脚本+临时目录（_chk/.wbtmp/__pycache__/.ruff_cache）已删，checkpoint 提交 `00d354a`（执行计划 v2 同时入 version）。
- **里程碑背景（主账口径）**：收尾 Wave 1–4 全收口，用户故事 ✅46/🟡7/❌0 · A 级真机 32 条 · 性能门禁 10/2/6 · 30s ✅（6.0s≤30s）——数字唯一现行口径 = `AGENTS.md`「当前状态」节。4b 修复批次的 R2 云打包/真机复验尚未闭环（依赖 W0 回捞）。
- **待用户拍板（详见决策台账 §5 与计划 §4）**：部署就绪包确认 / 自定义分类目标页是否入 MVP / AI 对话页后端端点归宿（§5.8）/ 主题卡数据源方案 a/b（W7 开工前置）等。
- **三座大山等团队**：凭证（企微 APPID·Secret 待打包 + 短信/uni-push + M1 服务器·M4 证书·M6 DCloud 签名）· 数据（B/C/D 真值+A 批负样本+E/F/G 排期+正式文案库）· 合规（企微认证/ICP/软著未提交）。

## 2. 工作区红线（当下生效！）

- **施工窗纪律**：只动 `feature/missing-pages-impl`（工作树 `.wt/missing-pages`）；任何 commit 仍 pathspec 限定，禁 `git add -A`/reset/stash；同文件多刀禁并行（§V 吞刀事故后铁律：提交前逐刀 grep 验刀）。
- 交付文档只读（改须用户授权）；密钥只走 Infisical；pre-commit 快速门禁不可绕（`--no-verify` 禁用）。
- **R2 测试依赖 Docker Desktop 在跑**（yishu-redis/yishu-qdrant——机器重启后要拉）。
- **勿碰名单**：salvage 分支 `salvage/main-worktree-dirty-20260829`@2bad295 残余工作认领前禁删；uvue_gen 画布真值与 `_diff_ledger.md`/`_execution_plan_20260904.md` 为本窗权威文件。
- 历史军规沿用：暗物质铁律（「已修」判定必含 git 入库检查——本窗 D-16/D-22 悬账即活例）。

## 3. 真机 / 环境要点（新窗口必读）

- nova 11 = `DKS9K23526028855`；统一 system adb v41（HBuilderX 内嵌 v36 会互杀 server）；**每次 cli launch 后必补 `adb reverse tcp:8000 tcp:8000`** 再端侧 healthz 探针（reverse 静默丢失一日 11 次）；EMUI **纯净模式**拦 install 且零错误提示——先关再装。
- 真机清单与前置表 P1–P11：`scripts/realdevice/README.md`；清单 04/05 需 `MOCK_EXTERNAL_AI=false` 真实 AI 档；证据三要素铁律（型号+时间/截图日志/判定）缺一写"待补"，不得宣称 A 级。
- 改 client/ 前先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`；真机相册/语音验收先读 `skills/android-media-e2e/SKILL.md`；编译推包/上机部署按 `uvue-deploy-device-ops` skill（deploy_one.sh 轮询版，reverse 先于启动）。
- 经验单一来源：`docs/lessons.md`（时间线台账 + 环境陷阱区）+ `docs/lessons-主题索引.md`（根因族速查；新增族11 工具自毁/族36 沙箱 ref）。
- dev worker（Windows）：无 scheduler 的 `work()` 入口（RQ spawn 崩）；生产 Linux 全模式；22:00 复盘＝部署侧 cron（`daily_review.py` 幂等）。

## 4. 新会话启动

`AGENTS.md` Startup Workflow（第 3 步起含读 `docs/决策台账.md` 对齐术语）→ 本文件 §1 → 读 `_execution_plan_20260904.md` + `_diff_ledger.md` → `./init.sh` → `git log --oneline -8`。

## 5. 历史交接存档（一行一届，全文见 git / progress.md 同期条目）

- **2026-08-25 · ASR/语音会话**（codex/asr-pipeline-hardening，PR#1 已并入 develop）：FunASR Flash 主通道多格式 + 本地 SenseVoice ONNX 情绪异步两段（转写不被情绪抹掉）+ 状态语义四态（succeeded/no_speech/failed_retryable/failed_final）+ 生产拒 mock。
- **2026-08-26 · 开发 Wave 1→3**（B2/B5b→B3→B4+B5c，pytest 312→341→420）；**CI #21 首次全绿**（7 根因链，lessons 族4/6）；**技术债 P0** 8 项安全修复（502 passed）；**开发 Wave 4 J/L/K**（B5a 消费域 + M3 微信域 code2session/内容安全适配器 + B5d 后台域）与批次决策落地（presign 删/短信 501 冻结/FinetuneJob 删/依赖升版 CVE）。
- **2026-08-27 · 收尾 Wave 1** 十分支集成（UTS 编译基线修复 39734fe 起，lessons 5+0 全保留）+ P0-2 搜索性能修复 + A3/D1 补集成（基线 6aea242）。
- **2026-08-28 · 收尾 Wave 3** 真机 7 清单全终态（01✅/02❌/03✅/04✅/05🟡/06🟡/07🟡，缺陷 D-01~D-21+O-1/O-2 挖出）+ **4a/4b 收口**（08 终版/状态列全收口）+ 补验 US-42/12/25/40/41→A（终值 ✅46/🟡7/A32）。
- **2026-08-29 · harness 台账整饬**：新建决策台账/lessons 主题索引，五本账口径统一；同日本窗完成 R1 波（fix/4b 八枚）+DASHSCOPE 验收 merge+暗物质审计（O-3）。
- **2026-09-01 · 跨窗合流波（本次追账）**：wrap1 UI 还原+5.24 迁移 17 枚合流 cea5025；主账三天空窗由本窗补齐（§1.11/O-4/D-21 状态变化）；媒体票据窗在途登记。
- **2026-08-25 · ASR/语音会话**（codex/asr-pipeline-hardening，PR#1 已并入 develop）：FunASR Flash 主通道多格式 + 本地 SenseVoice ONNX 情绪异步两段 + 状态语义四态 + 生产拒 mock。
- **2026-08-26 · 开发 Wave 1→3**（B2/B5b→B3→B4+B5c，pytest 312→341→420）；**CI #21 首次全绿**；**技术债 P0** 8 项安全修复；开发 Wave 4 J/L/K 落地。
- **2026-08-27 · 收尾 Wave 1** 十分支集成（UTS 编译基线修复 39734fe 起）+ P0-2 搜索性能修复 + A3/D1 补集成（基线 6aea242）。
- **2026-08-28 · 收尾 Wave 3** 真机 7 清单全终态（缺陷 D-01~D-21+O-1/O-2 挖出）+ **4a/4b 收口** + 补验 → 终值 ✅46/🟡7/A32。
- **2026-08-29 · harness 台账整饬**：新建决策台账/lessons 主题索引，五本账口径统一；同日 R1 波（fix/4b 八枚）+DASHSCOPE 验收 merge+暗物质审计（O-3）。
- **2026-09-01 · 跨窗合流波**：wrap1 UI 还原+5.24 迁移 17 枚合流 cea5025；fix/4b rebase d09f639；W10 峰宝九条全落地。
- **2026-09-02~04 · missing-pages 施工窗**：W0-W5 画布还原波次推进（存储/回收站/隐私政策/RecordSheet/EchoSheet/详情域等），详见 `_diff_ledger.md` 与 git log；09-04 执行计划 v2 定稿+垃圾清理 00d354a。
