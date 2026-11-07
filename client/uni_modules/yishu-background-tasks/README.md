# yishu-background-tasks（B5d · Wave4 Agent K）

后台任务 UTS 插件：单 WorkManager 队列 + 2h 周期同步 + attribution tag，标准基座自动降级。

## 对外 API（`utssdk/app-android/index.uts`）

| 函数 | 说明 |
|---|---|
| `isWorkManagerAvailable(): boolean` | 反射调 `BgTaskManager.isAvailable()`（Kotlin 侧用 `Class.forName` 判断）；标准基座返回 false。⚠️ 2026-09-11 修正：原 `ClassLoader.getResource('...class')` 在 Android 上**恒 null**（类在 dex 不在资源表），导致恒判 false、功能全哑 |
| `initBackgroundTasks(hours: number): void` | 注册 hours 小时周期任务（自定义基座走 PeriodicWork；标准基座退化 setInterval 兜底）。幂等 |
| `setBackgroundTaskHandler(cb: (taskType: string) => void): void` | 设置任务回调（含周期到点与 pending 队列 drain；注册即 drain 一次积压） |
| `enqueueTask(taskType: string): void` | 入队。类型映射：`voice_transcribe`→P0、`sync_photo`→P1、`event_aggregate`→P2、`profile_fetch`→P3、`batch_import`→P4 |
| `drainPendingTask(): string` | 取出并清除最早一条 pending（返回 taskType；无则返回 ''）。单条原语，由内部 drain 循环使用 |
| `drainPendingTasks(): void` | 消费全部 pending：逐条取出 → 派发 handler（逐条 setTimeout(0) 让出主线程，长队列不阻塞 UI；handler 未注册时保留队列不丢） |
| `pendingTaskCount(): number` | pending 队列长度 |
| `lastWakeupAt(): string` | 最近一次任务触发时间（ISO） |

## 任务类型与 attribution tag

| taskType | 优先级 | WiFi 约束 | attribution tag | unique name |
|---|---|---|---|---|
| `voice_transcribe` | P0 | 无 | `voice_transcribe` | yishu_voice_transcribe |
| `sync_photo` | P1 | UNMETERED | `sync_photo` | yishu_sync_photo |
| `event_aggregate` | P2 | 无 | `event_aggregate` | yishu_event_aggregate |
| `profile_fetch` | P3 | 无 | `profile_fetch` | yishu_profile_fetch |
| `batch_import` | P4 | UNMETERED | `sync_photo` | yishu_batch_import |
| `sync`（周期） | — | UNMETERED | `sync_photo` | yishu_periodic_sync |

指数退避：P0 30s、其余 60s（WorkManager 标准退避），`existingWorkPolicy=KEEP` 去重。

## 与 Agent H 的接口契约（已接线 · P0-3 收口）

H 已在 `client/utils/sync_client.ts` `registerBackgroundSync()` 中完成真接线（原注释写的 "yishu-bg-sync" 已过时，插件名为 **yishu-background-tasks**）：

```ts
// sync_client.ts（registerBackgroundSync 内）
import { initBackgroundTasks, setBackgroundTaskHandler, drainPendingTasks } from '@/uni_modules/yishu-background-tasks/utssdk/app-android/index.uts'

initBackgroundTasks(2) // 2h 周期（photo-watch.start() 已引导，幂等可重复调）
setBackgroundTaskHandler((taskType: string) => {
  // 统一走 B4 完整同步链路（sync 周期 / voice_transcribe / sync_photo / 聚合 / 拉取等类型）
  runSyncChain()
})
// App.onShow → sync_client.drainBackgroundTasks() → drainPendingTasks() 消费积压
```

- 自定义基座：WorkManager Worker 写 SharedPreferences pending（key `yishu_bg_tasks`），注册 handler 时 drain + App.onShow 再 drain → 两段式，杀进程不丢任务。
- 标准基座：`enqueueTask` 直接写 pending（同类型去重）；周期兜底由 `sync_client` 的 2h setInterval 完成（App 存活期）。
- 去重：`recordPending`（UTS）与 `BgTaskWorker`（Kotlin）均按 taskType 同类型去重，与 `ExistingWorkPolicy.KEEP` 语义对齐，SharedPreferences 不会无限增长。
- `photo-watch.start()` 已自动调 `initBackgroundTasks(2)`；`setBackgroundTaskHandler` 由 H 注册一次即可。
- sync_photo 的照片续传由 uploader 自身 `onNetworkRestored` 钩子负责（uploader 已依赖 sync_client，此处静态引用会成环，故 handler 不接 continuePendingUploads）。

## 构建说明

- 依赖 androidx.work 2.9.1：`utssdk/app-android/config.json` 的 `dependencies` 声明，由 gradle 解析完整传递闭包（含 Room、含 startup 的 manifest 合并）。2026-09-11 起取代原 libs 手工 jar 堆。
- ⚠️ **勿恢复 `libs/` 手工 jar 堆**：云打包实测 2688 条重复类（`:app:checkReleaseDuplicateClasses`，手工 jar 与 app/云端自带 androidx 整包撞车），且闭包缺 Room ⇒ WorkManager 运行期崩，而 `Class.forName` 探针仍报 True（假通过，比编译失败更隐蔽）。
- 本地若「更新三方依赖」报 `zip file is empty`：根因是本机 gradle 发行版的 `gradle-base-ide-plugins-8.13.jar` 被截断为 0 字节，补齐该文件即可，**与本插件无关**，别据此判定"config.json 机制不生效"。
- 原生 Kotlin（`BgBackground.kt`）仅在自定义基座（云打包）编译；标准基座由 UTS 层探测降级。
