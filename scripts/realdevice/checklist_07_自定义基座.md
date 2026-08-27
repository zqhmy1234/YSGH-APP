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
- 代码侧依赖已声明：`client/uni_modules/yishu-background-tasks/config.json` 声明 androidx.work（自定义基座才生效）。

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
- 执行日期：____ ｜ 设备：nova 11 ｜ 后端：本地/远程（地址____）｜ 档位：mock/真实
- 前置就绪：□ 后端在线 □ 网络（WiFi/蜂窝）□ 账号 □ 相册数据 □ 其他____
- 步骤结果：1)____ 2)____ 3)____ 4)____ 5)____ 6)____ 7)____（每步：通过/失败/备注）
- 证据文件：截图____ 日志____ 其他____
- 总体判定：✅ 通过 / ❌ 失败 / 🟡 部分（降级原因：____）
- 问题描述（失败时）：现象____ 期望____ 后端日志关键行____
```

## 8. 备注 / 降级

- **B5d 遗留定位**：progress.md 08-26「K 遗留待验：自定义基座云打包验证 FGS/WorkManager 真实执行/attribution panel」——本清单即该遗留的执行载体。
- **标准基座 vs 自定义基座**：标准基座下 WorkManager 不可用（降级 pending + setInterval，logcat `WorkManager 不可用`）、权限以基座 manifest 为准；**自定义基座是"真实执行"的前提**，若误用标准基座测 → 判定无效。
- **不虚构纪律**：XView 未落地、attribution 面板系统不支持 → 标"待补"，不作为通过项。
