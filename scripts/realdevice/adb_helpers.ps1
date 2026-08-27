#Requires -Version 5.1
<#
.SYNOPSIS
  忆述光华 · 真机补验 adb 封装（Wave 3 使用，Agent D1 产出）

.DESCRIPTION
  统一证据命名与采集入口。函数：
    Get-Device    检查设备在线（adb devices），输出在线序列号
    KeepAwake     屏幕常亮（svc power stayon usb，防截图全黑）
    Now           生成 YYYYMMDD_HHMMSS 时间戳（证据命名统一）
    Shot  <ck> <step>      截图 -> evidence/ck<编号>_<step>_<时间戳>.png
    GrabLog <ck> <keyword> [since]  日志 -> evidence/ck<编号>_<keyword>_<时间戳>.log（可带 -T 时间窗）
    Record <ck> <step> [seconds]    录屏 -> evidence/ck<编号>_<step>_<时间戳>.mp4（动态场景）
    Clear-Logcat   清空 logcat（开始新场景前）
    GrabAppLog <ck> [since]  抓 App 主日志（[yishu] 前缀）

.EXAMPLE
  . .\adb_helpers.ps1
  Get-Device
  Shot 01 2a
  GrabLog 01 upload
  Record 02 int 30
#>

# ---- 全局配置 ----
$script:EvidenceDir = Join-Path $PSScriptRoot 'evidence'   # 证据输出目录
$script:AdbExe = $null                                     # 缓存的 adb 路径

# 解析 adb 可执行文件：$env:ADB 优先，其次 PATH，最后常见 SDK 路径
function Get-AdbPath {
    if ($script:AdbExe -and (Test-Path $script:AdbExe)) { return $script:AdbExe }
    if ($env:ADB -and (Test-Path $env:ADB)) { $script:AdbExe = $env:ADB; return $script:AdbExe }
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { $script:AdbExe = $cmd.Source; return $script:AdbExe }
    $candidates = @(
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "$env:ANDROID_HOME\platform-tools\adb.exe",
        "$env:ANDROID_SDK_ROOT\platform-tools\adb.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $script:AdbExe = $c; return $script:AdbExe }
    }
    throw '未找到 adb：请把 platform-tools 加入 PATH，或设置 $env:ADB 指向 adb.exe'
}

function New-EvidenceDir {
    if (-not (Test-Path $script:EvidenceDir)) {
        New-Item -ItemType Directory -Path $script:EvidenceDir -Force | Out-Null
    }
}

# 时间戳：YYYYMMDD_HHMMSS（证据命名统一）
function Now {
    return Get-Date -Format 'yyyyMMdd_HHmmss'
}

# 设备检查：adb devices 在线设备列表
function Get-Device {
    $adb = Get-AdbPath
    $out = & $adb devices
    $out | ForEach-Object { Write-Host $_ }
    $online = @($out | Where-Object { $_ -match '\tdevice$' })
    if ($online.Count -gt 0) {
        $serials = @($online | ForEach-Object { ($_ -split "`t")[0] })
        Write-Host "[OK] 在线设备：$($serials -join ', ')"
        return $serials
    }
    Write-Host "[WARN] 无在线设备——请确认 nova 11 已 USB 调试授权（授权后 adb devices 显示 device 而非 unauthorized）"
    return @()
}

# 截图：Shot <ck> <step> -> evidence/ck<编号>_<step>_<时间戳>.png
# 用 Start-Process 字节直写，避免 PowerShell `>` 文本重定向损坏 PNG 二进制。
function Shot {
    param(
        [Parameter(Mandatory = $true)][string]$Ck,
        [Parameter(Mandatory = $true)][string]$Step
    )
    $adb = Get-AdbPath
    New-EvidenceDir
    $file = Join-Path $script:EvidenceDir ("ck{0}_{1}_{2}.png" -f $Ck, $Step, (Now))
    $argList = @('exec-out', 'screencap', '-p')
    $p = Start-Process -FilePath $adb -ArgumentList $argList -RedirectStandardOutput $file -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -eq 0 -and (Test-Path $file) -and ((Get-Item $file).Length -gt 0)) {
        Write-Host "[OK] 截图 $file（$((Get-Item $file).Length) bytes）"
        return $file
    }
    Write-Host "[WARN] 截图失败/空文件：$file —— 先 KeepAwake（防熄屏黑图）+ 确认设备授权（Get-Device）"
    return $null
}

# 日志：GrabLog <ck> <keyword> [since]
#   adb logcat -d [-T <since>] | Select-String <keyword> > evidence/ck<编号>_<keyword>_<时间戳>.log
# since 传 logcat -T 可识别的起始时间（如 '08-28 10:15:00.000'），留空取全量。
function GrabLog {
    param(
        [Parameter(Mandatory = $true)][string]$Ck,
        [Parameter(Mandatory = $true)][string]$Keyword,
        [string]$Since = ''
    )
    $adb = Get-AdbPath
    New-EvidenceDir
    $safe = $Keyword -replace '[^\w.-]', '_'
    $file = Join-Path $script:EvidenceDir ("ck{0}_{1}_{2}.log" -f $Ck, $safe, (Now))
    if ($Since -ne '') {
        & $adb logcat -d -T $Since | Select-String -Pattern $Keyword | Out-File -FilePath $file -Encoding utf8
    } else {
        & $adb logcat -d | Select-String -Pattern $Keyword | Out-File -FilePath $file -Encoding utf8
    }
    $lineCount = (Get-Content $file -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    Write-Host "[OK] 日志 $file（关键词：$Keyword，命中 $lineCount 行）"
    if ($lineCount -eq 0) { Write-Host "[INFO] 0 命中——检查关键词是否与实现一致，或先 GrabAppLog 看 App 是否在跑" }
    return $file
}

# 录屏：Record <ck> <step> [seconds] —— 录音中断/恢复等动态场景
function Record {
    param(
        [Parameter(Mandatory = $true)][string]$Ck,
        [Parameter(Mandatory = $true)][string]$Step,
        [int]$Seconds = 30
    )
    $adb = Get-AdbPath
    New-EvidenceDir
    $file = Join-Path $script:EvidenceDir ("ck{0}_{1}_{2}.mp4" -f $Ck, $Step, (Now))
    $tmp = '/sdcard/scr_rec.mp4'
    & $adb shell "screenrecord --time-limit $Seconds $tmp" | Out-Null
    & $adb pull $tmp $file | Out-Null
    & $adb shell rm -f $tmp | Out-Null
    if (Test-Path $file) {
        Write-Host "[OK] 录屏 $file"
        return $file
    }
    Write-Host "[WARN] 录屏失败（设备不支持 / 空间不足 / 未授权）"
    return $null
}

# 清空 logcat（开始新场景前调用，保证时间窗干净）
function Clear-Logcat {
    $adb = Get-AdbPath
    & $adb logcat -c | Out-Null
    Write-Host '[OK] logcat 已清空'
}

# 抓 App 主日志（[yishu] 前缀，App 内 console.log 均带此标记）
function GrabAppLog {
    param(
        [Parameter(Mandatory = $true)][string]$Ck,
        [string]$Since = ''
    )
    GrabLog -Ck $Ck -Keyword 'yishu' -Since $Since
}

# 屏幕常亮（USB 期间不熄屏，避免截图全黑 / tap 失效）
function KeepAwake {
    $adb = Get-AdbPath
    & $adb shell svc power stayon usb | Out-Null
    Write-Host '[OK] 屏幕常亮（svc power stayon usb）'
}

# 导出公开函数（Import-Module 场景）；dot-source 时同样生效
Export-ModuleMember -Function Get-Device, KeepAwake, Now, Shot, GrabLog, Record, Clear-Logcat, GrabAppLog
