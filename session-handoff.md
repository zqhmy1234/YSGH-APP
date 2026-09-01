# Session Handoff — 忆述光华

> **性质**：现行交接（2026-08-29 harness 整饬时重写）。历史交接原文在 git 历史（截至于 cbc1751 前），各期详录在 `progress.md` 对应条目——本文件不再滚雪球。目标：接手者 15 分钟上手。

## 1. 现在的位置（2026-09-01）

- **收尾 Wave 1–4 全收口（17/17）**。数字唯一现行口径 = `AGENTS.md`「当前状态」节：用户故事 ✅46/🟡7/❌0 · A 级真机 32 条 · 性能门禁 10/2/6 · 30s ✅（6.0s≤30s）· 功能代码 ~90% · 内测可达度 60–70%。
- **下一步 = 4b 修复批次**（代码工作，非文档）：
  - 批次1 **D-18/D-19** 原生能力对——✅ 08-29 代码落地（fix/4b 8cb15b4：标记资产探测+工程根 manifest+.gitignore 豁免）；验收＝R2 重打包后日志 `initBackgroundTasks ok` + `aapt dump manifest` 见 `DataSyncService`（含会话 B FGS 通知复验）；
  - 批次2 **D-16/D-07/D-08(+D-22 建议并入待拍板 §5.7)** 语音链——D-16 ✅后端/端侧已修（fix/4b f1f8a3c，pytest 89 绿；复验随 R2）；验收＝未识别不再伪造"平静"（返 null+UI 不渲染）/ <5min 录音落 COS 可回放转写 / 转写失败段保留 wav+no_speech（D-07/D-08/D-22 客户端半 09-01 随迁移合流解冻，rebase 进行中）；
  - 批次3 **S2 情绪校准**（待 C 批真值）+ **D-06** 录音中断机型适配（来电场景补测另需 P6 第二设备）；
  - **散单 D-05/D-10/D-14/D-21（均客户端向）建议并批次2、D-09 遗留/D-12 改进随批**——✅ 已并批（§1.6）；**09-01 迁移合流（cea5025）全部解冻随 rebase**，D-21 修复已入库待 R2 复验关单。
- **O-1 已定性（P-2 诊断 08-29）**：版本漂移证伪、非原生 AV，疑外部终止/内存压力（取证包+stage2 条件复现清单在报告内）；**O-2 坐实为缺陷→D-22**（并批待拍板 §5.7）。详见 `docs/P2诊断_O1O2_20260829.md`。
- **待用户拍板 3 项**（决策台账 §5）：部署就绪包确认 / 自定义分类目标页是否入 MVP / **AI 对话页后端端点归宿（§5.8，09-01 新登记）**。08-29 午后拍板三连已销账（§1.9）：5.24 迁移归第三窗·D-22 并批·单腿暴露=登录态 /asr/channels。08-29 已连销四项：交付文档同步授权、D-15='device'、取消=永久不再提示、**文案库基调（温和克制+禁区 10 项照单+默认库转正式 v1）**。
- **三座大山等团队**（全表 08 §1.4）：凭证（企微 5 项：前 3 已就绪·08-29 负责人通报，APPID·Secret 待打包/短信/uni-push + 内测包 M1 服务器·域名/M4 证书/M6 DCloud 签名）· 数据（B/C/D 真值+A 批负样本+E/F/G 排期+正式文案库；harness 一键就绪）· 合规（企微认证/ICP/软著未提交，5–6 周串行）。

## 2. 工作区红线（当下生效！）

- **主区旧脏已清空（08-29 拍板③）**：125 项三重备份（快照分支 `salvage/main-worktree-dirty-20260829`@2bad295 + `.cowork-temp/salvage/` patch+文件拷）后 checkout 还原净 develop——**salvage 分支在残余工作认领前禁删**（内含 sms/embedding-dtype/storage/export/orphan_scan 等他窗未提交货）；审计已揪出两单「修了没入库」（D-02/D-03+文案库，fix/4b 补落，tracker O-3）。**第三窗（wrap1）已合流收口 cea5025，但其 worktree 仍在活跃迭代 uvue_gen（不碰）**；**第四窗（媒体票据 Valet Key）在途勿碰名单：主区 8 文件（media.py/media_url.py 新增+events/config/errors/main/schemas content/storage 改动）+HBuilderX hens-svg 插件同步回潮目录（IDE 噪音，不碰不提交）**；任何 commit 仍 pathspec 限定，禁 `git add -A`/reset/stash。
- models.py / migrations 冻结；交付文档只读（改须用户授权，先例：08 快照同步 cbc1751）；密钥只走 Infisical；pre-commit 快速门禁不可绕（`--no-verify` 禁用）。
- 基线：develop @ cea5025+（含 DASHSCOPE merge 5ed7c5c + wrap1 UI/迁移波 cea5025，origin 已同步）；fix/4b @ acce7ef **rebase onto cea5025 进行中**（手术点：utils 三文件改名碰撞 upload_protocol/uploader 热修让位丢弃、voice 守卫重放 .uts、record.uvue 守卫进 RecordSheet 新结构、.gitignore 豁免必须幸存否则 manifest 再被吞、插件双 index.uts 三方合）。编译门条件全绿（HBuilderX/adb 空闲、内存 4GB）。**R2 云包口径：Vapor 已启用**。
- 待拍板 3 项/暗物质铁律（「已修」判定必含 git 入库检查）/salvage 分支禁删——详见台账 §1.11、tracker O-3/O-4。

## 3. 真机 / 环境要点（新窗口必读）

- nova 11 = `DKS9K23526028855`；统一 system adb v41（HBuilderX 内嵌 v36 会互杀 server）；**每次 cli launch 后必补 `adb reverse tcp:8000 tcp:8000`** 再端侧 healthz 探针（reverse 静默丢失一日 11 次）；EMUI **纯净模式**拦 install 且零错误提示——先关再装。
- 真机清单与前置表 P1–P11：`scripts/realdevice/README.md`；清单 04/05 需 `MOCK_EXTERNAL_AI=false` 真实 AI 档；证据三要素铁律（型号+时间/截图日志/判定）缺一写"待补"，不得宣称 A 级。
- 改 client/ 前先读 `skills/hbuilderx-uniappx-runloop/SKILL.md`；真机相册/语音验收先读 `skills/android-media-e2e/SKILL.md`。
- 经验单一来源：`docs/lessons.md`（时间线台账 + 环境陷阱区现至条目 33）+ `docs/lessons-主题索引.md`（10 个根因族速查）。
- dev worker（Windows）：无 scheduler 的 `work()` 入口（RQ spawn 崩）；生产 Linux 全模式；22:00 复盘＝部署侧 cron（`daily_review.py` 幂等）。

## 4. 新会话启动

`AGENTS.md` Startup Workflow（第 3 步起含读 `docs/决策台账.md` 对齐术语）→ 本文件 §1 → `./init.sh` → `git log --oneline -8`。

## 5. 历史交接存档（一行一届，全文见 git / progress.md 同期条目）

- **2026-08-25 · ASR/语音会话**（codex/asr-pipeline-hardening，PR#1 已并入 develop）：FunASR Flash 主通道多格式 + 本地 SenseVoice ONNX 情绪异步两段（转写不被情绪抹掉）+ 状态语义四态（succeeded/no_speech/failed_retryable/failed_final）+ 生产拒 mock。
- **2026-08-26 · 开发 Wave 1→3**（B2/B5b→B3→B4+B5c，pytest 312→341→420）；**CI #21 首次全绿**（7 根因链，lessons 族4/6）；**技术债 P0** 8 项安全修复（502 passed）；**开发 Wave 4 J/L/K**（B5a 消费域 + M3 微信域 code2session/内容安全适配器 + B5d 后台域）与批次决策落地（presign 删/短信 501 冻结/FinetuneJob 删/依赖升版 CVE）。
- **2026-08-27 · 收尾 Wave 1** 十分支集成（UTS 编译基线修复 39734fe 起，lessons 5+0 全保留）+ P0-2 搜索性能修复 + A3/D1 补集成（基线 6aea242）。
- **2026-08-28 · 收尾 Wave 3** 真机 7 清单全终态（01✅/02❌/03✅/04✅/05🟡/06🟡/07🟡，缺陷 D-01~D-21+O-1/O-2 挖出）+ **4a/4b 收口**（08 终版/状态列全收口）+ 补验 US-42/12/25/40/41→A（终值 ✅46/🟡7/A32）。
- **2026-08-29 · harness 台账整饬**：新建决策台账/lessons 主题索引，五本账口径统一；同日本窗完成 R1 波（fix/4b 八枚）+DASHSCOPE 验收 merge+暗物质审计（O-3）。
- **2026-09-01 · 跨窗合流波（本次追账）**：wrap1 UI 还原+5.24 迁移 17 枚合流 cea5025；主账三天空窗由本窗补齐（§1.11/O-4/D-21 状态变化）；媒体票据窗在途登记。
