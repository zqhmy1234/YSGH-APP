/**
 * 忆述光华 · 后台任务队列（WorkManager）原生 Kotlin 实现（B5d · Wave4 Agent K）
 *
 * 为什么原生 Kotlin + libs jar（而非 UTS 静态引用）：
 *  UTS 编译器对第三方库类型（androidx.work）的 .d.ts 依赖 HBuilderX 的
 *  config.json 依赖下载机制（gradle + Jars2DtsGenerator）；该机制在部分环境
 *  （CLI 编译 / .wt worktree / CI）不生效导致 error18。libs/ 目录是纯文件机制：
 *  resolveLibs 直接把 libs/*.jar 加入 kotlinc classpath，任何环境可编译。
 *  注意：标准基座运行流程不编译原生 .kt（仅自定义基座/云打包编译），
 *  UTS 侧以 Class.forName 探测本类是否存在，不存在时安全降级。
 *
 * 分工：
 *  - BgTaskWorker：WorkManager 到点唤醒执行器——读任务类型 → 写 pending + 唤醒时间
 *    （SharedPreferences，与 UTS 侧共用同一 key；UTS 层启动/前台 drain 消费执行）
 *  - BgTaskManager：全部 WorkManager 调度逻辑（P0-P4 优先级链 / WiFi 约束 /
 *    指数退避 / existingWorkPolicy 防重 / attribution tag / 2h 周期），
 *    UTS 侧通过 java.lang.reflect 调用其 @JvmStatic 方法（参数均为基础类型）。
 *
 * 优先级表（B5d §5）与 attribution tag（B5d §3）：
 *  P0 voice_transcribe（voice_transcribe）→ 独立唯一名，无网络约束（用户等待）
 *  P1 sync_photo（sync_photo）→ 独立唯一名 + UNMETERED
 *  P2 event_aggregate（event_aggregate）/ P3 profile_fetch（profile_fetch）/
 *  P4 batch_import（sync_photo）→ 低优先共享档 + UNMETERED，各类型独立唯一名防误去重
 */
package uni.UNIYISHU001

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.ListenableWorker
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.PeriodicWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/** 后台任务共享常量（与 index.uts 约定一致） */
object BgTaskPrefs {
    const val PREFS = "yishu_bg_tasks"
    const val PENDING_KEY = "pending_queue"
    const val WAKEUP_KEY = "last_wakeup"
}

/**
 * WorkManager 任务执行器：读 Data 中 task_type/attribution_tag →
 * 记唤醒时间 + 记 pending（应用层下次启动/前台 drain 执行）。
 * 进程被 WorkManager 冷启动时 UTS 层未就绪，两段式保证不丢任务。
 */
class BgTaskWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): ListenableWorker.Result {
        var taskType = ""
        var attributionTag = ""
        try {
            val input = inputData
            if (input != null) {
                taskType = input.getString("task_type") ?: ""
                attributionTag = input.getString("attribution_tag") ?: ""
            }
        } catch (_: Throwable) {
            taskType = ""
        }
        if (taskType.isEmpty()) taskType = "sync"

        val appContext = applicationContext
        val prefs = appContext.getSharedPreferences(BgTaskPrefs.PREFS, Context.MODE_PRIVATE)

        // 唤醒记录（本地时间 ISO8601，供 UI/调试）
        val now = java.util.Date()
        @Suppress("DEPRECATION")
        val iso = String.format(
            "%04d-%02d-%02dT%02d:%02d:%02d+08:00",
            now.year + 1900, now.month + 1, now.date, now.hours, now.minutes, now.seconds
        )
        prefs.edit().putString(BgTaskPrefs.WAKEUP_KEY, iso).apply()
        Log.i("yishu", "WorkManager 唤醒 taskType=$taskType tag=$attributionTag")

        // 记 pending：应用层启动/前台/注册 handler 时 drain 执行
        val lines = (prefs.getString(BgTaskPrefs.PENDING_KEY, "") ?: "")
            .split("\n").filter { it.isNotEmpty() }.toMutableList()
        lines.add(taskType)
        prefs.edit().putString(BgTaskPrefs.PENDING_KEY, lines.joinToString("\n")).apply()
        Log.i("yishu", "后台任务 pending 记录: $taskType")

        return ListenableWorker.Result.success()
    }
}

/** WorkManager 调度入口（UTS 反射调用；参数全为基础类型，避免跨语言类型问题） */
object BgTaskManager {

    private const val TAG = "yishu"

    // 唯一任务名：P0/P1 独立；P2-P4 各类型独立（同档低优先共享调度资源，互不抢占 P0/P1）
    private const val NAME_VOICE = "yishu_voice_transcribe"
    private const val NAME_PHOTO = "yishu_sync_photo"
    private const val NAME_AGGREGATE = "yishu_event_aggregate"
    private const val NAME_PROFILE = "yishu_profile_fetch"
    private const val NAME_BATCH = "yishu_batch_import"
    private const val NAME_PERIODIC = "yishu_periodic_sync"

    // attribution tag（B5d §3 定稿四 tag；P4 批量归 sync_photo 域）
    private const val TAG_VOICE = "voice_transcribe"
    private const val TAG_PHOTO = "sync_photo"
    private const val TAG_AGGREGATE = "event_aggregate"
    private const val TAG_PROFILE = "profile_fetch"

    @Volatile
    private var appContext: Context? = null

    /** UTS 层模块加载时注入 Application Context（反射调用，参数 Context） */
    @JvmStatic
    fun init(context: Context) {
        appContext = context.applicationContext
        Log.i(TAG, "BgTaskManager.init ok")
    }

    private fun ctx(): Context? = appContext

    /** androidx.work 是否可用（自定义基座 true / 标准基座 false） */
    @JvmStatic
    fun isAvailable(): Boolean = try {
        Class.forName("androidx.work.WorkManager")
        true
    } catch (_: Throwable) {
        false
    }

    /** 2h 周期兜底（唯一名 KEEP + WiFi 约束 + sync_photo tag；幂等） */
    @JvmStatic
    fun initPeriodic(hours: Int) {
        val context = ctx() ?: return
        if (!isAvailable()) {
            Log.i(TAG, "WorkManager 不可用（标准基座），周期同步跳过")
            return
        }
        try {
            val h = if (hours > 0) hours else 2
            val periodic = PeriodicWorkRequest.Builder(BgTaskWorker::class.java, h.toLong(), TimeUnit.HOURS)
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.UNMETERED)
                .build()
            periodic.setConstraints(constraints)
            periodic.addTag(TAG_PHOTO)
            periodic.setInputData(
                Data.Builder()
                    .putString("task_type", "sync")
                    .putString("attribution_tag", TAG_PHOTO)
                    .build()
            )
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(NAME_PERIODIC, ExistingWorkPolicy.KEEP, periodic.build())
            Log.i(TAG, "2h 周期后台同步已注册（tag=$TAG_PHOTO）")
        } catch (t: Throwable) {
            Log.e(TAG, "initPeriodic 失败", t)
        }
    }

    /** 一次性任务入队（按 taskType 推导优先级 + tag；唯一名 KEEP 防重；指数退避） */
    @JvmStatic
    fun enqueueTask(taskType: String) {
        val context = ctx() ?: return
        if (!isAvailable()) {
            return
        }
        try {
            when (taskType) {
                // P0 语音：独立唯一名，无网络约束（蜂窝也立即执行，用户等待）
                "voice_transcribe" -> enqueueUnique(context, NAME_VOICE, buildRequest(taskType, TAG_VOICE, unmetered = false, backoffSec = 30))
                // P1 新照片：独立唯一名 + WiFi（流量约束另有 uploader 蜂窝缩略图兜底）
                "sync_photo" -> enqueueUnique(context, NAME_PHOTO, buildRequest(taskType, TAG_PHOTO, unmetered = true, backoffSec = 60))
                // P2-P4：低优先共享档
                "event_aggregate" -> enqueueUnique(context, NAME_AGGREGATE, buildRequest(taskType, TAG_AGGREGATE, unmetered = true, backoffSec = 60))
                "profile_fetch" -> enqueueUnique(context, NAME_PROFILE, buildRequest(taskType, TAG_PROFILE, unmetered = true, backoffSec = 60))
                "batch_import" -> enqueueUnique(context, NAME_BATCH, buildRequest(taskType, TAG_PHOTO, unmetered = true, backoffSec = 60))
                else -> enqueueUnique(context, NAME_PHOTO, buildRequest(taskType, TAG_PHOTO, unmetered = true, backoffSec = 60))
            }
        } catch (t: Throwable) {
            Log.e(TAG, "enqueueTask($taskType) 失败", t)
        }
    }

    private fun buildRequest(taskType: String, tag: String, unmetered: Boolean, backoffSec: Long): OneTimeWorkRequest {
        val builder = OneTimeWorkRequest.Builder(BgTaskWorker::class.java)
        builder.setInputData(
            Data.Builder()
                .putString("task_type", taskType)
                .putString("attribution_tag", tag)
                .build()
        )
        builder.addTag(tag)
        if (unmetered) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.UNMETERED)
                .build()
            builder.setConstraints(constraints)
        }
        builder.setBackoffCriteria(BackoffPolicy.EXPONENTIAL, backoffSec, TimeUnit.SECONDS)
        return builder.build()
    }

    private fun enqueueUnique(context: Context, name: String, request: OneTimeWorkRequest) {
        WorkManager.getInstance(context).enqueueUniqueWork(name, ExistingWorkPolicy.KEEP, request)
    }
}
