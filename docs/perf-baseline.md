# 性能基线（2026-09-04 12:4x · P0 测量）

> 环境：真机 DKS9K23526028855（EMUI 中端机）· 标准基座 · 后端 8010（uvicorn worktree 版）· reverse tcp:8010 已建 · 分支 feature/missing-pages-impl
> 测量口径：`am start -W` TotalTime = 首帧绘制，非全可交互；jank 用 dumpsys gfxinfo（滚动 10 屏自动 swipe：540,1800→540,500×10）

## 指标基线

| # | 指标 | 基线值 | 备注 |
|---|---|---|---|
| 1 | 冷启动 TotalTime | **2814ms**（2542/2814/2815 取中位） | force-stop→am start -W；预热 1 次后测 |
| 1b | 冷启动 WaitTime | 2831ms（中位） | 含启动 provider 等待 |
| 2 | 首屏可交互 | ⏳ 与冷启动合并口径 | TotalTime 为代理指标；字体 27MB loadFont×3 在 onLaunch |
| 3 | timeline 滚动 jank | **Janky 0.99%**（807 帧 8 掉帧）；legacy 19.70%；p50=8ms p90=10ms p95=11ms p99=12ms | 基线已健康，P2 重点不在滚动 |
| 4 | 切 Tab 闪屏体感 | ⏳ 待峰宝手测（6 页） | 口径：白屏时长/数据空白期 |
| 5 | 基座 APK 体积 | **63.1MB**（66,168,087 B） | debug 基座，仅作相对对比 |
| 6 | static 资产 | **29MB**，其中 fonts **27MB**（3×Sarasa TTF ~9.1/9.3/9.2MB） | P3 主攻项 |
| 7 | 弱网首屏 | ⏳ 暂缓（无弱网注入环境） | 改后可视条件补 |

## 测量方法存档（复测用同口径）

```bash
ADB="D:/HBuilderX/plugins/launcher-tools/tools/adbs/adb.exe"
D="-s DKS9K23526028855"
ACT="io.dcloud.uniappx/io.dcloud.uniappxv.UniAppActivity"
# 冷启动：预热 1 次后测 3 次取中位
timeout 25 $ADB $D shell am force-stop io.dcloud.uniappx; sleep 3
timeout 25 $ADB $D shell am start -W -n $ACT | grep -E "TotalTime|WaitTime"
# 滚动 jank：reset → swipe×10 → dump
timeout 25 $ADB $D shell dumpsys gfxinfo io.dcloud.uniappx reset
# for i in 1..10: input swipe 540 1800 540 500 300; sleep 1.3
timeout 25 $ADB $D shell dumpsys gfxinfo io.dcloud.uniappx | grep -E "Total frames|Janky|percentile"
```

## 基线结论 → 优化指向

1. 冷启动 2.8s 的主要嫌疑=27MB 字体加载+串行启动链（App.uvue loadFont×3 + ensureLogin→flushOpQueue→fetchAll）→ P1/P3
2. 滚动渲染已健康（p50 8ms）→ P2 渲染层收益主要在**图片渐现/列表分批**而非帧率
3. 首屏被缩略图串行下载阻塞（timeline.uts:261-299）→ P1 卡PA 最高优先

## 复测（2026-09-04 14:55 · 优化后同口径）

| 指标 | 基线 | 优化后 | 备注 |
|---|---|---|---|
| 冷启动 TotalTime 中位 | 2814ms | **2659ms**（2659/2588/2665） | -5.5%；TotalTime 只测 Activity 首帧，字体收益主要体现在包体积(-24.5MB)与加载内存 |
| 滚动 jank | 0.99% | 8.59%（不可比） | 优化后 app 处于 timeline empty→empty 页状态，滚动场景与基线（有数据时间轴页）不同；待数据恢复后重测 |
| 待重测 | — | — | 切 Tab 闪屏体感/首屏可交互——需峰宝验机确认 |

**已知问题（验机确认项）**：app 冷启动后 timeline empty（redirect empty 页），库中数据完好（269 条，设备账号 e4134743 名下 12 条）——疑 cached token 失效后 ensureLogin 重登新映射，待峰宝 app 内退出重登验证。

## 复测修正（2026-09-04 15:1x · 截图实证）

**timeline empty 结论作废**：screencap 实证 app 正常（28 条记忆+待确认卡+分组时间轴全在）；14:53 的 empty 日志系 step1 同步期旧实例在 reverse 掉线窗口的临时失败，冷启动后自愈。**无需重登**。

| 指标 | 基线 | 复测（同口径 10 屏滚动） | 结论 |
|---|---|---|---|
| 冷启动 TotalTime 中位 | 2814ms | **2659ms** | -5.5% |
| 滚动 jank | 0.99%（p50 8ms） | **8.59% / 10.12%**（两次可重复，p50 12ms / p90 16ms / p95 17ms） | **真实恶化**，p50 仍在 60fps 预算内 |

**jank 恶化嫌疑排序**（待峰宝体感裁决；体感流畅则不动）：① lazy-load（图片滚动时才解码，帧预算被吃）② ThumbnailPool 滚动期后台补图抢占 ③ 分批 slice 每渲染重算。基线 0.99% 场景存证缺失（当时 app 页面状态未记录），绝对对比慎读。
