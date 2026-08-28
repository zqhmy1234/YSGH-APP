# Wave 3 · 真机补验执行跟踪表——docs/parallel-dev-收尾/19
> **状态**：✅ 已执行（2026-08-27 深夜前置核查与跟踪表就绪；清单执行判定由 Wave 3 协调者登记）

> 维护：协调者（主窗口）｜执行：用户（nova 11 FOA-AL00）
> 依据：SOP v2（2026-08-27 22:58）+《00_总纲_收尾.md》波次表 Wave 3 +《12_wave1_agentD1》7 清单
> 目标：把 07 报告"待真机实测"项升级为 A 级证据。**铁律：无 nova 11 实测记录不得宣称真机打通；证据三要素（型号+时间 / 截图日志 / 判定）缺一 = 该项不完整。**
> 基线：develop @ 6aea242（A3/D1 补集成后）。执行顺序：01→02→03→04→05→06→07（SOP v2 §3.1）。

---

## 1. 前置就绪总表（协调者核查记录）

| # | 前置 | 状态 | 核查证据（2026-08-27 深夜） |
|---|---|---|---|
| P1 | 本地后端在线 + adb reverse | ✅ | healthz 200 `{"status":"ok"}`（uvicorn pid 9744）；`adb -s DKS9K23526028855 reverse tcp:8000 tcp:8000` 已建立（UsbFfs）；config.ts REAL_DEVICE_HOST=localhost 口径一致 |
| P2 | nova 11 在线 + 常亮 | ✅ | `adb devices` → `DKS9K23526028855 device`（FOA-AL00 / HUAWEI / SDK 31）；`svc power stayon usb` 已设；设备时钟 23:38 与主机同步 |
| P3 | DASHSCOPE 真实档 | ✅ | backend/.env：MOCK_EXTERNAL_AI=false + DASHSCOPE_API_KEY 在位；STORAGE_BACKEND=fs |
| P4 | 蜂窝 SIM 可开关 | ⏳ 用户 | 清单 01 开工前用户现场切换验证 |
| P5 | 相册素材 | 部分 ✅ | 生成测试照 100 张含 EXIF 真值在 `.cowork-temp/test_photos`（08-22×40 / 08-23×30 / 08-24×30，每张 15KB）——够 01/02/03/06；**04 需用户真实跨日照片 15–20 张（语义归并用真实素材）；05 需用户 3 段 30–60s 真实录音**；03 用 60 张生成照 + 可选真实相册对照轮 |
| P6 | 通话模拟（02） | ⏳ 用户 | 第二设备呼入；不可用则闹钟/媒体抢占触发 onInterruptionBegin（README §5 备选） |
| P7 | DCloud 账号（07） | ⏳ 用户 | 07 排最后，届时确认登录态与云打包权限 |
| P8 | 测试账号隔离 | ✅ 基本干净 | DB 核对（psycopg3 只读）：各 mock-unionid 账号 contents≈0（仅 1 条残留）；真机 ensureLogin 走 `mock-unionid-{req.code}`（providers.py:353）——**03/04/05 开跑前按当次账号现值再清一次** |
| P9 | HBuilderX CLI | ✅（有一场小战） | cli.exe 在位；发现并清理 23:17 滞留 `cli launch --compile` + 残留 java（即 lessons 记录"编译秒退根因"）；adb v36(HBuilderX)/v41(platform-tools) 互杀 server 已恢复 |

## 2. 7 清单状态表（执行后逐项登记）

| 序 | 清单 | 对应 US/门禁 | 判定 | 档位 | 证据路径 | 登记（progress/feature_list） |
|---|---|---|---|---|---|---|
| 1 | 01 蜂窝链路+同步横幅 | US-48/46/47 | ✅ **通过**（Step1-4 全过：WiFi 10 张全链 done+EXIF 真值 / 蜂窝 20 张零上传 held / 手动 drain +20 幂等 / 断网 1 张 WiFi 钩子零点击补传，终态 34/34 done） | mock 可 | `evidence/ck01_step1_20260828_010928.log` + `ck01_step2_20260828_021530.log` + `ck01_step34_20260828_025313.log` + 截图×6 | ✅ 00:5x-02:5x 完成，登记见 progress+feature_list；缺陷 D-01~D-05 |
| 2 | 02 录音中断恢复 | US-20/52 | ❌ **失败**（D-06：闹钟/相机两路抢麦均不触发 onInterruptionBegin——闹钟场录音 284s 无断点[§2 前提证伪]；相机场抢麦窗 wav 纯零+UI 无提示+另一场合同操作硬杀弃档[非确定性]；恢复系系统自动回麦兜底。Step5 ✅短参验证过[60.04s 自动停+段落盘，调试参数缩短验证]；真实来电场景待补[P6]；新增 D-08 转写失败即弃段/D-09 sensevoice 兜底坏死） | mock 可 | `evidence/ck02_int_20260828_043300.log` + `ck02_waveform_1787862362332_20260828.txt` + `ck02_grab_trigger_20260828.log`（Step5 补测见 int 日志附录）+ 截图×2 | 待登记（progress/feature_list 波尾）；缺陷 D-06/D-07 |
| 3 | 03 首批 30s 计时 | §7.1 🟡→✅ | ✅ 完成（08-28 凌晨 R2 轮）| 6.0s ≤30s：T0=scan_file 扳机 07:20:11.06 → T1=首批日卡片渲染 07:20:17.05（runloop attachPhotos 双证）；50 张 13 批全量 61.2s（注入扳机串行占 87%，管线段 0.6-1.2s/批全 accepted）| evidence/ck03_R2_T1_20260828_072114.png + ck03_timing_R2_20260828.log；R1 轮环境发现：adb push 不自动索引/延迟生行不通知 → 扳机必须逐文件 scan_file | ⚠️转04×3：卡面日期=入库日非EXIF（D-12 候选）；13 L1 非按天3；「1张照片」文案 vs 批4张 |
| 4 | 04 L2/L3 云侧归并 | US-06/07 B→A | ✅ 完成（08-28 R2/R4/R5b 三轮） | L2 两独立轮**真实 qwen**：「抽象圆点拍摄集」/「抽象圆点实验」（标题变=非确定性铁证），llm+cloud-llm+confirmed+0.85，窗口=真值 08-22 08:00→08-24 10:56，成员 50/50 无缺并无错并；触发 run_user_aggregation(mode=l2l3) 0.596s | evidence/ck04_backend_worker + ck04_watch_R4 + ck04_runloop_R5b（.log）+ ck04_UI_0805/0806.png | ⚠️L2 卡片 UI 未见→4a 复验；L3 数据不满足→待补；新单 D-14/D-15，D-04/D-12/D-13 关单 |
| 5 | 05 转写/情绪双通道 | US-17/18/19 B→A | 🟡 部分（08-28 真录4条 + 环境修复后回放补证） | **转写 A 级**：百炼 FunASR 真通道 4/4 语义命中（"全班第三→第3"、"写点→洗点"同音错=真实识别非回显铁证）；**情绪真实**：SenseVoice ONNX 装好后对真机原录音回放，emotion_source=sensevoice_local / mock=false，S1平静 0.830 ✓ / S3低落→难过 0.875 ✓ / S2开心漏报(平静 0.496 低于 0.7 阈值) 待 C 批校准；guardrail passed=true 真通道返回；意外伴生：确认单截图自动入库+VL 全命中（D-06 再+1 证） | evidence/ck05_backend_20260828.md + ck05_final_20260828.log + ck05_emotion_replay_20260828.jsonl + ck05_watch_20260828.log + ck05_item_1453*.png + ck05_UI_emotion_live_20260828.png + ck05_emotion_live_replay_20260828.jsonl + w3_wavs(原录音留档) | ⚠️新单 D-16（三层默认值伪造"平静"）；短录音全判死=D-07 主犯本轮 4 条复现实锤（D-09 依赖侧已修）；**16:49 实况复录通过：UI 直显「难过」（conf 回放 0.840/sensevoice_local，DB bf9c9330；该条仍 failed=D-07 第五次复现）**；情绪展示项升实况 A 级 |
| 6 | 06 HBuilderX 编译冒烟 | §2.2 遗留① | 🟡 完成（08-28 随 03/04 会话 4 轮编译） | HBuilderX 5.15：全量 116184ms/62s/43.6s、缓存 27.8s；四度编译成功+装包启动 onLaunch 5.3s；四 tab 真实渲染+访谈/消息入口级；链路③相册→卡片强证；FATAL logcat 2万行 0 命中 | evidence/ck06_build_launch_20260828.log | ⚠️链路①②adb 中文 IME 注入不可（工装限制非缺陷）→ 并入 4a 人工复验 |
| 7 | 07 云打包真基座 | B5d 遗留三件套 | 🟡 链路 A 级+原生能力双缺陷（08-28 R7） | **云打包链路全通**：真 AppID `__UNI__2650A2A`（手机号绑定+重新获取）→pack 18.2s 编译+9min 排队→APK 23.6MB/SHA256 存证→纯净模式拦 adb install（环境，用户手装✓）→`--playground custom` 真基座 17:43:40 启动（onLaunch 3511ms+**sync pull 30 changes 端云直连**）；**但 WorkManager 判决仍"降级"=D-18 探测恒 false（dex 尸检：类全在包里）；FGS=D-19 manifest 从未注册；attribution 上游堵死待补** | evidence/ck07_pack_20260828.md + ck07_runloop_full/verdict_20260828.log | ⚠️新单 D-18（探针恒 false·正式包后台永不唤醒）/D-19（FGS 全基座必死）；会话 B·FGS 项作废；XView 未落地=待补（非失败） |

> 判定写法：✅ / ❌ / 🟡 部分（降级原因）/ 待补（缺前置项）。

## 3. 协调者操作位（每次真机开跑前）

1. **装最新包**：等无并行重进程（本表记录窗口）→ 杀残留 `java/cli` → `& D:\HBuilderX\cli.exe launch app-android --project "D:\GuangH-App\client" --deviceId "DKS9K23526028855"`（成功标志"应用【client】已启动"）→ adb server 互杀后补 `adb reverse tcp:8000 tcp:8000`。
2. 照片注入（01/03/06 用）：新目录 `yishu_testN`（N 每次 +1，游标教训）→ push → 逐文件 scan_file。
3. 每清单开始前 `Clear-Logcat`；证据用 `Shot/GrabLog/Record`（adb_helpers.ps1）。
4. 后端日志真实档佐证（04/05）：uvicorn 控制台日志 grep `qwen|funasr|sensevoice`（非 mock 标记）。

## 4. 缺陷单登记

> 模板见 SOP §4。

| 编号 | 清单 | 归属 | 处置 |
|---|---|---|---|
| D-01 | 01 Step1 | 客户端 upload_protocol | ✅ 已修+真机复验通过（01:09 十张零 4xx）：initUpload 单点 sanitizeKeyId（白名单外字符→_，>255 截尾）。**Wave2 交接"422=旧测试数据"系误诊**——R4#12 白名单收紧未同步客户端构造点，影响所有照片+语音上传 |
| D-02 | 01 Step1 管线段 | 服务端 9f0b2f4 回归 | ✅ 已修：8 个 enqueue_unique 调用点丢任务实参（key≠args）→ contents 永远 processing。修复后 10/10 done + 聚合 0.03s。教训已登记（mock 签名宽容 *args） |
| D-03 | 01 Step1 管线段 | 服务端 双路径不对称 | ✅ 已修：EXIF 权威解析只挂 multipart 路径，分片上传路径缺 → taken_at 污染成扫描时间。下沉 `services/exif.py`（子 IFD 0x8769 优先——华为真照片布局）+ 管线 `_process_photo` 单点回填；回归测试 `test_photo_exif_backfills_taken_at` |
| D-04 | 01 观察 | 端云日期语义 | 🔒 关单定性（04 终局 08-28）：L1 日期失真源头=测试生成器曾把 DateTimeOriginal 写 IFD0（Android 只读子 IFD→null→静默回退 DATE_ADDED），生成器已修（bc855a0）；w3k 合法 EXIF 实测 L1 三日精确命中（accepted=2/1、days=3、DB start/end=真值）→ **端侧链路无罪**。保留改进：服务端对 start_time≈date_added 的 L1 回填后回头修正（合并 D-12 处置） |
| D-05 | 01 Step2 | 客户端（横幅接线+队列） | 📋 待 Wave4 修，不阻塞：a) 横幅 emits 无宿主监听 → held 手动上传成功后不补端侧聚合/事件上云（contents 有、事件无）；b) drain 耗尽路径 held/failed 双登记无去重（计数虚增）。修复设计见 `evidence/ck01_step2_20260828_021530.log` 尾注 |
| D-06 | 02 Step2/3 | 客户端 yishu-recorder(UTS)+uni 运行时 | 📋 失败单移交 Wave4：真机麦克风被抢占时 uni RecorderManager 无感（无 interruption/error 回调、无 UI 提示）——相机录像抢麦窗 wav 内为数字零（**静默吞音**，用户以为在录）；另场次同操作直接 onError 硬杀+文件截断（**非确定性**）。闹钟场景证伪清单 §2 前提（GAIN_TRANSIENT 实测录音无感）。来电路径待 P6 补测。修复方向：AudioRecord.getInterruption/activeRecording 轮询自检 + 零能量看门狗 + 抢占窗在转写/入库显式标注，禁止静默 |
| D-07 | 02 Step4 连带 | 客户端短录音入库路径+服务端管线 | 📋 移交 Wave4：<5min 短录音"确认入库"只提交转写文本、音频本地丢弃（voice.ts 仅 >5min 上传），但 process_content 对 voice 一律要求音频对象 → worker 实录 `ASR 失败: AUDIO_NOT_FOUND` → contents 标 failed（用户感知"转写失败"主因，短录音永久不可回放）。**05 R6 复现实锤（08-28 14:46）：用户 4 条真录音全部中招 failed 仅 text 幸存；设备缓存抽回原 wav 做情绪回放（evidence/ck05_emotion_replay_20260828.jsonl）**。叠加：同步转写 30s 硬超时（voice.ts:160），实测 76.9s 音频冷态 24.3s/热态 3.7s——>90s 冷态必超时。方向：短录音带音频入库或管线放行无音频 voice；转写异步化或超时随时长伸缩 |
| D-08 | 02 Step5 补测连带 | 客户端转写失败处理 | 📋 移交 Wave4：自动停止/手动停止后 transcribeWav 失败（HTTP 非 200/超时）仅 toast 一次即重置空闲——**整段录音成孤儿**（无重试按钮、不落持久队列、UI 不留存 lastVoiceFile）。实例：04:54 的 60s 段（503），PC 直传回放可救（3.7s 出文）但端内无任何找回路径。方向：失败段入本地待发队列（同离线队列机制）+ 卡片保留"重试转写" |
| D-09 | 02 Step5 补测连带 | 后端 ASR 双通道 | 📋 移交 Wave4（清单 05 前哨发现）：sensevoice 兜底通道 **ModuleNotFoundError**（uvicorn 实录×6），双通道实际单腿——funasr 通道网络抖动即整端点 503（04:54:46 实例）。funasr 主通道自身工作正常（当晚 200×6，热态 3.7s/冷态 24.3s）。方向：修 sensevoice 依赖安装或从降级链摘除并在健康检查暴露单腿状态。**08-28 15:40 依赖侧已修**：numpy2.5.2/scipy1.18/librosa/funasr-onnx/onnxruntime/modelscope 全链装齐，SenseVoice ONNX 经 ModelScope 首调下载（D 盘缓存），回放实证 emotion_source=sensevoice_local 双腿接通；遗留"健康检查暴露单腿"并入 D-16 处置 |
| D-10 | 03 环境段 | 客户端 utils/auth.ts:61,66 | 📋 Wave4 修（环境诱发实证）：`res.data as UTSJSONObject` 无守卫+未校验 statusCode/content-type——8000 端口被 HBuilderX httpServer 占用返回非 JSON 时直接 ClassCastException 崩环（06:56-07:08 实录多次）。另 auth.ts:61 TEMP code=dev-client-w3e 波尾须还原。方向：200+content-type 校验后 cast，否则可读错误"后端不可达" |
| D-11 | 03 R1/R3 | 环境（EMUI MediaProvider）非 App | 🧾 环境单：adb push 秒级生行（date_added=推送时刻）但 notifyChange 不达 App ContentObserver（R1 found 0@+5s 竞态、R3 push 后 45s 零唤醒，两轮独立复现）→ 真机导入扳机必须逐文件 scan_file（≈1.17s/文件）。已沉淀 skill android-media-e2e §2 |
| D-12 | 03→04 | 客户端 yishu-photo-watch | 🔒 关单+改进项：定性见 D-04（端侧无罪，w3k 实证）。保留观测性改进：readExifTaken dt==null 分支加告警日志（现静默回退链 EXIF→DATE_TAKEN→DATE_ADDED 全盲），Wave4 落实 |
| D-13 | 03 R2 | 测试数据 artifact 非缺陷 | 🔒 关单："4 张→1 L1·1条"成员塌缩=同秒 DATE_ADDED 时间戳被 preprocess 去重并 1（D-12 级联）；w3k 每日 2 张→「·2条」不复发；真实相机照片秒级差异不受影响 |
| D-14 | 04 R5 | 客户端 photo-watch 上传路径 | 📋 移交 Wave4（真缺陷·照片静默丢失）：隧道死时 uploadBatch 失败仅 toast——**游标已推进不回退、contents 无补传队列**（w3j 6 张永久丢失 @07:56；同窗 R4 50 张却经 pending drain 恢复，两路径行为不一致）。另：drain 重传 contents 跳过 handleBatch 端侧聚合（R4 轮 L1 长期为空）。方向：上传成功才推进游标 + 照片上传并入持久队列，重传后补端侧聚合 |
| D-15 | 04 R5b | 端云事件语义 | 📋 产品语义待拍板：端侧自动生成 L1 落库 title_source='user'/generated_by='device'——自动事件标 user 源，未来 confirmed 会误触 B3-5 背书保护（aggregate.py:184）。方向：device 自动→title_source='device' 或 'template' |
| D-16 | 05 R6 | 后端 ASR 情绪通道+客户端展示 | 📋 移交 Wave4（真缺陷·伪造标签）：SenseVoice 增强失败（ModuleNotFoundError/模型缺失）时 transcriber.py:80 仅记 warning 并返回未增强结果，emotion 保持 models.py:66 + schemas/asr.py:21 双默认值"平静"、客户端 voice.ts:186 再 `?? '平静'` 兜底——**三层默认值把"没测出"联同伪装成"平静"，UI 无法区分**。老人情绪关怀场景高危：低落被标平静→notify.py:291 门控永不触发。方向：emotion_source=none 时 API 返回 null/「未识别」，UI source=none 不渲染情绪 chip |
| D-18 | 07 R7 | client/uni_modules/yishu-background-tasks index.uts:72-79 | 📋 移交 Wave4（高危·后台调度永久静默失效）：`workManagerRuntimePresent()` 用 `ClassLoader.getResource('androidx/work/WorkManager.class')` 探测——Android class 全编进 dex，`.class` 资源永不存在→**恒 false**（云包 dex 尸检：work∈classes3、BgTaskManager∈classes2=打包成功仍判降级）。**正式包同样永不启用 WorkManager**，B5d 唤醒/周期/退避全废，仅靠 setInterval 假活。方向：改探测插件自带 marker asset（assets 对 getResource 有效且无异常），或验证 UTS `catch (e: any)` 兜 CNFE 后改 Class.forName；验收=重打包日志 `initBackgroundTasks ok` |
| D-19 | 07 R7 | client/uni_modules/yishu-photo-watch（DataSyncService :255/:405） | 📋 移交 Wave4（高危·FGS 保活全基座必死）：服务仅 UTS 源码定义，**无 manifest 注册**（插件无 config.json/manifest 片段——依赖走 libs/*.jar 纯文件；云包 manifest `<service>` 实测只有 WebSocketService），且 FOREGROUND_SERVICE* 权限缺失→startService 无对象可起；源码注释"标准基座自动回退"掩盖了**自定义基座/正式包同样不生效**的真相（老人场景=录音/同步无保活，省电查杀裸奔）。方向：UTS 插件 config.json 补 services 声明+manifest 权限，随 D-18 一并重打包复验（含会话 B FGS 通知项） |

## 5. 环境事件记录（本波内）

- 23:17 滞留 cli/java（另一窗口 compile 会话残留）→ 23:4x 协调者按 lessons 清理，nova 从 offline 恢复。
- 23:28 起另一窗口 pytest 全量门禁（pid 28148）在跑，物理内存一度剩 1.7GB → **真机开跑前确认其结束**（编译需 ~5GB 余量）。
- adb 双版本 server 互杀：platform-tools v41 与 HBuilderX 内嵌 v36；表现为 `adb server version doesn't match; killing...` + 设备瞬失。处置：kill-server 重建 + 设备重插线自愈；每次 cli launch 后协调者补 reverse。
- 手机前台为用户游戏（皇室战争）→ 协调者不动屏，等用户就绪信号。
- **00:47 基座会话断裂链**：D-01 补丁差量编译后"调试基座已退出"→ 会话无法重挂 → `pm clear io.dcloud.uniappx` 重置基线（旧队列含用户私人照片 failed 项/过期 token/暂停态，一并清除；首次授权流为清单 03 所需）→ 首启弹"uni-app x 无响应"ANR 框（用户手动关闭，成功）→ 但 pm clear 杀掉旧会话 → 重跑 cli launch（pwsh-8）挂回，ensureLogin true。
- **隧道随会话重启失效**：launch 循环 adb server → reverse 丢失 → 会话日志 `ensureLogin -> false` + `sync pull 前登录失败`（00:58）。处置：每次 launch 后固定补 `adb reverse tcp:8000 tcp:8000` + 端侧 curl healthz 探针。
- **01:02 MediaStore 行复用陷阱（新变体）**：`yishu_test2` 目录名 08-24 用过 → 同路径 scan_file 复用旧行 id（189198-189207 < 游标 189722）→ 观察器永 `found 0`。"换目录名"不够，须**全局从未用过**——本波起用 `yishu_w3a/w3b/w3c…` 前缀。
- **[yishu] 证据通道修正**：App console.log 只进 HBuilderX 会话 stdout，**不进 logcat**（GrabLog yishu 0 命中实证）→ SOP 的 GrabAppLog 流程对本波废弃，改"会话日志落盘 evidence/*_session_*.log"。
- **RQ worker 缺失 + Windows embeddable 双坑**：本波发现 worker 进程从未在跑（D-02 因此静默一天）。启动链路：LobsterAI python 为 embeddable 发行（python311._pth → **忽略 PYTHONPATH 与 cwd**）→ `python -m app.workers.worker` 必炸 ModuleNotFoundError；uvicorn 能用是因 CLI 自己注入 cwd。正解 `-c "sys.path.insert(0, r'D:\GuangH-App\backend'); ..."` 引导。第二坑：`worker.work(with_scheduler=True)` 在 Windows spawn scheduler 子进程时 `TypeError: cannot pickle '_thread.lock'`（worker.py A2 内嵌 scheduler 模式在 Windows 不可用）→ dev 以无 scheduler 的 `work()` 消费（代价：RQ Retry 延迟任务不回投——登记部署清单，生产 Linux 无此问题）。
- WiFi 被手机自动切回蜂窝两次（00:34 前 / 01:07）：`adb shell svc wifi enable` 恢复；6021-5G 信号 RSSI -29。
- **O-1（观察项，非缺陷）**：18:0x 某次 uvicorn 在 BGE-M3 懒加载时刻无 traceback 直接 exit 1（日志末尾 `torch_dtype deprecated`+`tokenize→preprocess` 指纹），PC 端两次 curl 搜索复现均正常、latency 1.4-3.5s——判定瞬时原生崩溃，非可复现代码缺陷；疑与当日 numpy 2.5.2 升级后的版本漂移相关，4b 若再遇同类崩溃优先复查 numpy/scipy 与 torch/sentence-transformers 兼容性。
- **全量门禁状态（4a 收口时）**：syntax/lint/todos/api_smoke/research/cleanup 全绿；secrets 3 条误报（uiautomator XML 的 password 属性为 false）已删 XML 消除；pytest 723 过 + 3 挂→2 个为我方环境因素（geo_cache 残留真调用缓存行 + 我误设 QDRANT_COLLECTION）已修转绿，剩 1 个 test_photo_writes_image_vec 失败源于**另一窗口未提交的 storage.py（+67 行）** + COS 未配置——非已提交基线回归，待该窗口落地/配 COS 后应转绿。

## 6. 遗留

- `docs/parallel-dev-收尾/` 入库由 Wave 4 收口拍板（本波跟踪表 19 工作区持续更新；progress/feature_list 的真机 A 级登记待 7 清单全部终判后统一写入，避免与 4a 并行冲突）。
- ~~Wave 2 交接口径"旧队列照片 upload/init 422（旧测试数据）"~~ → **已证伪并闭环**：即 D-01（全量上传契约漂移），§4 缺陷单。
- dev worker 无 scheduler（Windows 坑，§5）：RQ Retry 退避不回投 + requeue_job 无周期触发 → 转 Wave 4b/部署就绪包条目（生产 Linux 以 `python -m app.workers.worker` 完整模式跑）。
- D-04（端云日期语义，§4）待清单 04 真实素材下定性。
- 手机相册里 yishu_test1/2、yishu_w3a 系列测试图与 MediaStore 行 wave 结束后统一清理（含孤儿行 content delete）。
