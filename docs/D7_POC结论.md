# 忆述光华 · POC D7 正式结论（S1-01 全局 Gate）

> 产出：2026-08-17（D7 结论正式化，按计划 8/23 归档评审）｜依据：《忆述光华_开发决策清单.md》#2（uni-app x 双轨 + POC 前置关卡）
> 证据源：[research/poc/POC五测测试包.md](file:///D:/GuangH-App/research/poc/POC五测测试包.md) + git（761fd25 POC 工程 / e56016c DEV-006 验证 / 0e48e79 结论预判 GO）

## 一、Gate 说明

- **Gate 内容**：验证 uni-app x 双轨架构中"系统能力层"（自研 UTS 插件 = 原生 Kotlin）三条硬路径在 Android 真机跑通。
- **判定规则**（开发规划 v3）：POC-01/02/03 全过 → **GO**（铺开 UTS 双轨）；任一不过 → NO-GO（切原生 Kotlin 单端 / Flutter，走决策清单 #2 升级路径）；POC-04 失败可降级为 DAO 替代方案（不阻塞架构决策）。

## 二、五测结果总表

| # | 测试项 | 验证内容 | 结果 | 证据 |
|---|---|---|---|---|
| POC-01 | 相册监听（ContentObserver） | 新照片 ≤10s 触发导入；杀进程重启自动重挂；撤销权限恢复不崩溃 | ✅ **PASS** | nova 11 真机：push 测试图 + MEDIA_SCANNER 广播 → 9s 内回调 #1/#2/#3（logcat POC:I 20:16:05-14） |
| POC-02 | 前台录音（microphone 前台服务） | 灭屏/切后台录音不中断；中断状态机；文件完整 | ✅ **PASS** | nova 11 真机：灭屏 13s 录制 9822ms 完整文件（目标 ≥8s），前台服务通知常驻（logcat POC02-Recording 20:19:14-26） |
| POC-03 | attribution tag（Android 16 媒体归因） | DEV-006 归因标识正确；DEV-007 低版本不崩溃 | ✅ **PASS（完整）** | DEV-006：Android 16 模拟器（yishu_api36，API 36）content query 断言 `_id=22, owner_package_name=com.yishu.poc`，系统自动归因于写入方 App；DEV-007：nova 11（Android 12）无归因 API 不崩溃 |
| POC-04 | SQLCipher 加密 | 密文无明文泄漏；错误密钥拒绝；轮换不丢数据 | ✅ **PASS** | nova 11 真机：密文无明文 ✓ 错误密钥拒绝 ✓ 正确密钥重读"敏感记忆内容-真机" ✓（logcat POC:I 20:15:39） |
| POC-05 | 事件聚合 spike | ST-DBSCAN 四层聚合算法可行性 | ✅ **PASS** | research/event_aggregation/run_validation.py：10 项验证全过（150 张→500 张 3ms，预算 2s），后续已升级为 M1 Part 1 正式原型（497 张真实截图基准 15 项验证全过，6966f13） |

## 三、结论

> ## **结论：GO（铺开 UTS 双轨）**

- POC-01/02/03/04/05 **五测全部 PASS**，无任一不过项，判定规则下唯一出口为 GO。
- D7 时间线：2026-08-16 真机实测完成 → 结论提前达成（原定 8/23），正式文档归档于 8/23 评审。

## 四、依据与证据链

1. **真机环境**：nova 11（FOA-AL00，Android 12 / EMUI 14.2 / SDK 31）+ 原生 Kotlin 最小工程（24MB APK，research/poc/android/，Gradle 8.7 / JDK 17 / ADB 37.0.1）。
2. **DEV-006 补充验证**：Android 16 强制 API 36 + 扩展归因 API；MediaColumns 无 ATTRIBUTION_ID，归因核心 = `MediaStore.MediaColumns.WRITER` + `files.owner_package_name`（系统自动填充）；第三方 App 查询 WRITER 列被系统隐藏（隐私保护），归因验证以系统侧（adb root / content query）为准。
3. **关联提交**：`761fd25`（POC 工程 + 真机五测）、`e56016c`（DEV-006 attribution 验证完成）、`0e48e79`（结论预判 GO）、`067611b`（环境就绪 nova 11 已连接）。
4. **后续演进**：POC-05 spike → M1 Part 1 事件聚合正式原型（`6966f13` merge，500 张真实截图基准）。

## 五、风险与后续动作

| 风险 | 影响 | 缓解/状态 |
|---|---|---|
| 厂商后台限制（小米/华为等） | 后台任务保活差异 | 明确列入 POC 范围；W3-4 UTS 插件打磨时按厂商白名单逐家验证（COMP-002 验证矩阵） |
| attribution 仅在模拟器验证（DEV-006） | 真机 Android 16 未覆盖 | 模拟器 API 36 系统侧断言已 PASS；有 Android 16 真机后补跑一次（低成本） |
| UTS 插件人力（原生 Kotlin） | 铺开 UTS 后长期瓶颈 | 用户 = T2（具备能力）；T3 交叉学习已列入 Sprint 1 计划 |

**下一步（D7 后）**：Sprint 2 已按 GO 排期执行 —— M1 Part 1/Part 2 完成，SetFit 训练中（S2-03），ASR 接口 + 护栏先行（S2-04），CI + D7 文档收尾（S2-05）。
