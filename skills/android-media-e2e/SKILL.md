---
name: android-media-e2e
description: >-
  Android 真机相册/MediaStore E2E 测试注入与验证：测试照片生成（EXIF 真值）→ adb 推送 →
  MediaStore scan_file 注入 → 权限 pm grant → 观察者触发验证 → UI 截屏/像素定位确认。
  忆述光华 F1 相册链路（客户端第一波）真机验收的标准化流程。
official: false
---

# Android 真机相册 E2E 测试（忆述光华 F1 链路）

> 目标：在真机上可靠地注入"新照片"、触发 App 相册监听链路、并验证端到端结果。
> 配套：《docs/client/07_第一波复盘总结_20260824.md》（坑清单）、scripts/generate_test_photos.py。

## 适用场景

1. 验证相册监听（ContentObserver）→ 上传 → 聚合 → 时间轴链路（F1 门禁）
2. 需要"可控真值"的照片集（EXIF 拍摄时间已知）驱动 L1/L2 正确率验收
3. 排查"观察者没触发 / 照片没上传 / 时间不对"类问题

## 前置条件

- 真机已授权（`adb devices` 显示 device）；`adb shell svc power stayon usb` 保持常亮
- 测试照片集已生成（`python scripts/generate_test_photos.py`，50 张带 EXIF DateTimeOriginal，3 天 4 片段真值）
- App 已装标准基座并运行（见 skill: hbuilderx-uniappx-runloop）

## 标准流程

### 1. 注入照片到 MediaStore（核心：scan_file 逐文件）

```powershell
# 1) 推到一个【全新目录】（关键：复用旧目录会复用旧行 id，游标不过、观察者不触发）
adb shell "mkdir -p /sdcard/Pictures/yishu_testN"
adb push <本地照片> /sdcard/Pictures/yishu_testN/
# 2) 逐文件 scan_file（目录广播 MEDIA_SCANNER_SCAN_FILE 对目录无效；scan_volume 会卡）
adb shell "content call --uri content://media/external/file --method scan_file --arg /sdcard/Pictures/yishu_testN/<file>"
# 验证入库（display_name 不含路径，用 test_ 前缀匹配）：
adb shell content query --uri content://media/external/images/media --projection _id:_display_name | Select-String "test_"
```

- 每次重测用**新目录名**（test2/test3/...）：rm 文件不删 MediaStore 行，scan_file 会复用同 id，App 游标已过 → 不触发
- 观察者触发节奏：每次 scan_file 触发 notifyChange → App 4s 静默窗口 → 分批查询上传（logcat 可见 `emitIncremental found N` / `batch received: N`）

### 2. 权限（标准基座场景）

```powershell
# SDK 31（Android 12）用 READ_EXTERNAL_STORAGE；SDK 33+ 用 READ_MEDIA_IMAGES
adb shell pm grant io.dcloud.uniappx android.permission.READ_EXTERNAL_STORAGE
# 验证：dumpsys package io.dcloud.uniappx | Select-String "READ_EXTERNAL_STORAGE: granted"
```

### 3. UI/行为验证

- App 日志：`adb logcat -d -t 200 | Select-String "yishu"`（观察者/上传/页面日志均在此）
- 上传结果：`psql ... "SELECT to_char(taken_at,'YYYY-MM-DD'), count(*) FROM contents ... GROUP BY 1"`（验证 EXIF 时间真值）
- 时间轴事件：`SELECT level, title, count(*) FROM events ...`（L1 日卡片数与真值对齐）
- 截屏：`adb exec-out screencap -p > out.png`；**按钮/控件定位用像素分析**（找实心色块 bounding box），不要靠估算坐标——空状态 CTA 是锈红 #B05A3A 实心块，纵向行密度 >120 的连续区间即按钮

### 4. 游标重置/重测技巧

- App 游标（SharedPreferences yishu_photo_watch/last_seen_photo_id）持久化；重测时**不要清游标**，直接注入新目录（新 id > 游标）即可触发
- 想验证"首次启动只收增量"：清 App 数据后首扫会把游标初始化到 max(id)（预期行为，见 lessons 隐私红线）
- 全量存量事故复盘：游标=0 时首扫会命中整个相册（曾有真机 9319 张事故）——游标初始化到 max(id) 是硬要求

## 常见问题速查

| 现象 | 原因/修复 |
|---|---|
| 观察者不触发 | 目录复用旧行 id（换新目录）；App 进程被冻结（强停后重 launch）；屏幕锁屏（stayon usb） |
| 照片 taken_at 全是今天 | MediaProvider scan_file 不提取 EXIF，DATE_TAKEN=扫描时间 → 后端 PIL EXIF 权威解析兜底（客户端时间不可信） |
| 上传 500 fake 容量超限 | fake 存储进程内 512MB 上限（防护）→ 重启后端进程即清空；真实联调用 minio/cos |
| 点击无反应 | 可能点的是旧内容（CTA 只在空状态存在）→ 先截屏确认当前 UI 再操作 |
| 删除后重扫不触发 | 见"每次重测用新目录名" |

## 安全/纪律

- 测试照片用完删除设备目录 + 清理 DB 测试用户（unionid LIKE 'mock-unionid-%'）
- 注入量控制：只推需要的数量（50 张），避免污染真机相册/后端
- 真机是峰宝的日常机：操作前确认前台 App，避免打断使用；测试完恢复后台
