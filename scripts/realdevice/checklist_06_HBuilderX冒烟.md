# Checklist 06 · H5 收口后 HBuilderX 编译真机冒烟（§2.2 遗留①）

> 生成：2026-08-28｜归属：scripts/realdevice（Agent D1）｜Wave 3 真机执行
> 对应：报告 §2.2 遗留①（H 批客户端收口后补跑编译 + 真机冒烟，行为等价验证）
> 相关技能：`skills/hbuilderx-uniappx-runloop/SKILL.md`（编译/装包/UTS 避坑）、`skills/android-media-e2e/SKILL.md`（注入/像素定位）

---

## 1. 目的

H 批客户端收口后补跑 **HBuilderX CLI 编译 + 真机冒烟**：六页可达、关键链路可用、全程无崩溃（行为等价验证，H 批遗留销项）。

## 2. 前置条件

- P9 HBuilderX CLI 环境（`D:\HBuilderX\cli.exe`；JDK17 / Android SDK / adb 就绪）；P3 nova 11 开发者模式 + USB/adb；P1 后端在线（联调用 adb reverse 最稳）。

### 2.1 前置就绪自查（不满足则本清单标"待补"，勿硬跑）

- □ HBuilderX CLI 可调用：`& D:\HBuilderX\cli.exe --version`（或 `cli.exe -h`）
- □ 设备在线：`Get-Device`（记下序列号，装包用 `--deviceId`）
- □ 后端在线：`curl http://127.0.0.1:8000/healthz`；如走真机联调先 `adb reverse tcp:8000 tcp:8000`
- □ 编译产物目录不冲突（`client/unpackage/`、`client/.hbuilderx/` 为构建产物，已 gitignore）

## 3. 执行步骤

**Step 1 · HBuilderX CLI 标准编译**
- 执行：`& D:\HBuilderX\cli.exe launch app-android --project "D:\GuangH-App\client" --compile true`
- **预期**：结尾输出 `项目 client 编译成功`；`[plugin:uni:app-uts] 编译失败` = 按 skill §4 速查表修。
- 记录 HBuilderX 版本与编译耗时。
- 采证：编译日志重定向 `evidence/ck06_build_<ts>.log`（或截取控制台）。

**Step 2 · 安装到真机**
- 执行：`& D:\HBuilderX\cli.exe launch app-android --project "D:\GuangH-App\client" --deviceId "<序列号>"`（序列号来自 `Get-Device`）
- **预期**：输出 `应用【client】已启动`；若"手机无响应"→ `adb shell am start -n io.dcloud.uniappx/io.dcloud.uniapp.UniAppActivity` 手动拉起后重跑。
- 采证：启动日志 `ck06_launch_<ts>.log`。

**Step 3 · 六页冒烟**
- 逐页可达、无白屏/崩溃（用四宫格导航 reLaunch 切换）：
  1. **首页（时间轴）** 2. **记录（文字 + 录音入口）** 3. **搜索** 4. **我的** 5. **访谈** 6. **消息**
- 每页：截图 + 无 FATAL。
- 采证：`Shot 06 1a`…`Shot 06 6a`（六页各一张）；`GrabLog 06 fatal`。

**Step 4 · 关键链路冒烟**
- ① 文字记录 → 分类 → 时间轴出现（记录页输入 → 提交 → 首页时间轴出现该条）
- ② 搜索词 → 结果（搜索页输入关键词 → 有结果卡片）
- ③ 相册导入 → 上传横幅（注入 1–3 张新照片 → 首页上传横幅出现）
- 采证：`Shot 06 7a`（时间轴出现文字条目）、`Shot 06 7b`（搜索结果）、`Shot 06 7c`（上传横幅）；`GrabLog 06 yishu`。

**Step 5 · 全程崩溃检查**
- 全程 `adb logcat` 检查**无 FATAL EXCEPTION**（`AndroidRuntime` FATAL）。
- 采证：`GrabLog 06 fatal`（关键词 `FATAL`/`AndroidRuntime`）；`GrabLog 06 crash`。

## 4. 预期（可判定口径）

| 项 | 预期 | 判定口径 |
|---|---|---|
| 编译 | 编译无错 | 结尾 `项目 client 编译成功` |
| 六页 | 首页/记录/搜索/我的/访谈/消息均可达，无白屏/崩溃 | 六页截图 + 无 FATAL |
| 关键链路 | 文字→分类→时间轴；搜索→结果；相册→上传横幅 | 3 条链路截图 + 日志 |
| 崩溃 | 全程无崩溃 | logcat 无 `FATAL EXCEPTION` |

## 5. 证据清单（证据三要素：①nova 11 + 日期时间 ②截图/日志路径 ③结果判定）

- 截图：`evidence/ck06_1a..6a_<ts>.png`（六页）、`ck06_7a_<ts>.png`（时间轴条目）、`ck06_7b_<ts>.png`（搜索结果）、`ck06_7c_<ts>.png`（上传横幅）
- 日志：`evidence/ck06_build_<ts>.log`（编译）、`ck06_launch_<ts>.log`（装包启动）、`ck06_fatal_<ts>.log`、`ck06_crash_<ts>.log`、`ck06_yishu_<ts>.log`
- logcat 过滤关键词：`FATAL` / `AndroidRuntime` / `yishu`
- 记录：HBuilderX 版本 __；编译耗时 __s；装包耗时 __s

## 6. 判定标准

- **✅ 通过**：编译无错 + 六页可达 + 3 条关键链路可用 + 无 FATAL → **H 批遗留销项**。
- **❌ 失败**：任一页崩溃 / 白屏（附 logcat 堆栈）；关键链路任一不可用。
- **🟡 部分**：编译成功但个别链路受后端/环境阻塞（如后端未起）→ 标注阻塞项，其余照验。

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

- **行为等价**：H 批收口后六页/链路与收口前行为一致即视为等价，无需逐像素对比。
- 像素定位提示：uni-app x 自绘 UI 的 uiautomator dump 不可靠，按钮定位用截图像素分析（锈红 #B05A3A 实心块），见 skills/android-media-e2e。
- 设备是日常机：测试照片/测试内容用完清理（删除设备目录 + 清测试账号数据）。
