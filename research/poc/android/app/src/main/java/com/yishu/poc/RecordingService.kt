package com.yishu.poc

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.Environment
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * POC-02 前台录音验证（foregroundServiceType=microphone）
 *
 * 验证目标（DEV-003/010）：
 *  1. 灭屏/切后台录音持续
 *  2. 前台服务常驻通知可见
 *  3. 中断恢复状态机（B5-d-3）
 *
 * 判定：录制 10 秒后文件可播放且时长≈录制时长即 PASS。
 */
class RecordingService : Service() {

    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null
    private var isRecording = false

    companion object {
        private const val TAG = "POC02-Recording"
        private const val CHANNEL_ID = "poc_recording"
        const val ACTION_START = "com.yishu.poc.START"
        const val ACTION_STOP = "com.yishu.poc.STOP"
        private var lastFile: File? = null

        fun lastRecording(): File? = lastFile
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        lastFile = null
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startRecording()
            ACTION_STOP -> {
                stopRecording()
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID, "录音中", NotificationManager.IMPORTANCE_LOW
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun startRecording() {
        val file = File(
            getExternalFilesDir(Environment.DIRECTORY_MUSIC),
            "poc02_${SimpleDateFormat("HHmmss", Locale.US).format(Date())}.m4a"
        )
        recorder = MediaRecorder().apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioEncodingBitRate(128_000)
            setAudioSamplingRate(44_100)
            setOutputFile(file.absolutePath)
            prepare()
            start()
        }
        outputFile = file
        isRecording = true
        Log.i(TAG, "录音开始: ${file.absolutePath}")

        // 前台服务通知（microphone 类型）
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("忆述POC · 录音中")
            .setContentText("前台服务验证（POC-02）")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1001, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(1001, notification)
        }
    }

    private fun stopRecording() {
        if (!isRecording) return
        try {
            recorder?.stop()
        } catch (e: RuntimeException) {
            Log.e(TAG, "录音停止异常（时长过短？）: ${e.message}")
        }
        recorder?.release()
        recorder = null
        isRecording = false
        lastFile = outputFile
        Log.i(TAG, "录音结束: ${outputFile?.absolutePath}")
    }

    override fun onDestroy() {
        stopRecording()
        super.onDestroy()
    }
}
