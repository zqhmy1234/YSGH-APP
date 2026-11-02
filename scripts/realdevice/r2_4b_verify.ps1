# R2 · 4b 修复批次真机复验电池组（自动化段 + 人工段清单）
# 用法: pwsh -File r2_4b_verify.ps1 [-Stage autopsy|install|backend|logs|manual] [-Port 8010]
#       Windows PowerShell 5.1 需加 -ExecutionPolicy Bypass（默认策略禁用 .ps1）；
#       ⚠️ 5.1 读「无 BOM 的 UTF-8 .ps1」会按 ANSI 解码 → 中文乱码 → 解析报错。本文件已加 UTF-8 BOM 规避。
# 约定: 证据一律落 scripts/realdevice/evidence/，命名 r2_<项>_<时间戳>（证据三要素：设备+时间/文件/判定）
#
# ⚠️ 2026-10-11 迁址与端口校正（开发 C）：
#   1) 原脚本写死 D:\GuangH-App\.wt\fix4b\...，该目录已不存在（项目现于 D:\projects\YSGH-APP）。
#      现按脚本自身位置推导仓库根 —— 仓库再搬家不用改脚本。
#   2) 端口 8000 → 8010：8000 会被 HBuilderX launcher 的 httpServer 抢占（对任何路径返 HTTP 200
#      + 裸字符串 "404"），客户端守卫 resolve(null) 表现为"接口全空但不报错"。必须与
#      client/utils/config.uts 的 8010 一致，否则 adb reverse 隧道端口对不上。
#   3) 复验基线用内容判定，不要引用提交哈希：4b 批次那批哈希（8cb15b4/f1f8a3c/6f43e87/
#      97eb795/381a210/2a0194b）在本仓库不可解析。基线记录写「分支 + 内容证据」：
#      client/AndroidManifest.xml 含 DataSyncService + FOREGROUND_SERVICE*；yishu-background-tasks
#      用 Class.forName 探测；getBaseUrl() 走 8010。
param(
    [string]$Apk = "",
    [string]$Serial = "DKS9K23526028855",
    [string]$Stage = "autopsy",
    [int]$Port = 8010
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # <repo>\scripts\realdevice → <repo>
$ev = Join-Path $PSScriptRoot "evidence"
if (-not (Test-Path $ev)) { New-Item -ItemType Directory -Path $ev | Out-Null }
if ([string]::IsNullOrWhiteSpace($Apk)) {
    $Apk = Join-Path $RepoRoot "client\unpackage\debug\android_debug_vapor.apk"
}
$BackendDir = Join-Path $RepoRoot "backend"

# aapt（autopsy 段依赖）：不在 PATH 时从 Android SDK build-tools 兜底
# （HBuilderX 自带的 adb 目录里没有 aapt，实测 `aapt` 直接调用会 CommandNotFound）
if (-not (Get-Command aapt -ErrorAction SilentlyContinue)) {
    $sdkBt = Join-Path $env:LOCALAPPDATA 'Android\Sdk\build-tools'
    if (Test-Path $sdkBt) {
        $aaptExe = Get-ChildItem -LiteralPath $sdkBt -Recurse -File -Filter 'aapt.exe' -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | Select-Object -First 1
        if ($aaptExe) {
            $env:PATH = (Split-Path -Parent $aaptExe.FullName) + ';' + $env:PATH
            Write-Host "[环境] aapt 取自 $($aaptExe.FullName)" -ForegroundColor DarkGray
        } else {
            Write-Host "[环境] 未找到 aapt：autopsy 段会失败，请装 Android SDK build-tools" -ForegroundColor Yellow
        }
    }
}
$ts = Get-Date -Format "yyyyMMdd_HHmmss"

function Step($name, $body) {
    Write-Host "`n=== [$name] ===" -ForegroundColor Cyan
    & $body
}

switch ($Stage) {
"autopsy" {
    # D-19: FGS service 必须出现在包 manifest（工程根 AndroidManifest.xml 合并点）
    if (-not (Test-Path $Apk)) { throw "包不在: $Apk（云打包未完成？）" }
    $mt = aapt dump xmltree $Apk AndroidManifest.xml 2>$null
    Step "D-19 DataSyncService 注册" {
        $hit = $mt | Select-String "DataSyncService"
        if ($hit) { Write-Host "PASS: $hit" } else { Write-Host "FAIL: manifest 无 service——R1-a 工程根 manifest 未生效，检查云打包是否吞并（ask.dcloud 214927）" -ForegroundColor Red }
    }
    Step "D-19 FGS 权限" {
        $p = $mt | Select-String "FOREGROUND_SERVICE"
        if ($p) { Write-Host "PASS"; $p | Select-Object -First 3 } else { Write-Host "FAIL: 无 FOREGROUND_SERVICE* 权限" -ForegroundColor Red }
    }
    # D-18: 探针实现已从 marker asset 改为 Class.forName（f1a2843，真机实证）——
    #   旧的 assets/yishu/workmanager-marker.txt 检查会误报 FAIL（该资产已不再需要）。
    #   现口径 = 被探测的类必须真在本包 dex 里，Class.forName 才可能成功。
    Step "D-18 BgTaskManager 在包内 dex" {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $needle = "BgTaskManager"
        $foundIn = @()
        $zip = [System.IO.Compression.ZipFile]::OpenRead($Apk)
        try {
            foreach ($e in $zip.Entries) {
                if ($e.FullName -notmatch '^classes\d*\.dex$') { continue }
                $ms = New-Object System.IO.MemoryStream
                $s = $e.Open(); $s.CopyTo($ms); $s.Close()
                $ascii = [System.Text.Encoding]::ASCII.GetString($ms.ToArray())
                if ($ascii.Contains($needle)) { $foundIn += $e.FullName }
            }
        } finally { $zip.Dispose() }
        if ($foundIn.Count -gt 0) { Write-Host "PASS: $needle ∈ $($foundIn -join ',')" }
        else { Write-Host "FAIL: dex 内无 $needle——Class.forName 探测必然降级" -ForegroundColor Red }
    }
    # 包体积记录（字体 26.4MB 在包内的实测代价，给用户拍字体去向用）
    Step "包体积" { [math]::Round((Get-Item $Apk).Length / 1MB, 1) }
}
"install" {
    Step "设备在场" {
        $d = adb devices | Select-String $Serial
        if (-not $d) { throw "设备不在场——先让峰宝重插 USB/确认授权（adb server 互杀已排除）" }
        Write-Host "PASS"
    }
    Write-Host "[人工] 手机侧确认 EMUI 纯净模式已关（设置→安全），否则 install 静默失败零报错" -ForegroundColor Yellow
    Step "安装" { adb -s $Serial install -r $Apk }
    Step "reverse 三连" {
        adb -s $Serial reverse tcp:$Port tcp:$Port
        $h = adb -s $Serial shell "curl -s -m 3 http://127.0.0.1:$Port/healthz"
        if ($h -match '"status"\s*:\s*"ok"') { Write-Host "PASS: $h" } else { Write-Host "FAIL: 设备侧 healthz 不通（后端起了吗？跑 -Stage backend）" -ForegroundColor Red }
    }
    Step "屏幕常亮" { adb -s $Serial shell "svc power stayon usb; input keyevent KEYCODE_WAKEUP" }
}
"backend" {
    # fix4b 后端（与被测代码同源）+ dev worker（Windows 无 scheduler 形态，见 handoff 坑账）
    Write-Host "[提示] 与媒体窗共用 $Port 端口前先打招呼：$((Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count) 个监听者已在"
    # 需在已 activate YSG 的会话里跑（python 解析走该 conda 环境），并先设 $env:NO_PROXY='localhost,127.0.0.1,::1'
    Start-Process -WorkingDirectory $BackendDir `
        -FilePath python -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","$Port" -WindowStyle Minimized
    Start-Process -WorkingDirectory $BackendDir `
        -FilePath python -ArgumentList "-c","from app.workers.worker import get_worker_class; from app.core.queue import QUEUE_HIGH, QUEUE_LOW, get_queue; import logging; logging.basicConfig(level=logging.INFO); w=get_worker_class()([get_queue(QUEUE_HIGH),get_queue(QUEUE_LOW)]); w.work()" -WindowStyle Minimized
    Start-Sleep -Seconds 6
    (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/healthz").Content
}
"logs" {
    adb -s $Serial logcat -d -t 400 | Select-String "yishu" | Tee-Object "$ev\r2_logcat_$ts.log"
}
"manual" {
    # 逐项验收口径（tracker19 §4 行），每条 PASS 需 evidence 三要素
    # 覆盖核对（2026-10-11 开发 C）：C-1 要求关 D-15/16/18/19/21，其中 D-15 原先缺口径；
    # 另补 D-10/D-12（同批已落地待复验但原清单漏列）。
    @(
      "D-18/19  logcat 见 'initBackgroundTasks ok' + 周期任务登记；前台服务通知在场（会话 B）；云包另跑 autopsy 核对 manifest service/权限",
      "D-16     无情绪语音→chip 不渲染；主导情绪无 null 空提示；(服务端)GET /api/v1/asr/channels 两通道布尔",
      "D-15     L1 卡片落库 title_source='device'（设备侧写入口径）；用户真编辑走服务端 edit 仍标 'user'",
      "D-07     <4min 录音入库→contents done 且 COS voice/ 有对象；详情页可回放；用户改过的转写文本不被管线覆盖",
      "D-07b    4~5min 录音（8MB 黑洞段）→走分片持久化不再静默 null",
      "D-08     断网停止录音→重试条在场→杀 App 重启→仍找回'发现未转写录音'→恢复网络重试成功并入队",
      "D-10     后端不可达 / 返回非 JSON → App 不崩（ClassCastException 消失）且走可读失败分支",
      "D-12     EXIF 缺失照片导入 → logcat 见时间回退两档 warn（EXIF 缺→DATE_TAKEN 可能扫描污染 / →DATE_ADDED 兜底）",
      "D-22     文本记录→点任一标签→立即 toast '已纠正'；DB: correction_log 该行 new_label=所点标签（非 mixed）；contents.content_class 同步改；再输同文本→分类直接命中个人层（layer=personal）；toast 若现 '模型参考' 不得改变已点标签",
      "D-05/14  蜂窝注入照片→held 计数=实数（无双登记）→横幅'立即上传原图'→成功后时间轴出现 L1 事件（contents 有、事件再无孤儿）",
      "D-21     manage/index/messages/interview 四页真机可滚动到底",
      "gap目验  ⚠️ 原引用 docs/audit_20260831 报告 §四 P0 清单——该文件在本仓库不存在（悬空引用，待确认已删还是未入库）",
      "US-25    第三次纠错交互复判（D4 弹窗缺席现状，U3 拍板依据）"
    ) | ForEach-Object { Write-Host "[ ] $_" -ForegroundColor Yellow }
    Write-Host "`n证据: adb -s $Serial exec-out screencap -p > $ev\r2_<项>_$ts.png ；日志 -Stage logs 自动落盘"
}
}
