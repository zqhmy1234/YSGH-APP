# Checklist 01 · 蜂窝链路 + 同步横幅（US-48/46/47）

> 生成：2026-08-28｜归属：scripts/realdevice（Agent D1）｜Wave 3 真机执行
> 对应：报告 §8.1④｜功能域：同步（流量约束 + 暂停/恢复横幅 + 断网重试幂等）
> 相关代码：`client/utils/uploader.ts`（NetKind/decideMode/held 队列/continuePendingUploads）、`client/components/UploadStatusBanner/UploadStatusBanner.uvue`（横幅）、`client/utils/sync_client.ts`（暂停/恢复/网络恢复钩子）

---

## 1. 目的

验证流量约束承诺（B4 §5）：**WiFi 传原图、蜂窝只传缩略图+元数据**（当前 Agent G upload_mode 未落地 → 蜂窝自动=**暂缓原图入 held 队列**，不消耗流量）；同步暂停横幅触发/恢复；断网重试不丢数据（op 队列幂等）。

## 2. 前置条件

- P1 本地后端在线（mock 档即可）；P3 nova 11 已装最新包 + USB 授权；P4 **蜂窝 SIM 卡可开关**；P5 相册 ≥20 张含 EXIF 照片。

### 2.1 前置就绪自查（不满足则本清单标"待补"，勿硬跑）

- □ 后端在线：`curl http://127.0.0.1:8000/healthz` → `{"status":"ok"}`
- □ 设备在线：`Get-Device` 显示 `device`；`KeepAwake` 保持常亮
- □ 蜂窝 SIM 卡在位，可开关 WiFi（设置 → WLAN 关/开）
- □ 相册含 ≥20 张带 EXIF 照片（用 `scripts/generate_test_photos.py` 生成 + scan_file 注入，见 skills/android-media-e2e）
- □ App 已登录测试账号（记录账号标识，用于 DB 核对）

## 3. 执行步骤

> 每步结尾采证据（截图用 `Shot <ck> <step>`，日志用 `GrabLog <ck> <keyword>`；开始前 `Clear-Logcat`）。

**Step 1 · WiFi 下导入 10 张 → 上传原图**
- 确认当前 WiFi 连接（设置 → WLAN 开）。
- 注入 10 张测试照片到**全新目录**（`/sdcard/Pictures/yishu_test1`，逐文件 `content call ... scan_file`，见 skills/android-media-e2e；换新目录名防游标不触发）。
- 等待观察者分批上传完成（logcat 可见 `emitIncremental found N` / `batch received: N` / 上传日志）。
- 采证：`Shot 01 1a`（首页横幅/时间轴）；`GrabLog 01 upload`（上传体积日志）。

**Step 2 · 切蜂窝（关 WiFi）→ 导入 10 张 → 记录体积**
- 设置 → WLAN 关（走蜂窝 4G/5G）。
- 注入 10 张测试照片到新目录（`yishu_test2`）。
- **预期**：蜂窝下原图**不自动上传**（held 队列累积，`heldCount()` 增加）；logcat 有 `蜂窝网络：只传缩略图+元数据` / `蜂窝/离线暂缓` 日志；**无原图上传字节**。
- 采证：`Shot 01 2a`（横幅 held 态）；`GrabLog 01 held`（暂缓日志）；记录后端本次蜂窝窗口新增 contents 数（应 ≈0，因为原图未传）。

**Step 3 · 横幅两态 + 手动"立即上传原图"**
- 蜂窝 held 态横幅文案应为：`N 张照片待 WiFi 上传（蜂窝仅传缩略图+元数据）`，按钮 `立即上传原图`。
- 点 `立即上传原图`（`continuePendingUploads`）→ 观察 10 张原图上传成功。
- 采证：`Shot 01 3a`（held 态横幅）；`Shot 01 3b`（手动上传中/完成）；`GrabLog 01 upload`。

**Step 4 · 断网暂停 → 恢复网络 → 重试完成不丢**
- 开飞行模式（模拟断网）→ 触发一次上传（导入 1 张到 `yishu_test3`）→ 上传失败，sync_client 暂停。
- 横幅应显示暂停态（`网络异常，已暂停同步`，按钮 `继续上传`）。
- 关飞行模式（恢复网络）→ 观察自动补传（WiFi 恢复钩子 / `继续上传`）→ 全部成功。
- 采证：`Shot 01 4a`（暂停态横幅）；`Shot 01 4b`（恢复后完成）；`GrabLog 01 pause`；`GrabLog 01 network`。

## 4. 预期（可判定口径）

| 步骤 | 预期 | 判定口径 |
|---|---|---|
| 1 | WiFi 下 10 张原图全部上传成功，体积≈原图 | 后端 contents 该账号 +10；上传日志有原图体积 |
| 2 | 蜂窝下原图不自动上传（held 累积），体积≈0；横幅提示"待 WiFi 上传（蜂窝仅传缩略图+元数据）" | 蜂窝窗口新增 contents ≈0；logcat 有蜂窝暂缓日志（引 `uploader.ts decideMode: cellular→hold`） |
| 3 | 手动"立即上传原图"成功上传 10 张 | 后端 contents 该账号 +10；横幅恢复"正在上传…"→正常 |
| 4 | 断网上传失败不丢：暂停横幅出现，网络恢复后队列自动/手动重试全部成功 | 后端 contents 最终 = 首日 10 + 蜂窝 10 + 断网 1 = 21；无永久 failed |

## 5. 证据清单（证据三要素：①nova 11 + 日期时间 ②截图/日志路径 ③结果判定）

- 截图：`evidence/ck01_1a_<ts>.png`、`ck01_2a_<ts>.png`、`ck01_3a_<ts>.png`、`ck01_3b_<ts>.png`、`ck01_4a_<ts>.png`、`ck01_4b_<ts>.png`
- 日志：`evidence/ck01_upload_<ts>.log`、`ck01_held_<ts>.log`、`ck01_pause_<ts>.log`、`ck01_network_<ts>.log`
- logcat 过滤关键词：`yishu` / `upload` / `held` / `蜂窝` / `pause` / `sync` / `network`
- DB 核对（可选，psql）：`SELECT count(*) FROM contents WHERE user_id='<测试账号>'`（最终应为 21）

## 6. 判定标准

- **✅ 通过**：Step 1–4 全过；尤其 Step 2 蜂窝下不自动传原图（流量约束承诺成立，引 `uploader.ts` NetKind/decideMode 常量核对）、Step 4 断网不丢（21 条全落库）。
- **❌ 失败**：蜂窝仍自动上传原图 / 横幅提示缺失 / 断网后丢数据（附日志定位行）。
- **🟡 部分**：P4 蜂窝卡不可用 → 蜂窝两项标"待补"，仅记录 WiFi 与断网项。

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

- 蜂窝下"缩略图+元数据"为 B4 §5 契约；当前 Agent G upload_mode 未落地，**蜂窝自动 = 暂缓原图**（不传，流量 0）。若验收日 upload_mode 已落地，Step 2 改为"蜂窝传缩略图+元数据，体积明显小于原图"（阈值引 uploader.ts thumbnail 分支，体积对比按后端存储对象大小统计）。
- 设备是日常机：操作前确认前台 App；测试照片用完删除设备目录 + 清理 DB 测试用户（`unionid LIKE 'mock-unionid-%'`）。
