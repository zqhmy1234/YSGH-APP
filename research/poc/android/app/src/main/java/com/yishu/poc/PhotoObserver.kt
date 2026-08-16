package com.yishu.poc

import android.content.ContentResolver
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore

/**
 * POC-01 相册监听验证（ContentObserver）
 *
 * 验证目标（DEV-001/002）：
 *  1. 新照片到达触发导入（≤10s）
 *  2. App 被杀重启后自动重挂
 *
 * 判定：回调计数 > 0 即 PASS。
 */
class PhotoObserver(
    private val resolver: ContentResolver,
    private val onNewPhoto: (String) -> Unit,
) : ContentObserver(Handler(Looper.getMainLooper())) {

    private val collectionUri: Uri = if (android.os.Build.VERSION.SDK_INT >= 29) {
        MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
    } else {
        MediaStore.Images.Media.EXTERNAL_CONTENT_URI
    }

    override fun onChange(selfChange: Boolean, uri: Uri?) {
        val path = uri?.lastPathSegment ?: "unknown"
        onNewPhoto(path)
    }

    fun register() {
        resolver.registerContentObserver(collectionUri, true, this)
    }

    fun unregister() {
        resolver.unregisterContentObserver(this)
    }
}
