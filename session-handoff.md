# Session Handoff — 忆述光华

> **性质**：现行交接（2026-08-29 harness 整饬时重写）。历史交接原文在 git 历史（截至于 cbc1751 前），各期详录在 `progress.md` 对应条目——本文件不再滚雪球。目标：接手者 15 分钟上手。

## 1. 现在的位置（2026-08-29）

- **收尾 Wave 1–4 全收口（17/17）**。数字唯一现行口径 = `AGENTS.md`「当前状态」节：用户故事 ✅46/🟡7/❌0 · A 级真机 32 条 · 性能门禁 10/2/6 · 30s ✅（6.0s≤30s）· 功能代码 ~90% · 内测可达度 60–70%。
- **下一步 = 4b 修复批次**（代码工作，非文档）：
  - 批次1 **D-18/D-19** 原生能力对——修复方向见 tracker 19 §4；验收＝重打包后日志 `initBackgroundTasks ok` + `aapt dump manifest` 见 `DataSyncService`（含会话 B FGS 通知复验）；
  - 批次2 **D-16/D-07/D-08** 语音链——验收＝未识别不再伪造"平静"（返 null+UI 不渲染）/ <5min 录音落 COS 可回放转写 / 转写失败段保留 wav+no_speech；
  - 批次3 **S2 情绪校准**（待 C 批真值）+ **D-06** 录音中断机型适配（来电场景补测另需 P6 第二设备）；
  - **散单 D-05/D-10/D-14/D-21（均客户端向）建议并批次2、D-09 遗留/D-12 改进随批**——🔴 待负责人确认排期（`docs/决策台账.md` §1.6）。
- **O-1 环境项优先排查**：uvicorn 搜索嵌入时刻瞬时原生崩溃（两次复发、无 traceback，疑 numpy 2.5.2/torch 漂移——tracker 19 §5）。
- **待用户拍板 6 项**（决策台账 §5）：部署就绪包确认 / 自定义分类目标页是否入 MVP / "取消=不再提示"口径 / D-15 端侧 L1 title_source 语义 / 文案库基调审阅 / **授权订正交付文档 08（line12/21/173）与 02（line119）旧时点数字**。
- **三座大山等团队**（全表 08 §1.4）：凭证（企微三件套/微信 AppID·Secret/短信/uni-push + 内测包 M1 服务器·域名/M4 证书/M6 DCloud 签名）· 数据（B/C/D 真值+A 批负样本+E/F/G 排期+正式文案库；harness 一键就绪）· 合规（企微认证/ICP/软著未提交，5–6 周串行）。

## 2. 工作区红线（当下生效！）

- **他窗口 164 个文件未提交**（29 已暂存，含 backend storage.py+67 行、client、交付文档 03 等）：任何 commit **必须 pathspec 限定**（`git commit -- <自己的路径>`），禁 `git add -A`/reset/stash 触碰他人内容；`test_photo_writes_image_vec` 当前失败即该窗口 storage.py 未落地+COS 未配所致——非基线回归，勿"顺手修"。
- models.py / migrations 冻结；交付文档只读（改须用户授权，先例：08 快照同步 cbc1751）；密钥只走 Infisical；pre-commit 快速门禁不可绕（`--no-verify` 禁用）。
- 基线：develop @ cbc1751（2026-08-28 23:22）。

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
- **2026-08-29 · harness 台账整饬**（本次）：新建决策台账/lessons 主题索引，AGENTS/progress/feature_list/init.sh 数字与口径统一——见 progress.md 当日条目。
