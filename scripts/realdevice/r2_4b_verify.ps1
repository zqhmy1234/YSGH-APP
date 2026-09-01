# R2 · 4b 修复批次真机复验电池组（自动化段 + 人工段清单）
# 用法: powershell -File r2_4b_verify.ps1 [-Stage autopsy|install|backend|logs|manual]
# 约定: 证据一律落 scripts/realdevice/evidence/，命名 r2_<项>_<时间戳>
#       （证据三要素：设备+时间 / 文件 / 判定）
#
# ⚠️ 2026-09-01 修订（相对 develop 上同名脚本的四个修正）：
#   1. adb 用 HBuilderX 自带 1.0.41 真身（adbs/ 根下裸文件），不用 PATH 上的杂版——
#      老 client（1.0.36/1.0.31）连上 41 server 会互杀，reverse 隧道全灭
#   2. aapt 写绝对路径（build-tools 34.0.0 仍有 aapt v1），不依赖 PATH
#   3. evidence 目录指向 fix4b 工作树（被测代码同树），而非主区
#   4. autopsy 段新增「D-19 类名正确性」判据：UTS 插件 Service 的 FQN 必须是
#      uts.sdk.modules.<插件目录名驼峰>.<类名>，uni.UNI<APPID>.* 一律判错
#      （实证：旧包 dex 里 uni/UNI* 命中 0 个，uts/sdk/modules/* 命中 501 个）
param(
    [string]$Apk = "D:\GuangH-App\.wt\fix4b\client\unpackage\debug\android_debug.apk",
    [string]$Serial = "DKS9K23526028855",
    [string]$Stage = "autopsy",
    [string]$Adb  = "D:\HBuilderX\plugins\launcher-tools\tools\adbs\adb.exe",
    [string]$Aapt = "C:\Users\ghf\AppData\Local\Android\Sdk\build-tools\34.0.0\aapt.exe"
)
$ErrorActionPreference = "Stop"
$ev = "D:\GuangH-App\.wt\fix4b\scripts\realdevice\evidence"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"

function Step($name, $body) {
    Write-Host "`n=== [$name] ===" -ForegroundColor Cyan
    & $body
}

switch ($Stage) {

"autopsy" {
    # 无设备亦可跑：只需 APK 落地
    if (-not (Test-Path $Apk)) { throw "包不在: $Apk（云打包未完成？）" }
    $mt = & $Aapt dump xmltree $Apk AndroidManifest.xml 2>$null

    Step "D-19a service 是否出现在包 manifest" {
        $hit = $mt | Select-String "DataSyncService"
        if ($hit) { Write-Host "PASS（合并机制生效）: $hit" }
        else { Write-Host "FAIL: 包 manifest 无 service——工程根 AndroidManifest.xml 未被云打包合并" -ForegroundColor Red }
    }
    Step "D-19b service 类名是否合法（uts.sdk.modules.<驼峰插件名>.*）" {
        $names = $mt | Select-String 'android:name="([^"]*DataSyncService)"' |
                 ForEach-Object { $_.Matches[0].Groups[1].Value }
        if (-not $names) { Write-Host "N/A: 无 service 节点" -ForegroundColor Yellow; return }
        foreach ($n in $names) {
            if ($n -like "uts.sdk.modules.*") { Write-Host "  PASS: $n" -ForegroundColor Green }
            else { Write-Host "  FAIL: $n —— 非 uts.sdk.modules 命名空间，Android 系统无法实例化该组件" -ForegroundColor Red }
        }
    }
    Step "D-19c FGS 权限" {
        $p = & $Aapt dump badging $Apk 2>$null | Select-String "FOREGROUND_SERVICE"
        if ($p) { Write-Host "PASS"; $p | Select-Object -First 5 }
        else { Write-Host "FAIL: 无 FOREGROUND_SERVICE* 权限" -ForegroundColor Red }
    }
    Step "D-18 marker 资产（nativeResources → assets）" {
        $mk = & $Aapt list $Apk 2>$null | Select-String "assets/yishu/workmanager-marker.txt"
        if ($mk) { Write-Host "PASS: $mk" -ForegroundColor Green }
        else { Write-Host "FAIL: nativeResources 未打入 assets——D-18 探测仍会恒 false" -ForegroundColor Red }
    }
    Step "包体积与头部占用（字体去向拍板依据）" {
        $len = (Get-Item $Apk).Length
        Write-Host ("  APK 文件: {0} bytes = {1} MB" -f $len, [math]::Round($len/1MB,1))
        Write-Host "  体积 Top10（未压缩）:"
        & $Aapt list -v $Apk 2>$null |
            ForEach-Object { if ($_ -match '^\s*(\d+)\s+\S+\s+\d+\s+\d+%\s+\S+\s+\S+\s+\S+\s+(\S+)$') {
                [pscustomobject]@{Size=[int64]$Matches[1]; Path=$Matches[2]} } } |
            Sort-Object Size -Descending | Select-Object -First 10 |
            ForEach-Object { "    {0,10:N0}  {1}" -f $_.Size, $_.Path }
    }
}

"install" {
    Step "设备在场" {
        $d = & $Adb devices | Select-String $Serial
        if (-not $d) { throw "设备不在场——先重插 USB/确认授权（必要时 adb kill-server; adb start-server 排除 server 互杀）" }
        Write-Host "PASS"
    }
    Write-Host "[人工] 手机侧确认 EMUI 纯净模式已关（设置→安全），否则 install 静默失败零报错" -ForegroundColor Yellow
    Step "安装" { & $Adb -s $Serial install -r $Apk }
    # 每次 cli launch / USB 重连后必须重建 reverse（实锤多次）
    Step "reverse 三连" {
        & $Adb -s $Serial reverse tcp:8010 tcp:8010
        & $Adb -s $Serial reverse tcp:8000 tcp:8000
        & $Adb -s $Serial reverse --list
    }
    Step "屏幕常亮" { & $Adb -s $Serial shell "svc power stayon usb; input keyevent KEYCODE_WAKEUP" }
}

"backend" {
    # ⚠️ 占 8000 前先跟媒体票据窗打招呼（其 media 端点只在主区）
    Write-Host "[提示] 8000 端口现有监听者数: $((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count)"
    Start-Process -WorkingDirectory "D:\GuangH-App\.wt\fix4b\backend" `
        -FilePath python -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WindowStyle Minimized
    # Windows 无 scheduler 形态，worker 用简单 worker 类
    Start-Process -WorkingDirectory "D:\GuangH-App\.wt\fix4b\backend" `
        -FilePath python -ArgumentList "-c","from app.workers.worker import get_worker_class; from app.core.queue import QUEUE_HIGH, QUEUE_LOW, get_queue; import logging; logging.basicConfig(level=logging.INFO); w=get_worker_class()([get_queue(QUEUE_HIGH),get_queue(QUEUE_LOW)]); w.work()" -WindowStyle Minimized
    Start-Sleep -Seconds 6
    (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz).Content
}

"logs" {
    & $Adb -s $Serial logcat -d -t 400 | Select-String "yishu" | Tee-Object "$ev\r2_logcat_$ts.log"
}

"manual" {
    # 逐项验收口径（tracker19 §4 行），每条 PASS 需 evidence 三要素
    @(
      "D-18/19  logcat 见 'initBackgroundTasks ok' + 周期任务登记；前台服务通知在场",
      "D-16     无情绪语音→chip 不渲染；主导情绪无 null 空提示；(服务端)GET /api/v1/asr/channels 两通道布尔",
      "D-07     <4min 录音入库→contents done 且 COS voice/ 有对象；详情页可回放；用户改过的转写文本不被管线覆盖",
      "D-07b    4~5min 录音（8MB 黑洞段）→走分片持久化不再静默 null",
      "D-08     断网停止录音→重试条在场→杀 App 重启→仍找回'发现未转写录音'→恢复网络重试成功并入队",
      "D-22     文本记录→点任一标签→立即 toast '已纠正'；DB: correction_log 该行 new_label=所点标签（非 mixed）；contents.content_class 同步改；再输同文本→分类直接命中个人层（layer=personal）；toast 若现 '模型参考' 不得改变已点标签",
      "D-05/14  蜂窝注入照片→held 计数=实数（无双登记）→横幅'立即上传原图'→成功后时间轴出现 L1 事件（contents 有、事件再无孤儿）",
      "D-21     manage/index/messages/interview 四页真机可滚动到底",
      "gap目验  按 docs/audit_20260831 报告 §四 P0 清单巡页（重点 index/record 间距与 detail hero 两 overlay 层）",
      "US-25    第三次纠错交互复判（D4 弹窗缺席现状，U3 拍板依据）"
    ) | ForEach-Object { Write-Host "[ ] $_" -ForegroundColor Yellow }
    Write-Host "`n证据: & $Adb -s $Serial exec-out screencap -p > $ev\r2_<项>_$ts.png ；日志 -Stage logs 自动落盘"
}
}
