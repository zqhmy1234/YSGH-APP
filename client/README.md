# 忆述光华 · 客户端（uni-app x）

> **2026-09-23 维护注**：本文原为「第一波（W3-4）」时期的文档，其中目录结构/配置说明已严重过期（曾写「单页：时间轴」、`pages/index/index.uvue`、`.ts` 后缀、端口 8000、Kotlin Hybrid Mode）。
> 本次按代码实况修正**目录结构与配置段**；下文的「第一波验收」表与「已知边界」**保留为历史记录**，勿当作现状。
> 客户端的权威状态见 `../AGENTS.md`、`../docs/决策台账.md`；开发主循环见 `../skills/hbuilderx-uniappx-runloop/SKILL.md`（**改 client/ 前先读**）。

第一波（W3-4）交付：F1 数据链路（相册→上传→云侧聚合→回读）+ F8 时间轴。

## 环境要求

- HBuilderX（已装 `D:\HBuilderX`，用 HBuilderX 打开本目录；CLI 用法见 skill）
- JDK17 / Android SDK / adb（已就绪）
- 真机 nova 11（USB 调试需在手机上**允许授权**；`adb devices` 显示 `unauthorized` 时无法部署）

## 目录结构（2026-09-23 按代码实况核对）

```
client/
├── manifest.json                 # App 配置（权限 5 条，含 RECORD_AUDIO；icons/splashScreens 已配）
├── AndroidManifest.xml           # 云打包唯一 manifest 合并点（FGS service + 明文放行）
├── pages.json                    # 页面注册（16 页；主入口 pages/shell/shell）
├── main.uts / App.uvue           # 入口（启动时静默登录预热 token）
├── pages/
│   ├── shell/shell.uvue          # 四 tab 单页容器（B10 方案A：TabIndex/TabAi/TabSearch/TabProfile 保活切换）
│   ├── detail/ favorites/ storage/(+trash) messages/ interview/
│   ├── portrait/(manage+theme-detail) settings/ account/security
│   └── privacy/ about/(+agreement) empty/ debug/agg-check
├── components/Tab{Index,Ai,Search,Profile}/   # 由老 pages/{index,ai,search,profile} 整体搬迁（老页已删除）
├── static/                       # 图标(icons/)、空态插画、字体(fonts/)、App 图标(app-icon/)、演示图
├── utils/                        # 41 个 .uts（顶层；另 agg/ 7 个）—— 5.24 迁移后**无 .ts**
│   ├── config.uts                # baseURL 开关（ENV='prod' / PROD_BASE_URL）+ 占位守卫 + resolveMediaUrl
│   ├── auth.uts                  # 设备码登录（/auth/device）+ token 存取 + refresh 单飞 + logout
│   ├── device_id.uts             # 设备唯一标识取值链（ANDROID_ID → storage → 框架 → 仅 dev 兜底）
│   ├── api.uts / retry.uts / log.uts / sentry.uts
│   ├── uploader_{types,pending,net,queue,photo,batch}.uts / upload_pipeline.uts / upload_protocol.uts   # 批量上传（并发/重试/分片；B5c 按分节拆 6 模块）
│   ├── timeline.uts / search_api.uts / play_{echo,interview,messages,favorite,trash,content}.uts / capsule_api.uts   # play 于 B5a 按域拆分
│   ├── voice.uts / audio_player.uts / text_recorder.uts
│   ├── sync_{types,queue,local,pipeline,schedule}.uts / event_sync.uts / event_ops.uts / queue_store.uts   # 同步域于 B5b 按职责拆分
│   └── contract.uts（端点/字段单一契约源）· shell_state.uts · pause_controller.uts · agg_runner.uts · time.uts
├── styles/                       # 外置样式（B5d：detail.uvue 的 <style> 纯移动为 detail.css，uvue 侧 @import）
└── uni_modules/                  # 4 个 UTS 插件（**无 .kt，已全 UTS**）
    ├── yishu-photo-watch/        # 相册监听 + dataSync 前台服务（utssdk/app-android/{config.json,index.uts}）
    ├── yishu-recorder/           # 录音（含运行时权限申请）
    ├── yishu-background-tasks/   # 后台任务（WorkManager 通路）
    └── yishu-device-id/          # ANDROID_ID 读取
```

## 真机联调配置（现行为准）

`utils/config.uts`：

| 场景 | 配置 |
|---|---|
| **内测/生产包** | `ENV = 'prod'` + `PROD_BASE_URL` 填真实地址（端口 **保持 8010**）+ `REAL_DEVICE_HOST = ''` |
| dev 模拟器 | `ENV = 'dev'`，`REAL_DEVICE_HOST = ''` → 走 `10.0.2.2:8010` |
| dev 真机（adb 隧道，最稳） | `ENV = 'dev'`，`REAL_DEVICE_HOST = 'localhost'` → 走 `127.0.0.1:8010`，需 `adb reverse tcp:8010 tcp:8010` |
| dev 真机（局域网） | `ENV = 'dev'`，`REAL_DEVICE_HOST = '<本机局域网 IP>'`，后端需 `--host 0.0.0.0` 且同网段 |

> ⚠️ 端口 **8010** 不可改：8000 会被 HBuilderX launcher 的 node 桩抢占（对任何路径都返回 200 + 裸字符串 "404"，客户端守卫因此静默 resolve(null)，表现为"接口全空但不报错"）。
> 出包前置必须跑 `deploy/scripts/preflight_pack.ps1`（换址清单见 `deploy/ONE_SWAP.md`）。

## HBuilderX 步骤

1. HBuilderX → 文件 → 打开目录 → 选 `D:\GuangH-App\client`
2. 运行 → 运行到手机或模拟器 → 制作自定义基座（首次，权限一次配全）
3. 真机部署 nova 11；logcat 观察 `[yishu]` 前缀日志
4. 授权相册/麦克风 → 触发链路 → 目视确认

## 第一波验收（⚠️ **历史记录**，2026-08-24 当时状态，勿当现状）

| 任务 | 验证 | 状态（当时） |
|---|---|---|
| B-BE-1/2/3 后端 multipart 端点 | pytest 9 项新增 + curl 冒烟（50 张全链路） | ✅ 服务端已验证 |
| B-CL-1 工程骨架 | HBuilderX 打开可编译 | 待峰宝 |
| B-CL-2 环境配置 | 配置切换生效 | 代码就绪 |
| B-CL-3 认证封装 | 调通受保护 API | 代码就绪 |
| B-CL-4 网络层 | 错误 toast 正确 | 代码就绪 |
| B-UT-1..4 UTS 插件 | HBuilderX 编译 + 真机 logcat | 代码就绪 / 编译待峰宝 |
| B-UT-5 自定义基座 | nova 11 部署 | 待峰宝（需授权） |
| B-UP-1/2 上传链路 | 50 张上传 + 自动刷新 | 代码就绪 |
| B-F8-1..4 时间轴 UI | 真机截图 + 自审 ≥75 分 | 代码就绪 |
| B-VA-1 测试照片 | `python scripts/generate_test_photos.py --push` | 生成 ✅ / push 待设备 |

## 已知边界（⚠️ **历史记录**，第一波裁剪当时口径）

- 端侧不落 XView；游标去重用 SharedPreferences（轻量）
- 服务端 timeline 无游标分页（契约 list），客户端分组渲染；服务端分页列第二波
- L2 语义归并待真实数据（P2-07 后端已标注 cloud-proto 候选）
- 图片感知哈希（perceptual_hash）客户端计算列第二波，第一波去重依赖后端

> 现状未闭环项以 `../docs/决策台账.md` §7（未关闭重大缺陷）与 `../docs/远期待办总账.md` 为准。
