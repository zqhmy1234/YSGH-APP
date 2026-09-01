# 忆述光华 POC 原生工程 · Gradle Wrapper 脚本（Windows）
@echo off
setlocal
set JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot
call "D:\tools\gradle-8.7\bin\gradle.bat" %*
endlocal
