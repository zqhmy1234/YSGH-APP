package com.yishu.photowatch

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * 安全存储（B-CL-3）：EncryptedSharedPreferences 存 token/敏感值
 *
 * androidx.security:security-crypto（插件 package.json 已声明依赖）。
 * AES256-GCM 加密 + 独立主密钥（MasterKey），DB 级联泄漏时 token 不可读。
 */
class SecurePrefs(context: Context) {

    private val prefs: SharedPreferences = run {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "yishu_secure",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun set(key: String, value: String) {
        prefs.edit().putString(key, value).apply()
    }

    fun get(key: String): String = prefs.getString(key, "") ?: ""

    fun remove(key: String) {
        prefs.edit().remove(key).apply()
    }
}
