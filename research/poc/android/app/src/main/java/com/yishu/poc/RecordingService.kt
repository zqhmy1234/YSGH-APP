package com.yishu.poc

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * POC-02 前台录音验证（foregroundServiceType=microphone）—— Wave4 AgentJ 按 wav 16k 契约重做
 *
 * 契约（对齐后端 ASR：`backend/app/services/external/asr.py`）：WAV / 16kHz / 16bit / 单声道
 * （MediaRecorder 无法直出 wav → 用 AudioRecord 采 PCM + 手写 WAV 头）。
 *
 * 中断状态机（B5-a/J-7，对齐客户端 UTS 插件 yishu-recorder）：
 *   IDLE → RECORDING（AudioRecord.startRecording）
 *   RECORDING → INTERRUPTED（来电/系统抢占 → 自动 pause；shouldResume=true 自动恢复）
 *   INTERRUPTED/PAUSED → RECORDING（resume；AudioRecord.pause()/resume()，API 24+）
 *   30min 自动结束（Handler 定时 → 自动 stop + 落盘分段保存，可继续录下一段）
 *   精确计时排除暂停/中断时段
 *
 * 动作：ACTION_START / ACTION_STOP（MainActivity 兼容）/ ACTION_PAUSE / ACTION_RESUME /
 *       ACTION_SET_SHOULD_RESUME（extra "should_resume" 布尔）
 * 验证：录制后文件 WAV 头可读、时长 ≈ 实际录音时长即 PASS。
 */
class RecordingService : Service() {

    private var recorder: AudioRecord? = null
    private var outputFile: File? = null
    private var outStream: FileOutputStream? = null

    // ---- 状态机 ----
    private var state = "idle"            // idle / recording / interrupted / paused
    private var shouldResume = true        // 来电/闹钟中断后自动恢复
    private var sessionStartMs = 0L
    private var recordedAccumMs = 0L       // 已录音时长（排除暂停/中断）
    private var pauseStartMs = 0L
    private var pcmFrames = 0L             // 已写 PCM 帧数（16bit 单声道 → WAV 头字节=帧数*2）

    private val mainHandler = Handler(Looper.getMainLooper())
    private val autoStop = Runnable { autoStop() }

    companion object {
        private const val TAG = "POC02-Recording"
        private const val CHANNEL_ID = "poc_recording"
        const val ACTION_START = "com.yishu.poc.START"
        const val ACTION_STOP = "com.yishu.poc.STOP"
        const val ACTION_PAUSE = "com.yishu.poc.PAUSE"
        const val ACTION_RESUME = "com.yishu.poc.RESUME"
        const val ACTION_SET_SHOULD_RESUME = "com.yishu.poc.SET_SHOULD_RESUME"
        const val EXTRA_SHOULD_RESUME = "should_resume"

        // 后端 ASR wav 契约：16kHz / 16bit / 单声道
        const val SAMPLE_RATE = 16000
        const val CHANNELS = 1
        const val AUTO_STOP_MS = 30L * 60 * 1000  // 30min 自动结束

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
            ACTION_PAUSE -> pauseRecording()
            ACTION_RESUME -> resumeRecording()
            ACTION_SET_SHOULD_RESUME -> {
                shouldResume = intent.getBooleanExtra(EXTRA_SHOULD_RESUME, true)
                Log.i(TAG, "shouldResume=$shouldResume")
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

    private fun showForeground(notice: String) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("忆述POC · 录音中")
            .setContentText(notice)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1001, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(1001, notification)
        }
    }

    private fun startRecording() {
        if (state == "recording") return
        val file = File(
            getExternalFilesDir(Environment.DIRECTORY_MUSIC),
            "poc02_${SimpleDateFormat("HHmmss", Locale.US).format(Date())}.wav"
        )
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        val rec = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            (minBuf * 2).coerceAtLeast(minBuf)
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord 初始化失败")
            return
        }
        try {
            // 写 WAV 头占位（16bit 单声道 16kHz），末尾回填真实大小
            outStream = FileOutputStream(file).also {
                it.write(wavHeader(0L))
            }
        } catch (e: IOException) {
            Log.e(TAG, "无法创建 wav 文件: ${e.message}")
            return
        }

        recorder = rec
        outputFile = file
        sessionStartMs = System.currentTimeMillis()
        pauseStartMs = 0L
        pcmFrames = 0L
        recordedAccumMs = 0L
        rec.startRecording()
        state = "recording"
        startDrainLoop()
        mainHandler.postDelayed(autoStop, AUTO_STOP_MS)
        showForeground("WAV 16k 单声道 · 30min 自动结束")
        Log.i(TAG, "录音开始(wav16k): ${file.absolutePath}")
    }

    /** PCM 读取循环（独立线程写文件；AudioRecord 读取与写文件分离避免丢帧） */
    private fun startDrainLoop() {
        Thread {
            val rec = recorder ?: return@Thread
            val out = outStream ?: return@Thread
            val buf = ByteArray(
                AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT) * 2
            )
            try {
                while (state == "recording" || state == "paused" || state == "interrupted") {
                    if (state == "recording") {
                        val n = rec.read(buf, 0, buf.size)
                        if (n > 0) {
                            out.write(buf, 0, n)
                            pcmFrames += n / 2  // 16bit
                        }
                    } else {
                        Thread.sleep(50)
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "PCM 写入异常: ${e.message}")
            }
        }.start()
    }

    /** 来电/闹钟中断：自动 pause（RECORDING → INTERRUPTED），shouldResume 决定是否自动恢复 */
    private fun pauseRecording() {
        if (state != "recording") return
        val rec = recorder ?: return
        recordedAccumMs += System.currentTimeMillis() - sessionStartMs
        pauseStartMs = System.currentTimeMillis()
        try {
            if (Build.VERSION.SDK_INT >= 24) rec.pause() else rec.stop()
        } catch (e: IllegalStateException) {
            Log.e(TAG, "pause 异常: ${e.message}")
        }
        state = "interrupted"
        Log.i(TAG, "录音中断 → INTERRUPTED（shouldResume=$shouldResume）")
        if (shouldResume) {
            mainHandler.postDelayed({ resumeRecording() }, 500)
        }
    }

    /** 恢复录音（INTERRUPTED/PAUSED → RECORDING） */
    private fun resumeRecording() {
        if (state != "interrupted" && state != "paused") return
        val rec = recorder ?: return
        if (Build.VERSION.SDK_INT >= 24) rec.startRecording()
        sessionStartMs = System.currentTimeMillis()
        pauseStartMs = 0L
        state = "recording"
        Log.i(TAG, "录音恢复 → RECORDING")
    }

    /** 30min 自动结束：落盘分段保存（可继续录下一段） */
    private fun autoStop() {
        if (state != "recording") return
        Log.i(TAG, "30min 自动结束，落盘分段")
        stopRecording()
    }

    private fun stopRecording() {
        if (state == "idle") return
        val rec = recorder
        mainHandler.removeCallbacks(autoStop)
        try {
            rec?.stop()
        } catch (e: IllegalStateException) {
            Log.e(TAG, "录音停止异常（时长过短？）: ${e.message}")
        }
        rec?.release()
        recorder = null
        state = "idle"

        // 回填 WAV 头（RIFF 大小 + data 大小）
        val f = outputFile
        if (f != null && f.exists() && f.length() > 44) {
            try {
                RandomAccessFile(f, "rw").use { raf ->
                    val dataBytes = raf.length() - 44
                    raf.seek(4)
                    raf.writeInt(Integer.reverseBytes((dataBytes + 36).toInt()))
                    raf.seek(40)
                    raf.writeInt(Integer.reverseBytes(dataBytes.toInt()))
                }
            } catch (e: IOException) {
                Log.e(TAG, "WAV 头回填失败: ${e.message}")
            }
        }
        outStream?.close()
        outStream = null
        lastFile = f
        val duration = recordedAccumMs + if (pauseStartMs == 0L) System.currentTimeMillis() - sessionStartMs else 0L
        Log.i(TAG, "录音结束: ${f?.absolutePath} 时长=${duration}ms 帧=${pcmFrames}")
    }

    /** 构造 16bit 单声道 16kHz WAV 头（dataSize 先填 0，结束回填） */
    private fun wavHeader(dataSize: Long): ByteArray {
        val header = ByteArray(44)
        header[0] = 'R'.code.toByte(); header[1] = 'I'.code.toByte()
        header[2] = 'F'.code.toByte(); header[3] = 'F'.code.toByte()
        writeIntLE(header, 4, (dataSize + 36).toInt())
        header[8] = 'W'.code.toByte(); header[9] = 'A'.code.toByte()
        header[10] = 'V'.code.toByte(); header[11] = 'E'.code.toByte()
        header[12] = 'f'.code.toByte(); header[13] = 'm'.code.toByte()
        header[14] = 't'.code.toByte(); header[15] = ' '.code.toByte()
        writeIntLE(header, 16, 16)          // fmt chunk size
        writeShortLE(header, 20, 1)         // PCM
        writeShortLE(header, 22, CHANNELS)
        writeIntLE(header, 24, SAMPLE_RATE)
        writeIntLE(header, 28, SAMPLE_RATE * CHANNELS * 2)  // byte rate
        writeShortLE(header, 32, CHANNELS * 2)              // block align
        writeShortLE(header, 34, 16)        // bits per sample
        header[36] = 'd'.code.toByte(); header[37] = 'a'.code.toByte()
        header[38] = 't'.code.toByte(); header[39] = 'a'.code.toByte()
        writeIntLE(header, 40, dataSize.toInt())
        return header
    }

    private fun writeIntLE(b: ByteArray, off: Int, v: Int) {
        b[off] = (v and 0xFF).toByte()
        b[off + 1] = ((v shr 8) and 0xFF).toByte()
        b[off + 2] = ((v shr 16) and 0xFF).toByte()
        b[off + 3] = ((v shr 24) and 0xFF).toByte()
    }

    private fun writeShortLE(b: ByteArray, off: Int, v: Int) {
        b[off] = (v and 0xFF).toByte()
        b[off + 1] = ((v shr 8) and 0xFF).toByte()
    }

    override fun onDestroy() {
        stopRecording()
        super.onDestroy()
    }
}
