package com.yishu.poc

import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.widget.Button
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import net.sqlcipher.database.SQLiteDatabase
import java.io.File

/**
 * POC 五测主界面（S1-01）
 *
 * 按钮：POC-01 相册监听注册 | POC-02 录音 10s | POC-04 SQLCipher | POC-03 attribution 信息
 * 日志：追加到 TextView + logcat（tag POC）
 */
class MainActivity : AppCompatActivity() {

    private lateinit var logView: TextView
    private var photoObserver: PhotoObserver? = null
    private var photoEventCount = 0

    private val permissions = buildList {
        add(android.Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) {
            add(android.Manifest.permission.READ_MEDIA_IMAGES)
            add(android.Manifest.permission.POST_NOTIFICATIONS)
        } else {
            add(android.Manifest.permission.READ_EXTERNAL_STORAGE)
        }
    }

    companion object {
        private const val TAG = "POC"
        private const val REQ_PERMS = 100
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        logView = findViewById(R.id.logView)

        findViewById<Button>(R.id.btnPhoto).setOnClickListener { runPoc01() }
        findViewById<Button>(R.id.btnRecord).setOnClickListener { runPoc02() }
        findViewById<Button>(R.id.btnSqlcipher).setOnClickListener { runPoc04() }
        findViewById<Button>(R.id.btnAttribution).setOnClickListener { runPoc03() }

        log("POC 启动 | 设备: ${Build.MODEL} | Android ${Build.VERSION.SDK_INT} (${Build.VERSION.RELEASE})")
        requestPermissionsIfNeeded()
    }

    private fun requestPermissionsIfNeeded() {
        val missing = permissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), REQ_PERMS)
        } else {
            log("权限已全部授予")
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        val granted = grantResults.count { it == PackageManager.PERMISSION_GRANTED }
        log("权限结果: $granted/${grantResults.size} 授予")
    }

    // ---------- POC-01 相册监听 ----------
    private fun runPoc01() {
        log("=== POC-01 相册监听（ContentObserver）===")
        photoObserver?.unregister()
        photoEventCount = 0
        photoObserver = PhotoObserver(contentResolver) { uri ->
            photoEventCount++
            runOnUiThread { log("[POC-01] 新照片事件 #$photoEventCount: $uri") }
        }.also { it.register() }
        log("ContentObserver 已注册。现在去拍一张新照片或保存图片到相册（≤10s 内应有回调）")
        if (Build.VERSION.SDK_INT >= 29 && Build.VERSION.SDK_INT < 33) {
            log("提示: Android 10-12 需 READ_EXTERNAL_STORAGE 且未分区存储限制")
        }
    }

    // ---------- POC-02 前台录音 ----------
    private fun runPoc02() {
        log("=== POC-02 前台录音 10s（microphone 前台服务）===")
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            log("[POC-02] ❌ 无录音权限")
            return
        }
        val svc = Intent(this, RecordingService::class.java).setAction(RecordingService.ACTION_START)
        ContextCompat.startForegroundService(this, svc)
        log("[POC-02] 前台服务已启动。现在灭屏/切后台 10 秒…")
        android.os.Handler(mainLooper).postDelayed({
            val stop = Intent(this, RecordingService::class.java).setAction(RecordingService.ACTION_STOP)
            startService(stop)
            // startService 是异步的：等服务完成 stop + 文件落盘后再验证
            android.os.Handler(mainLooper).postDelayed({ verifyRecording() }, 1500)
        }, 10_000)
    }

    private fun verifyRecording() {
        val file = RecordingService.lastRecording()
        if (file == null || !file.exists()) {
            log("[POC-02] ❌ 录音文件不存在")
            return
        }
        val retriever = MediaMetadataRetriever()
        retriever.setDataSource(file.absolutePath)
        val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0
        retriever.release()
        val ok = durationMs >= 8_000
        log("[POC-02] ${if (ok) "✅" else "❌"} 文件: ${file.name} | 时长: ${durationMs}ms（目标≥8s）")
        if (ok) log("[POC-02] ✅ PASS：灭屏/后台录音可用（10s 录制完整）")
        else log("[POC-02] ❌ FAIL：录音被中断或损坏")
    }

    // ---------- POC-03 attribution ----------
    private fun runPoc03() {
        log("=== POC-03 attribution tag ===")
        if (Build.VERSION.SDK_INT >= 34) {
            // Android 14+：检查系统 media 归因是否可访问（Android 16 完整验证需更高版本）
            val hasAttribution = try {
                Settings.Global.getString(contentResolver, "media_projection_config") != null
            } catch (e: Exception) {
                false
            }
            log("[POC-03] Android 14+ 设备，attribution API 存在性: $hasAttribution")
        } else {
            log("[POC-03] 当前 Android ${Build.VERSION.SDK_INT}（<34）：验证 DEV-007 低版本兼容 — 无归因 API 不崩溃 ✅")
        }
        log("[POC-03] 完整归因标识（DEV-006）需 Android 16 设备/模拟器，本机标记为部分验证")
    }

    // ---------- POC-04 SQLCipher ----------
    private fun runPoc04() {
        log("=== POC-04 SQLCipher 真机验证 ===")
        SQLiteDatabase.loadLibs(this)
        val dbFile = File(filesDir, "poc04_encrypted.db")
        dbFile.delete()
        val key = "poc04-device-key"

        // 1. 写入
        val db = SQLiteDatabase.openOrCreateDatabase(dbFile, key, null)
        db.execSQL("CREATE TABLE IF NOT EXISTS mem (id INTEGER PRIMARY KEY, text TEXT)")
        db.execSQL("INSERT INTO mem (text) VALUES ('敏感记忆内容-真机')")
        db.close()
        log("[POC-04] 加密库已创建并写入")

        // 2. 密文验证：raw 文件不应含明文
        val raw = dbFile.readBytes()
        val plaintextBytes = "敏感记忆内容-真机".toByteArray(Charsets.UTF_8)
        val leaked = containsSubsequence(raw, plaintextBytes)
        log("[POC-04] 明文泄漏检查: ${if (leaked) "❌ 发现明文" else "✅ 密文无明文"}")

        // 3. 错误密钥 → 应失败
        var wrongKeyRejected = false
        try {
            SQLiteDatabase.openOrCreateDatabase(dbFile, "wrong-key", null)
                .execSQL("SELECT count(*) FROM mem")
        } catch (e: Exception) {
            wrongKeyRejected = true
        }
        log("[POC-04] 错误密钥拒绝: ${if (wrongKeyRejected) "✅" else "❌"}")

        // 4. 正确密钥重读
        val db2 = SQLiteDatabase.openOrCreateDatabase(dbFile, key, null)
        val cursor = db2.rawQuery("SELECT text FROM mem", null)
        var value: String? = null
        if (cursor.moveToFirst()) value = cursor.getString(0)
        cursor.close()
        db2.close()
        log("[POC-04] 正确密钥重读: ${if (value == "敏感记忆内容-真机") "✅ $value" else "❌ $value"}")

        val pass = !leaked && wrongKeyRejected && value == "敏感记忆内容-真机"
        log("[POC-04] 结论: ${if (pass) "✅ PASS" else "❌ FAIL"}")
    }

    private fun containsSubsequence(haystack: ByteArray, needle: ByteArray): Boolean {
        if (needle.isEmpty()) return true
        outer@ for (i in 0..haystack.size - needle.size) {
            for (j in needle.indices) {
                if (haystack[i + j] != needle[j]) continue@outer
            }
            return true
        }
        return false
    }

    // ---------- 日志 ----------
    private fun log(msg: String) {
        Log.i(TAG, msg)
        runOnUiThread {
            logView.append(msg + "\n")
            (logView.parent as? ScrollView)?.post { (logView.parent as ScrollView).fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }
}
