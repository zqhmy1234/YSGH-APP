# Wave 4 · Agent K（B5d Android 后台域）任务卡——docs/parallel-dev/11

## Mission
按《_B5a_B5d修正后待办.md》完成 K-1/K-2：Android 前台服务（microphone/dataSync 互斥、短命化）+ WorkManager 队列（P0-P4、WiFi 约束）+ attribution tag 落地；ASR 适配器抽象（低优先）。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md` + `13_集成规则` + **`_B5a_B5d修正后待办.md`**。
2. audit 证据：`audit_B5a_B5d_voice.md` §8——前台服务/WorkManager/attribution 全仓零命中；现有 `client/uni_modules/yishu-photo-watch/utssdk/app-android/index.uts`（ContentObserver，页面级注册，App 被杀失效）；POC 验证过 attribution（D7_POC结论 POC-03）。
3. 设计依据：B5d §2 统一调度（单前台服务+单 WorkManager 队列）、§3 Android 实现边界（短命化、microphone/dataSync 不同时开、attribution tag：sync_photo/voice_transcribe/event_aggregate/profile_fetch）、§5 优先级表（P0 语音>P1 新照片>P2 聚合>P3 拉取>P4 批量）。
4. 技能：`skills/android-media-e2e/SKILL.md`（真机 E2E）；`skills/hbuilderx-uniappx-runloop/SKILL.md`（编译循环）。

## Scope（可改）
1. `client/uni_modules/yishu-photo-watch/`（**你独占**：升级为前台服务承载 dataSync 监听 + 短命化）
2. **新插件** `client/uni_modules/yishu-background-tasks/`（WorkManager 队列 UTS 桥接：P0-P4、WiFi 约束、指数退避、attribution tag 上报）
3. `client/pages/index/index.uvue`（**只读**：需要接线时登记给集成 Agent，你不改）
4. `skills/android-media-e2e/SKILL.md`（E2E 步骤更新，如需）
5. 相关测试/验证记录（.cowork-temp/）

## 绝不碰（只读）
`client/utils/voice.ts`、录音插件（Agent J 域）；`backend/` 全部（后端 RQ 两级队列已存在，WorkManager 是客户端侧）；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **前台服务短命化**：yishu-photo-watch 升级——前台服务（microphone 录音时 / dataSync 监听时，两类型不同时开，互斥切换）；录音结束 stopForeground 降级；不常驻 24h。
2. **WorkManager 队列**：新插件 yishu-background-tasks——任务注册（upload/aggregate/pull 等静默任务）、优先级链（P0/P1 独立高优先，P2-P4 低优先共享）、WiFi 约束（NetworkType.UNMETERED）、existingWorkPolicy 防重、指数退避。
3. **attribution tag**：四个 tag（sync_photo/voice_transcribe/event_aggregate/profile_fetch）落到每个任务（Android 16 扩展归因 API 要求，架构期设计）。
4. **2h 定时兜底**：WorkManager PeriodicWork（2h，接 Agent H 的同步逻辑入口；接口约定写进完成消息）。
5. **K-2 ASR 适配器抽象**（低优先）：后端 asr.py 通道字典 → 配置化 max_duration（登记给集成 Agent 或直接小改，视 Wave 4 时 asr.py 状态）。

## Dependencies
- Agent H（Wave 3）的 sync 客户端入口（定时任务调用点）
- 真机 nova 11（前台服务/WorkManager 行为验证：灭屏、杀进程恢复）
- Android SDK 环境（HBuilderX 自定义基座）

## DoD
1. HBuilderX 编译通过；真机冒烟：录音时前台服务（microphone）、后台同步（dataSync）、杀进程后 WorkManager 恢复、attribution 面板可查（Android 16 模拟器或厂商实现）。
2. 更新 .cowork-temp/audit_B5a_B5d_voice.md 状态列（B5d 段）。
3. 完成消息：文件清单 + 真机验证结果 + 与 H 的接口约定。

## Integration
分支 `wave4-agentK`；与 J/L 并行（插件文件域不重叠）；merge 后客户端编译验证；后台行为属真机验收项，标"待峰宝 nova 11"。
