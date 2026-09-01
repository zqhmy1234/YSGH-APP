// POC-03 helper：枚举 MediaStore.MediaColumns 中 attribution 相关字段
// 用法：push 到设备后通过 app_process 或嵌入 app 运行
package com.yishu.poc

import android.provider.MediaStore
import java.lang.reflect.Modifier

object MediaColumnsInspector {
    fun inspect(): List<String> {
        val results = mutableListOf<String>()
        val clazz = MediaStore.MediaColumns::class.java
        clazz.declaredFields
            .filter { Modifier.isStatic(it.modifiers) && it.type == String::class.java }
            .map { it.name }
            .filter { it.contains("ATTRIB", true) || it.contains("GENERAT", true) || it.contains("ORIGIN", true) }
            .forEach { results.add(it) }
        return results
    }
}
