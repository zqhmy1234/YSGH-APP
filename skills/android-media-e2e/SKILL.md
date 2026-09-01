---
name: android-media-e2e
description: >-
  Android 真机相册/MediaStore E2E 测试注入与验证：测试照片生成（EXIF 真值）→ adb 推送 →
  MediaStore scan_file 注入 → 权限 pm grant → 观察者触发验证 → 上传/聚合 DB 取证 → UI 截屏确认。
  忆述光华 F1 相册链路真机验收的标准化流程（2026-08-28 Wave3 取证增强版）。
official: false
---

# Android 真机相册 E2E 测试（忆述光华 F1 链路）

> 目标：在真机上可靠地注入"新照片"、触发 App 相册监听链路、并验证端到端结果。
> 配套：《docs/client/07_第一波复盘总结_20260824.md》、scripts/generate_test_photos.py、
> scripts/realdevice/checklist_01..07 + evidence/（真机取证归档处）。

## 适用场景

1. 验证相册监听（ContentObserver）→ 上传 → 聚合 → 时间轴链路（F1 门禁）
2. 需要"可控真值"的照片集（EXIF 拍摄时间已知）驱动 L1/L2 正确率验收
3. 排查"观察者没触发 / 照片没上传 / 时间不对 / 事件没生成"类问题

## 前置检查（发射前全部过一遍，缺一必返工）

```powershell
# P1 后端在线
Invoke-WebRequest http://127.0.0.1:8000/healthz          # 或 uvicorn 起于 Python313\Scripts
# P2 隧道三连（cli launch 启动/退出都会弄丢 reverse tcp:8000，每次 launch 后必做！）
adb -s <SER> reverse tcp:8000 tcp:8000
adb -s <SER> shell "curl -s -m 3 http://127.0.0.1:8000/healthz"   # 必须 {"status":"ok"}
# P3 屏幕常亮 + 前台
adb -s <SER> shell "svc power stayon usb; input keyevent KEYCODE_WAKEUP"
# P4 worker 活着（L2/L3 取证依赖 process_content 完成）
# P5 App 登录态：runloop 日志见 ensureLogin ok / sync pull 即为在线；掉线修复见下
```

- **App 掉线修复（免重启）**：`adb shell svc wifi disable; sleep 4; svc wifi enable` → runloop 出现 "sync network restored" → 下拉刷新触发重试。

## 1. 照片生成（EXIF 真值 —— 子 IFD 陷阱！）

```python
# scripts/generate_test_photos.py 已按此实现（2026-08-28 修复 D-12 误源）
exif = Image.Exif()
ts = dt.strftime("%Y:%m:%d %H:%M:%S")
exif[306] = ts                      # IFD0 DateTime 兜底
exif[34665] = {36867: ts, 36868: ts}  # ★ DateTimeOriginal 必须在 Exif 子 IFD
img.save(path, "JPEG", exif=exif.tobytes())
```

- **教训**：Pillow 扁平写法 `exif[36867]=ts` 落在 IFD0；**Android ExifInterface 只从子 IFD 读 DateTimeOriginal** → 返回 null 且端侧**无日志静默**回退 DATE_ADDED（入库今天）→ 日卡片日期全错。PC 侧 PIL 合并视图能读回，自校验发现不了——**验证必须 `getexif().get_ifd(0x8769)[36867]`**。
- 真实相机照片写标准子 IFD，无此问题；此坑只坑自制测试数据。

## 2. 注入与触发（核心事实：push 会秒级生行，但通知丢失）

```powershell
# 每次重测用【全新目录】test_photos_w3N（游标持久于外部存储 prefs，新行 id>游标才会被发现）
adb -s <SER> push <本地目录> /sdcard/Pictures/
# ★ 扳机必须逐文件 scan_file（EMUI 上 adb push 建的行不给 ContentObserver 发 notifyChange，
#   观察者在 +5s 被唤醒时 found 0 为可见性竞态，之后永不重放 —— D-11 实测两次复现）
adb -s <SER> shell 'for f in /sdcard/Pictures/test_photos_w3N/*.jpg; do content call --uri content://media/external/file --method scan_file --arg $f >/dev/null 2>&1; done; echo SCAN-DONE'
```

- **PS 引号陷阱**：双引号里 `"--arg \$f"` 会被设备侧 sh 当字面量 → 50 次空转 0.6s 假成功。**设备侧 for 循环要么整体单引号，要么经 python subprocess 单字符串传参**。
- scan_file 串行 ≈1.17s/文件（50 张 ≈59s），计时测量时必须扣除这段扳机开销（或改用"observer 首唤醒时刻"口径）。
- 验证生行/计数**只用单列投影**（见取证工具箱），多列逗号投影会报错被吞。

## 3. 取证工具箱（每条都是今晚验证过的可用形态）

```powershell
# A) MediaStore 查询：单列投影 + 不接 2>/dev/null（逗号投影报 Invalid column 被 wc 吞成假 0！）
adb -s <SER> shell "content query --uri content://media/external/images/media --projection _id --where '_id > <CURSOR>'"

# B) content delete 会【连带删真实文件】（shell uid 权限），想留文件先备份再删行

# C) UI 一次采证：dump + 解析（uni-app x 原生节点可 dump）
adb -s <SER> shell uiautomator dump /sdcard/x.xml
adb -s <SER> pull /sdcard/x.xml .cowork-temp/x.xml
python .cowork-temp/ui_parse.py .cowork-temp/x.xml    # 输出 "text [[bounds]]" 行

# D) 截图（含状态栏时钟=时间要素）：adb -s <SER> exec-out screencap -p > evidence/<tag>_<ts>.png

# E) DB 直查（psycopg；注意 DATABASE_URL 要剥掉 +psycopg）：
#    url = open('backend/.env').read 里 DATABASE_URL.replace('postgresql+psycopg','postgresql')
#    查询前先探 schema（event_items 无 user_id；contents 列名是 taken_at 非 taken_time；
#    表名 events/contents/link=event_items(content_id,event_id,created_at)）
#    autocommit=True 再 DELETE——否则 except 分支 rollback 会把删除静默撤销。
#    内联 python -c 带引号/COUNT(*) 必坏 PS 解析 → 一律 Write 脚本文件再跑。

# F) 服务端清库（重测前置）：先查 contents 子表 FK（content_tags/voice_segments/event_items/
#    correction_log/question_history）逐个删，再删 contents；events 单独按 user_id 删。
#    客户端幽灵卡片靠 pull 墓碑自动消失，无需清 App 数据。
```

## 4. 判定链（DB 字段即证据，别只靠日志）

| 问题 | 判据（表.字段） |
|---|---|
| L2/L3 是真实 LLM 还是 mock | `events.title_source='llm' AND generated_by='cloud-llm'` **只有真调用成功分支才写**（event_merge 任何异常降级 → 'template'/'cloud-proto'）；再看标题是否模板格式（`{tag} · N 条`=mock） |
| L1 日期是否用了 EXIF 还是入库时间 | `events.start_time` vs `contents.taken_at` vs 媒体行 date_added；若 start=date_added → 端侧 EXIF 回退（查照片 EXIF 是否子 IFD） |
| 成员数塌缩 | `SELECT count(*) FROM event_items WHERE event_id=...`；端侧 preprocess 按时间戳去重，**同秒入库的照片会被并成 1**（测试数据特有，相机照片间隔≠0 不受影响） |
| 上传成功但事件为空 | 走了离线 pending drain 重传 → **该路径不触发 handleBatch().then() 端侧聚合**（真机缺陷候选：重传后 L1 永不生成，需 App 重启或重新触发） |
| worker 聚合运行 | RQ 日志行 `run_user_aggregation(..., mode='l2l3')` + 完成耗时（l2l3 只组装秒级正常，VL 在逐张 process_content 8-25s 里） |
| 真实外网调用与重试 | worker stderr 中文告警行「外部调用 image_caption 第 n/3 次失败…Ns 后重试」+ ConnectionReset 码（有=真实链路；注意 Windows GBK 乱码用 -X utf8 读） |

## 5. 重测与清理纪律

- App 游标（prefs KEY_LAST_SEEN，外部存储，pm clear 不丢）：重测**不清游标**，注入新目录（新 id>游标）即触发；首扫初始化到 max(id) 是防 9319 张存量事故的硬要求。
- 每轮命名递增（w3f/w3g/w3h...），DB 侧配套清理测试用户（unionid LIKE 'mock-unionid-%'）。
- 设备清理三件套：媒体行删（会连文件）→ PC 备份目录保留 → DB 用户级联清。
- **证据三要素**：设备号+时间（截图带状态栏时钟最佳）、文件路径、判定结论。缺一不算 A 级。
- 失败第一问 = **环境 vs 代码**：先验证读数通道本身可信（如逗号投影假 0、cli 吞隧道），再怀疑被测物。

## 常见问题速查

| 现象 | 原因/修复 |
|---|---|
| 观察者不触发 | **push 不补发 notifyChange（D-11 常态）** → scan_file 逐文件唤醒；目录复用旧行（换新目录）；App 进程死（cli 重启） |
| content query 计数 0 但文件在 | 逗号投影报错被吞（假 0）→ 单列投影重查 |
| 照片 L1 卡片日期=今天 | 测试照 EXIF 不在子 IFD → 端侧静默回退 DATE_ADDED（§1 修复生成器；真实相机照无此病） |
| contents 有了 events 空 | 离线 drain 重传跳过端聚合 → App 重启后对**新注入**目录走 live 路径验证 |
| 上传 500 fake 容量超限 | fake 存储进程内 512MB 上限 → 重启后端进程即清空 |
| auth.ts ClassCastException | uvicorn 死了 HBuilderX httpServer 占 8000 返回非 JSON → 查 healthz 真身再测 |
| cli launch 后全链路 401/超时 | reverse 8000 被 launch/退出清掉 → 前置检查 P2 三连 |
| 点击无反应 | 先 dump 确认当前 UI（CTA 只在空状态存在） |

## 安全/纪律

- 真机是峰宝的日常机：操作前确认前台 App，避免打断使用；**PIN/密码永不由 Agent 代输**；测试完恢复后台并清理注入物。
- 注入量控制：只推需要的数量，避免污染真机相册/后端。
- 调试脚本一律放 `.cowork-temp/`（repo 根运行，路径自带绝对化），可复用的再提升进 scripts/。

## 6. Wave 3 沉淀（2026-08-28 · 05/06/07 波次教训）

- **EMUI 纯净模式拦 adb install 且零错误信息**（P9 前置必查）：自定义基座 APK `adb install` 失败+空错误 → 先看手机屏幕弹窗/设置关闭"纯净模式"，别绕 adb 版本/streamed 排查。
- **UTS 基座能力探测不得用 `getResource('*.class')`**（D-18）：Android class 全编进 dex，`.class` 资源恒不存在 → 恒 false。云包是否含原生类用 `findstr /m /c:<类名> classes*.dex` 尸检；探测改插件自带 marker asset（assets 对 getResource 有效无异常）或 `catch (e: any)` 包 Class.forName。
- **UTS Service 必须 manifest 注册**（D-19）：`class X extends Service` 只在源码里、插件无 config.json/AndroidManifest 片段 → 云包 manifest 无该 service → startService 必死；"标准基座自动回退"注释掩盖了全基座失效。检查：`aapt dump xmltree android_debug.apk AndroidManifest.xml | Select-String service`。
- **语音短录音入库链路**（D-07/D-16/D-17 同族）：≤8MB 直传转写成功但**音频不落 COS** → 管线 AUDIO_NOT_FOUND 判死（contents.failed、text 幸存、voice_segments 空）；原 wav 可从 `Android/data/<pkg>/cache/uni-recorder/` 抽回做 PC 回放补证。情绪三层默认值（models/schemas/客户端 `?? '平静'`）把"未测出"伪造成"平静"，识别失败与真平静 UI 不可分。
- **adb reverse 会随 cli launch 退出/任何 HBuilderX adb 操作丢**（本日 11 次）：每次 launch 后固定补 `adb reverse tcp:8000 tcp:8000` + 设备侧 curl healthz；检测到"HTTP 0/网络异常"先查隧道三连。
- **测试环境隔离铁律**：跑门禁前 `Remove-Item Env:QDRANT_COLLECTION`（手动复现用的环境变量会泄漏进 pytest 弄挂隔离测试）；真实外部调用（AMap 等）会留缓存行污染测试 fixture（geo_cache 按测试坐标同 geohash 短路 mock）——失败第一问先清环境残留。
- **review_agent secrets 会误报 uiautomator XML**：AX 树 EditText 自带 `password` 属性（值 false）命中正则 → 证据 XML 用后即删（PNG 才是证据），或措辞避开 `password=` 字面。
- **云打包 CLI 参数**：`--ignoreWarnings true` 必须显式布尔值；`--playground custom` 切自定义基座运行（配 `--native-log true` 收原生日志）；打包成功日志会本地落 `client/unpackage/debug/android_debug.apk`。
