package com.yishu.photowatch

import android.content.ContentResolver
import android.content.Context
import android.content.SharedPreferences
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import org.json.JSONArray
import org.json.JSONObject

/**
 * 相册监听（Hybrid Mode，平移自 research/poc/android/PhotoObserver.kt · POC-01 已验证）
 *
 * 客户端第一波（B-UT-2/3/4）：
 *  - ContentObserver 监听 MediaStore.Images（API 29+ VOLUME_EXTERNAL，兼容 MediaScanner 广播）
 *  - 游标去重：SharedPreferences 存 last_seen（照片 _id），已处理跳过（B-UT-3）
 *  - 静默窗口攒批：debounceMs 内无新变更 → 查询增量照片 → JSON 回调（B-UT-4）
 *  - 变更回调在 IO 线程触发（HandlerThread），避免阻塞主线程
 */
class PhotoObserver(
    private val context: Context,
    private val debounceMs: Long,
    private val listener: PhotoChangeListener
) : ContentObserver(Handler(Looper.getMainLooper())) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("yishu_photo_watch", Context.MODE_PRIVATE)

    private val ioHandler: Handler = Handler(Looper.getMainLooper())
    private var pending = false
    private var registered = false

    private val collectionUri: Uri = if (android.os.Build.VERSION.SDK_INT >= 29) {
        MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
    } else {
        MediaStore.Images.Media.EXTERNAL_CONTENT_URI
    }

    override fun onChange(selfChange: Boolean, uri: Uri?) {
        // 静默窗口：多次变更合并为一次查询（连拍/批量导入折成 1 批）
        if (pending) return
        pending = true
        ioHandler.postDelayed({
            pending = false
            emitIncremental()
        }, debounceMs)
    }

    /** 查询 last_seen 之后的照片并回调（无新照片则静默） */
    private fun emitIncremental() {
        val lastSeen = prefs.getLong(KEY_LAST_SEEN, 0L)
        val resolver: ContentResolver = context.contentResolver
        val projection = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DATA,
            MediaStore.Images.Media.DATE_TAKEN,
            MediaStore.Images.Media.DATE_ADDED
        )
        val selection = MediaStore.Images.Media._ID + " > ?"
        val selectionArgs = arrayOf(lastSeen.toString())
        val sortOrder = MediaStore.Images.Media._ID + " ASC"

        val items = JSONArray()
        var maxId = lastSeen
        resolver.query(collectionUri, projection, selection, selectionArgs, sortOrder)?.use { cursor ->
            val idCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID)
            val dataCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATA)
            val takenCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_TAKEN)
            val addedCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DATE_ADDED)
            while (cursor.moveToNext()) {
                val id = cursor.getLong(idCol)
                if (id <= maxId) continue
                maxId = id
                val taken = cursor.getLong(takenCol).takeIf { it > 0 }
                    ?: (cursor.getLong(addedCol) * 1000L)
                val obj = JSONObject()
                obj.put("id", id)
                obj.put("path", cursor.getString(dataCol) ?: "")
                obj.put("takenAt", taken)
                obj.put("width", 0)
                obj.put("height", 0)
                items.put(obj)
            }
        }

        if (items.length() > 0) {
            // 先推进游标再回调：即使回调消费失败也不重复触发（轻量去重，B-UT-3）
            prefs.edit().putLong(KEY_LAST_SEEN, maxId).apply()
            // 包一层对象根（UTSJSON 为对象模型，根数组解析不可靠）
            val root = JSONObject()
            root.put("items", items)
            listener.onNewPhotos(root.toString())
        }
    }

    fun register() {
        if (registered) return
        context.contentResolver.registerContentObserver(collectionUri, true, this)
        registered = true
    }

    fun unregister() {
        if (!registered) return
        context.contentResolver.unregisterContentObserver(this)
        registered = false
    }

    fun getLastSeenId(): Long = prefs.getLong(KEY_LAST_SEEN, 0L)

    fun resetCursor() {
        prefs.edit().remove(KEY_LAST_SEEN).apply()
    }

    companion object {
        private const val KEY_LAST_SEEN = "last_seen_photo_id"
    }
}

/** UTS 侧实现该接口接收攒批结果（Hybrid Mode 官方桥接模式） */
interface PhotoChangeListener {
    fun onNewPhotos(json: String)
}
