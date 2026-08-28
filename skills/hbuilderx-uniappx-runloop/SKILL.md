---
name: hbuilderx-uniappx-runloop
description: >-
  忆述光华客户端（uni-app x）开发主循环：HBuilderX CLI 编译验证 → 真机运行 → 日志/截图验证，
  含 UTS 编译器高频错误速查表（UTSJSONObject/Promise catch/对象字面量/overload panic 等）、
  插件安装/基座/权限/屏幕管理等环境问题排查。改任何 client/ 代码前先读本 skill。
official: false
---

# HBuilderX + uni-app x 编译运行循环（忆述光华客户端）

> 目标：改 client/ 代码后，用最短循环（编译 ~30s / 真机 ~1min）验证并修复，踩过的坑不重踩。
> 配套：《docs/client/07_第一波复盘总结_20260824.md》（坑根因全景）、docs/lessons.md（强制教训台账）。

## 适用场景

1. 改了 client/ 下任何 .uvue / .uts / .ts / manifest.json / pages.json
2. 需要验证 UTS 插件/页面能否编译（不碰真机）
3. 需要部署到真机 nova 11 并观察行为（logcat / 截图）
4. 遇到 UTS 编译错误、HBuilderX 插件安装失败、基座/权限问题

## 前置条件

- HBuilderX 5.15 已装（D:\HBuilderX）；JDK17 / Android SDK / adb 就绪
- 真机 nova 11（FOA-AL00）已 USB 调试授权（`adb devices` 显示 device 而非 unauthorized）
- 项目已导入 HBuilderX（`cli project open --path D:\GuangH-App\client`；重装后需重导）
- 后端 dev 服务跑着（真机联调：`uvicorn app.main:app --host 0.0.0.0 --port 8000`；client/utils/config.ts 的 REAL_DEVICE_HOST 填电脑局域网 IP）

## 标准流程

### 1. 编译验证（不碰真机，~30s/轮）

```powershell
& D:\HBuilderX\cli.exe launch app-android --project "D:\GuangH-App\client" --compile true
```

- 只看结尾：`项目 client 编译成功` = 过；`[plugin:uni:app-uts] 编译失败` = 按第 4 节速查表修
- 黄色 `warning: Identity equality ... boxing` 可忽略（statusCode === 200 的装箱告警）

### 2. 真机运行（~1min）

```powershell
& D:\HBuilderX\cli.exe launch app-android --project "D:\GuangH-App\client" --deviceId "DKS9K23526028855"
```

- 首次会装/更新调试基座（约 200MB，装完可能"手机无响应"——手动拉起基座后重跑即可）
- 成功标志：`应用【client】已启动`；App 控制台日志（`[yishu]` 前缀）实时输出在该会话

### 3. 观察与验证

- App 日志：`adb logcat -d -t 200 | Select-String "yishu"`（App 内 console.log 也会进 logcat，`I console` 标签）
- 截屏：`adb exec-out screencap -p > out.png`（用 image 工具/像素分析看内容）
- 屏幕常亮：`adb shell svc power stayon usb`（设备 30s 熄屏会导致 adb tap/截图全黑）

### 4. UTS 编译错误速查表（第一波实战高频）

| 错误 | 修复 |
|---|---|
| `display: block` 不支持 / `page` 选择器不支持 | uvue 仅 class 选择器 + display: flex/none；全局底色走 pages.json + 根 .page 类 |
| `Object literals only support ... construction type`（UTS110111163） | DTO 用 **class + 构造函数**（interface 不能对象字面量）：`new TimelineEvent(...)` |
| `找不到名称 UTSJSON` / `Could not resolve "UTSJSON"` | 全局类型名是 **UTSJSONObject**（UTSJSON 不是全局）；UTSAndroid/UTSJSONObject 均无需 import |
| `Promise.catch 重载不匹配`（None of the following candidates） | 全改 **resolve-only**：失败 resolve(null)+toast，不 reject；onRejected 参数留空 `() => {}`；then 回调参数显式标注类型 |
| `找不到名称 tryOnce/worker`（先定义后使用） | const 箭头不可自引用 → 模块级 function / class 方法（UploadPool 模式） |
| `overload error A != B`（编译器 panic） | interface.uts 少放函数声明；平台 index.uts **不要 `export * from interface.uts`**；类型用 `export { X } from ...` 具名再导出 |
| `返回类型不匹配 Context vs Context?` | `UTSAndroid.getAppContext()!` 非空断言；`getUniActivity()` 才是拿 Activity 的 API |
| `实际类型 Number 预期 Long` | 声明 `const x: Long`；比较用 `ZERO_LONG` 常量而非 `0` |
| `UTSArray<String> 预期 Array<...>`（原生互转） | MediaStore query 传 null projection/selectionArgs，值拼进 selection 字符串 |
| `catch (e: Error | null)` 类型不匹配 | `catch (e: Error)` |

### 5. 环境问题排查

- **插件 npm 安装 ETARGET**：HBuilderX 默认 npmmirror 滞后于 5.15。先 `npm view <pkg> versions --registry https://registry.npmjs.org` 查官方源；没有就 `Invoke-WebRequest` 从 `https://update.liuyingyong.cn/hbuilderx/upgrade_repositories/5.15.2026070915/win32/plugins/contents/<pkg>.<ver>.zip` 下载解包到 `D:\HBuilderX\update\plugins\`（如 uniapp-runextension 219MB）
- **标准基座权限**：标准调试基座用基座自身 manifest，项目 manifest.json 权限不生效。运行时权限按 SDK 选名：`Build.VERSION.SDK_INT >= 33 ? READ_MEDIA_IMAGES : READ_EXTERNAL_STORAGE`；`adb shell pm grant io.dcloud.uniappx <perm>` 验证
- **基座"手机无响应"**：`adb shell am start -n io.dcloud.uniappx/io.dcloud.uniapp.UniAppActivity` 手动拉起后重跑 launch
- **端口占用**：`Get-NetTCPConnection -LocalPort 8000 -State Listen` 找 PID 强杀；起服务前确认 FREE

## 安全/纪律

- 改完必须过 review_agent 全绿才能提交；`client/` 已被 ruff/review_agent 排除（B2 决策，Python 工具链不扫）
- 提交前 `git status` 检查：`client/unpackage/` `client/.hbuilderx/` 是构建产物（已 gitignore）
- 真机测试数据用完清理（测试照片目录 / DB 测试用户），不留脏状态

## 6. Wave 3 真机波教训（2026-08-28 沉淀，补录）

- **adb reverse 铁律（本波 11+ 次阵亡）**：`cli launch` 退出、任何 HBuilderX GUI/打包操作、adb server 重启都会清掉 `adb reverse tcp:8000 tcp:8000`。**每次 launch 后固定补 reverse + 设备侧 `adb shell curl -s http://127.0.0.1:8000/healthz` 探活**；App 报"网络异常/HTTP 0"第一排查=隧道三连（reverse 列表 / 设备 curl / PC healthz），别急着查代码。
- **EMUI 纯净模式拦 adb install 且零错误提示**：`adb install` 失败+空错误 → 先看手机屏幕弹窗/设置关"纯净模式"，别绕 adb 版本/streamed 排查。
- **云打包 CLI（07 清单实参）**：`cli pack --platform android --iscustom true --android.packagename com.yishu.guanghua --ignoreWarnings true`——`--ignoreWarnings` 需**显式布尔值**；自定义基座运行=`cli launch ... --playground custom --native-log true`（native-log 收原生日志）；出包自动落 `client/unpackage/debug/android_debug.apk`；先 `aapt dump xmltree <apk> AndroidManifest.xml | Select-String service` 验基座 manifest。
- **UTS 基座能力探测不得用 `getResource('*.class')`**（D-18 根因）：Android class 全编译进 dex，`.class` 资源恒不存在→探测恒 false。云包是否含原生类用 `findstr /m /c:<类名> classes*.dex` 尸检；探测改插件自带 marker asset 或 `catch (e: any)` 包 Class.forName。
- **UTS Service 必须 manifest 注册**（D-19 根因）：`class X extends Service` 只在源码里、插件无 manifest 片段 → 云包无该 service → startService 必死；"标准基座自动回退"注释掩盖全基座失效。
- **全量编译才暴露存量错**：增量编译 warm cache 会掩盖 UTS 错误，`--cleanCache` 全量编译是回归前的硬验证（本波 7 处存量 UTS 错如此暴露）。
