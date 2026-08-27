# 真机补验准备 · 7 份清单 + adb 脚本（scripts/realdevice/）

> 归属：**Agent D1**（Wave 1 · 真机补验准备域）｜分支 `wrap1-agentD1`
> 服务对象：**Wave 3 用户/协调者**在 nova 11 真机照做执行
> 依据：《docs/parallel-dev-收尾/00_总纲_收尾.md》§1 项 12/16/17、《07_晚间例会汇报_20260827.md》§8.1④ + §6.3（B 级升 A）+ §7.1（30s 门禁）
> 铁律：**无真机记录不得宣称打通**（07 报告证据纪律）；设备不在场 / 网络不可用 / 系统不支持 → 标 **"待补"**，**不得虚构 A 级**。

---

## 1. 7 份清单索引（对应报告 / 用户故事 / 门禁）

| 编号 | 清单文件 | 对应 | 前置条件 | 验证档位 |
|---|---|---|---|---|
| 01 | `checklist_01_蜂窝同步.md` | US-48/46/47 · 同步流量约束 | 蜂窝 SIM 卡可开关 + 相册 ≥20 张含 EXIF | mock/真实均可 |
| 02 | `checklist_02_录音中断.md` | US-20/52 · 录音中断状态机 | 通话模拟方式（或闹钟/媒体抢占备选） | mock/真实均可（只管状态机） |
| 03 | `checklist_03_首批30s计时.md` | §7.1 首批事件 30s 门禁 🟡→✅ | 相册 ≥50 张（建议 60 余量）含 EXIF + 测试账号隔离 | mock 即可 |
| 04 | `checklist_04_L2L3云侧归并.md` | US-06/07 · L2 主题/L3 流归并（B 级升 A） | 后端在线 + **DASHSCOPE key 真实档** + 跨日照片组 15–20 张 | **真实 qwen（非 mock）** |
| 05 | `checklist_05_真实转写情绪.md` | US-17/18/19 · FunASR 转写 + SenseVoice 情绪（B 级升 A） | 后端在线 + **DASHSCOPE key 真实档** + 3 段 30–60s 安静录音 | **真实双通道（非 mock）** |
| 06 | `checklist_06_HBuilderX冒烟.md` | §2.2 遗留① · H 批编译真机冒烟 | HBuilderX CLI + nova 11 USB/adb + 后端在线 | mock 即可 |
| 07 | `checklist_07_自定义基座.md` | B5d 遗留 · 自定义基座云打包（SQLCipher/FGS/WorkManager/attribution） | **DCloud 账号 + 自定义基座打包权限** + HBuilderX CLI | 真实（自定义基座） |

## 2. 前置条件总表（Wave 3 执行前逐项勾，7 份清单共用）

| # | 前置 | 用于清单 | 说明 / 判定就绪 |
|---|---|---|---|
| P1 | 本地后端在线 | 01–06 | `uvicorn app.main:app --host 0.0.0.0 --port 8000`；真机联调用 **adb reverse**（`adb reverse tcp:8000 tcp:8000`，config.ts REAL_DEVICE_HOST=localhost）最稳 |
| P2 | DASHSCOPE key 真实档 | 04/05 | `.env` 已配 DASHSCOPE_API_KEY + DASHSCOPE_WORKSPACE_ID + `MOCK_EXTERNAL_AI=false`；**后端日志必须出现真实 qwen / funasr / sensevoice 调用，不得有 mock 标记**（否则该项不达标） |
| P3 | nova 11 已装最新包 + USB 调试授权 | 01–07 | `adb devices` 显示 `device`（非 unauthorized）；`adb shell svc power stayon usb` 保持常亮 |
| P4 | 蜂窝 SIM 卡可开关 | 01/03 | 系统设置 → 移动网络 关/开 WiFi 切换流量形态 |
| P5 | 相册数据 | 01/03/04 | 含 EXIF 照片：01 ≥20 张；03 ≥50 张（建议 60）；04 跨日/跨场景 15–20 张（分 2–3 天） |
| P6 | 测试账号数据隔离 | 03（其他可选） | 清空该测试账号旧 contents/events（或新建测试账号），避免旧数据污染 30s 计时与归并判定 |
| P7 | 通话模拟方式 | 02 | 第二设备呼入；不可用则备选：**系统闹钟 / 播放媒体** 触发 `onInterruptionBegin`（README §5 备选） |
| P8 | 3 段 30–60s 真实录音 | 05 | 平静 / 开心 / 低落各一，背景安静，原文留存（对照表用） |
| P9 | HBuilderX CLI 环境 | 06/07 | `D:\HBuilderX\cli.exe` 可调用；JDK17/Android SDK/adb 就绪（见 skills/hbuilderx-uniappx-runloop） |
| P10 | DCloud 账号 + 自定义基座打包权限 | 07 | DCloud 开发者中心登录态；云打包自定义基座权限 |
| P11 | evidence 输出目录 | 全部 | `scripts/realdevice/evidence/` 已建（gitignore 不入库），adb 脚本自动写入 |

> 前端联调说明：清单 04/05 需 P1+P2（真实档）；清单 01/03 需 P4（蜂窝切换）+ P5；清单 07 需 P10（云打包）。
> 任一清单前置未满足 → 该清单标 **"待补"** 并记录缺哪项，**不得跳过前置硬跑**（会导致证据不可判定）。

## 3. 证据标准（7 份清单统一遵守）

| 项 | 标准 |
|---|---|
| 证据三要素 | 每项通过必须有：①真机型号（nova 11）+ 日期时间；②截图/日志文件路径；③结果判定（通过/失败/降级原因） |
| 截图命名 | `evidence/ck<编号>_<步骤>_<YYYYMMDD_HHMMSS>.png`（adb 脚本 `Shot` 自动命名） |
| 日志采集 | `adb logcat -d -T <时间戳> > evidence/ck<编号>_<关键词>.log`（adb 脚本 `GrabLog` 自动命名）；过滤关键词每清单"证据"节写明 |
| 记录表 | 每份清单末尾固定模板（§记录表），逐格填写；**缺一格 = 该项证据不完整** |
| 降级规则 | 设备不在场 / 网络不可用 / 系统版本不支持 → 标 **"待补"**，不得虚构 A 级（07 报告证据纪律） |

## 4. adb_helpers.ps1 使用

```powershell
# 载入（每次新开 PowerShell 先执行）
cd D:\GuangH-App\scripts\realdevice
. .\adb_helpers.ps1

# 设备检查
Get-Device
KeepAwake                    # 屏幕常亮（防熄屏黑图）

# 截图：evidence/ck01_2a_20260828_101530.png
Shot 01 2a

# 日志（关键词过滤 + 时间窗）：evidence/ck01_upload_20260828_101530.log
GrabLog 01 upload

# 录屏（动态场景：录音中断/恢复）→ evidence/ck02_int_20260828_101530.mp4
Record 02 int 30

# 时间戳（手动命名用）：20260828_101530
Now
```

函数清单：`Get-Device`（设备在线检查）、`Shot <ck> <step>`（截图）、`GrabLog <ck> <keyword> [since]`（日志）、`Record <ck> <step> [seconds]`（录屏）、`Now`（时间戳）、`Clear-Logcat`、`GrabAppLog <ck>`（抓 `[yishu]` 前缀主日志）、`KeepAwake`。
> 注：`Shot` 用 `Start-Process` 字节直写，避免 PowerShell `>` 文本重定向损坏 PNG 二进制。

## 5. Wave 3 执行顺序建议（高频项先行）

| 序 | 清单 | 理由 |
|---|---|---|
| 1 | **03 首批 30s 计时** | 门禁 🟡→✅ 的关键销项，前置最轻（mock 档即可），先跑拿结论 |
| 2 | **01 蜂窝链路 + 同步横幅** | 流量约束是同步域核心承诺，蜂窝卡就绪即可跑 |
| 3 | **02 录音中断恢复** | 中断状态机为语音域高风险项，不依赖真实 key |
| 4 | **05 真实转写/情绪** | 需真实档 key + 安静录音，B 级升 A |
| 5 | **04 L2/L3 云侧归并** | 需真实档 key + 跨日照片组，B 级升 A |
| 6 | **06 HBuilderX 编译冒烟** | 环境就绪随手跑，H 批遗留销项 |
| 7 | **07 自定义基座云打包** | 依赖 DCloud 云打包（耗时/权限），放最后 |

> 若 P2（真实档 key）未就绪：04/05 标"待补"先行做其余，不阻塞 01/02/03/06。
> 每份清单执行完 → 填记录表 → 证据文件落 `evidence/` → 结果汇总进 `progress.md`（格式参照既有"真机 E2E 全链路验收 08-24"条目）。

## 6. 目录结构

```
scripts/realdevice/
├── README.md            # 本文件
├── adb_helpers.ps1      # adb 封装（截图/日志/时间戳/录屏/设备检查）
├── checklist_01_蜂窝同步.md
├── checklist_02_录音中断.md
├── checklist_03_首批30s计时.md
├── checklist_04_L2L3云侧归并.md
├── checklist_05_真实转写情绪.md
├── checklist_06_HBuilderX冒烟.md
├── checklist_07_自定义基座.md
└── evidence/            # Wave 3 证据输出（.gitignore 不入库）
```
