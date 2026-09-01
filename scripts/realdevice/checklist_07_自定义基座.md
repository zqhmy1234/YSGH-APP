# Checklist 07 · 自定义基座云打包（B5d 遗留）

> 生成：2026-08-28｜归属：scripts/realdevice（Agent D1）｜Wave 3 真机执行
> 对应：报告 §8.1④ + B5d 遗留（progress 08-26「K 遗留待验」：自定义基座云打包验证 FGS/WorkManager 真实执行/attribution panel）
> 相关设计：`忆述光华_交付文档/忆述光华_深度开发设计/05d_后台任务与录音_B5d.md`（§3 attribution tag / §5 前台服务短命化 / WorkManager 队列）
> 相关代码：`client/uni_modules/yishu-background-tasks/`（BgBackground.kt：WorkManager 调度 + attribution tag）、`client/uni_modules/yishu-photo-watch/`（dataSync 前台服务）、`client/uni_modules/yishu-recorder/`（录音）

---

## 1. 目的

**自定义基座云打包**验证：SQLCipher XView 加密库加载、FGS/WorkManager **真实执行**（非标准基座降级）、attribution 面板（B5d 遗留项销项）。

## 2. 前置条件

- **P10 DCloud 账号 + 自定义基座打包权限**（DCloud 开发者中心登录态 + 自定义基座打包权限）；P9 HBuilderX CLI；P3 nova 11 已装/待装自定义基座。
- 代码侧依赖已声明：~~`client/uni_modules/yishu-background-tasks/config.json` 声明 androidx.work~~ **实为 `libs/*.jar` 纯文件机制**（work-runtime-2.9.1 等 13 个 jar，无 config.json——2026-08-28 R7 尸检更正；插件目录亦无任何 AndroidManifest 片段，此为 D-19 根源）。

### 2.1 前置就绪自查（不满足则本清单标"待补"，勿硬跑）

- □ DCloud 账号登录态 + 自定义基座打包权限可用
- □ HBuilderX CLI 可调用；设备在线（`Get-Device`；`KeepAwake`）
- □ 代码侧 SQLCipher XView 模块已配置（见 Step 1 说明——若项目未含 XView 模块，Step 3 该项标"待补"，不虚构）

## 3. 执行步骤

**Step 1 · 配置 SQLCipher XView 模块 → 云打包自定义基座**
- 按 B5d 文档配置：DCloud 开发者中心 → 云打包 → **自定义基座** → 配置 SQLCipher（加密数据库 XView）相关模块 + androidx.work 依赖（config.json 已声明）。
- 执行云打包自定义基座；**记录打包耗时与基座版本号**。
- 采证：打包日志/截图 `Shot 07 1a`（打包配置页）、打包产物路径记录。

**Step 2 · 安装基座 + 运行 App**
- 安装自定义基座到 nova 11（HBuilderX CLI launch 或 adb install）→ 运行 App。
- **预期**：基座安装成功、App 正常启动。
- 采证：`Shot 07 2a`（App 启动/首页）；`GrabLog 07 yishu`。

**Step 3 · SQLCipher 加密库加载**
- **预期**：logcat **无 XView/SQLCipher 初始化错误**；本地库正常读写（创建内容 / 相册游标持久化 / op_log 写入，视代码落地情况）。
- **当前代码状态**：XView（SQLCipher op_log 表）**尚未落地**（`client/utils/event_ops.ts:17`、`sync_client.ts:10` 注释"随自定义基座落地"）——若云打包未带 XView 模块，本地库读写项标 **"待补"**（不虚构 A 级），仅记录加密库加载情况。
- 采证：`GrabLog 07 xview`（关键词 `XView`/`SQLCipher`）；`GrabLog 07 error`（初始化错误扫描）。

**Step 4 · WorkManager 后台任务真实执行**
- 触发后台任务（新照片上传 / 事件聚合 / 云侧拉取——通过相册监听或调试入口拉起）。
- **预期**：logcat 显示 **worker 真实执行记录**（`BgBackground.kt` 日志 `WorkManager 唤醒 taskType=.. tag=..`，非标准基座降级的 `WorkManager 不可用`）。
- 采证：`GrabLog 07 work`（关键词 `WorkManager`/`唤醒`/`taskType`/`tag`）；`Shot 07 3a`（触发入口/执行状态）。

**Step 5 · 前台服务（FGS）录音场景通知常驻**
- 开始录音 → **预期**：microphone 前台服务通知常驻通知栏（自定义基座 + POST_NOTIFICATIONS 生效）；录音期间 dataSync 前台服务让位（互斥，B5d §3）。
- 采证：`Shot 07 4a`（录音中通知栏 FGS 通知截图）；`GrabLog 07 fgs`（关键词 `startForeground`/`前台服务`）。

**Step 6 · attribution 面板可见**
- 打开系统归因/数据访问面板（系统设置 → 隐私 → 敏感数据访问 / 数据访问面板；Android 15+ 扩展归因）。
- **预期**：面板可见，可查麦克风使用标注 / WorkManager 归因（`sync_photo`/`voice_transcribe`/`event_aggregate`/`profile_fetch`）。
- **系统版本注意**：nova 11（FOA-AL00）为 **Android 13 基座（HarmonyOS）**，Android 15/16 扩展归因面板**可能不可见** → 此时该步降级：验证 **logcat 中 attribution tag 接线**（BgBackground.kt 写入 tag），面板可见性标 **"待补（系统不支持）"**，**不虚构**。
- 采证：`Shot 07 5a`（归因/数据访问面板截图，或注明系统无此入口）；`GrabLog 07 attribution`（关键词 `attribution`/`tag`）。

## 4. 预期（可判定口径）

| 项 | 预期 | 判定口径 |
|---|---|---|
| 基座 | 云打包成功 + 安装运行正常 | 打包日志 + 基座版本 + App 启动 |
| 加密库 | SQLCipher/XView 加载无错 | logcat 无 XView 初始化错误（本地库读写视 XView 落地情况） |
| WorkManager | worker 真实执行（非 mock/非降级） | logcat `WorkManager 唤醒 taskType=.. tag=..`，无 `WorkManager 不可用` |
| FGS | 录音场景 FGS 通知常驻 | 通知栏截图 + `startForeground` 日志 |
| attribution | 面板可展示（或系统不支持降级） | 面板截图，或 logcat tag 接线 + 标注系统不支持 |

## 5. 证据清单（证据三要素：①nova 11 + 日期时间 ②截图/日志路径 ③结果判定）

- 截图：`evidence/ck07_1a_<ts>.png`（打包配置）、`ck07_2a_<ts>.png`（App 启动）、`ck07_3a_<ts>.png`（WorkManager 触发）、`ck07_4a_<ts>.png`（FGS 通知）、`ck07_5a_<ts>.png`（attribution 面板）
- 日志：`evidence/ck07_build_<ts>.log`（打包日志）、`ck07_xview_<ts>.log`、`ck07_error_<ts>.log`、`ck07_work_<ts>.log`、`ck07_fgs_<ts>.log`、`ck07_attribution_<ts>.log`、`ck07_yishu_<ts>.log`
- logcat 过滤关键词：`yishu` / `WorkManager` / `XView` / `SQLCipher` / `startForeground` / `attribution` / `tag` / `FATAL`
- 记录：基座版本 __；打包耗时 __；安装耗时 __

## 6. 判定标准

- **✅ 通过**：基座安装成功 + 加密库加载无错 + WorkManager/FGS **真实执行**（日志有执行记录）+ attribution 面板可展示（或注明系统不支持降级）→ **B5d 遗留销项**。
- **❌ 失败**：WorkManager/FGS **执行记录缺失**（附日志定位——真降级还是未触发）；加密库初始化报错。
- **🟡 部分**：XView 模块未含（当前代码状态）→ 本地库读写标"待补"；attribution 面板系统不支持 → 标"待补（系统不支持）"，其余照验，不虚构。

## 7. 记录表

```markdown
## 记录表
- 执行日期：2026-08-28 16:46–17:50 ｜ 设备：nova 11（DKS9K23526028855）｜ 后端：本地 :8000（adb reverse）｜ 档位：真实（DCloud 云打包 + 真机真基座）
- 前置就绪：☑ DCloud 账号（用户完成手机号绑定→manifest「重新获取」真 AppID `__UNI__2650A2A`，替换假占位 `__UNI__YISHU001`）☑ HBuilderX CLI ☑ 设备在线 ☑ 后端+隧道（中途被 HBuilderX adb 互杀一次，重建后双绿）
- 步骤结果：1) 云打包 ✅：`cli pack --iscustom true --android.packagename com.yishu.guanghua --ignoreWarnings true`（布尔必须显式值；包名警告=自动添加测试包名，非阻断）；云编译 18.2s（唯一 CSS 警告 word-break@agg-check:63）→ 排队 149 位/约 9 分钟 → 17:02:09 出包 **23,648,455B，SHA256 1B5683DC…D34DBCA4**，versionName 0.1.0(100) 2) 安装+运行 ✅（一波三折）：**adb install 被 EMUI 纯净模式拦截且错误信息为空**（streamed/版本排查全绕路，真凶=纯净模式）→ 用户关闭后手装成功 @17:05:44 → `cli launch --playground custom --native-log true` **17:43:40 真基座启动**：onLaunch 3511ms、首页渲染 580ms、ensureLogin→true、**sync pull since=0→30 changes（端云链路在自定义基座上直接拉通）** 3) XView/SQLCipher：全日志 0 错误（FATAL/XView/SQLCipher 无命中）；databases/ 仅 DCStorage——**XView 本地库未落地，读写项按 §3 口径标"待补"** 4) WorkManager ❌：判决行仍"标准基座（无 WorkManager），降级"（index.uts:189）——**但 dex 尸检证明 androidx.work∈classes3.dex、BgTaskManager∈classes2.dex=打包成功、能力在包里**；真因=探测代码 `ClassLoader.getResource('androidx/work/WorkManager.class')` 在 Android 恒 null（class 全部编进 dex，无 .class 资源；META-INF 版本文件也被 D8 剥除，探针无一路可用）→ **D-18，正式包亦永不启用 WorkManager** 5) FGS ❌ 不可测：云包 manifest `<service>` 仅 WebSocketService（调试通道），**DataSyncService 无 manifest 注册、FOREGROUND_SERVICE* 权限缺失**（插件目录无 config.json/manifest 片段）→ **D-19**；会话 B 的 FGS 复测计划作废 6) attribution ❌ 待补：上游 WorkManager 永不入队（D-18 堵死），tag 无从验证（Android 13 面板支持另说）
- 证据文件：日志 ck07_pack_20260828.md（时间线全录）+ ck07_runloop_full_20260828.log（启动流全量）+ ck07_runloop_verdict_20260828.log（判决 15 行摘录）；产物 client/unpackage/debug/android_debug.apk（哈希见上）；dex 尸检=findstr classes2/3.dex 命中记录
- 总体判定：🟡 部分（**云打包链路+基座安装启动+端云连通 = A 级成立**；WorkManager/FGS/attribution 三项原生能力 ❌/待补——非打包问题、非基座限制，**是两条产品级代码缺陷 D-18/D-19，正式包同样静默降级**，老人场景=后台同步永不唤醒+前台保活全失效）
- 问题描述：现象 自定义基座日志仍打"降级 pending+setInterval" / 期望 真基座 WorkManager 真实调度 / 定位 index.uts:72-79 探针恒 false（见 D-18）；DataSyncService startService 无注册对象（见 D-19）
```

## 8. 备注 / 降级

- **B5d 遗留定位**：progress.md 08-26「K 遗留待验：自定义基座云打包验证 FGS/WorkManager 真实执行/attribution panel」——本清单即该遗留的执行载体。
- **标准基座 vs 自定义基座**：标准基座下 WorkManager 不可用（降级 pending + setInterval，logcat `WorkManager 不可用`）、权限以基座 manifest 为准；**自定义基座是"真实执行"的前提**，若误用标准基座测 → 判定无效。
- **不虚构纪律**：XView 未落地、attribution 面板系统不支持 → 标"待补"，不作为通过项。
