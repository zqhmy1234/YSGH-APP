# R2 · 4b 修复批次真机复验电池组（自动化段 + 人工段清单）
# 用法: powershell -File r2_4b_verify.ps1 [-Stage autopsy|install|backend|logs|manual]
# 约定: 证据一律落 scripts/realdevice/evidence/，命名 r2_<项>_<时间戳>（证据三要素：设备+时间/文件/判定）
param(
    [string]$Apk = "D:\GuangH-App\.wt\fix4b\client\unpackage\debug\android_debug.apk",
    [string]$Serial = "DKS9K23526028855",
    [string]$Stage = "autopsy"
)
$ErrorActionPreference = "Stop"
$ev = "D:\GuangH-App\scripts\realdevice\evidence"
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
    # D-18: marker asset 必须随包（marker⇒classes 同包不变式）
    Step "D-18 marker asset" {
        $mk = aapt list $Apk 2>$null | Select-String "assets/yishu/workmanager-marker.txt"
        if ($mk) { Write-Host "PASS" } else { Write-Host "FAIL: nativeResources 未打入 assets" -ForegroundColor Red }
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
        adb -s $Serial reverse tcp:8000 tcp:8000
        $h = adb -s $Serial shell "curl -s -m 3 http://127.0.0.1:8000/healthz"
        if ($h -match '"status"\s*:\s*"ok"') { Write-Host "PASS: $h" } else { Write-Host "FAIL: 设备侧 healthz 不通（后端起了吗？跑 -Stage backend）" -ForegroundColor Red }
    }
    Step "屏幕常亮" { adb -s $Serial shell "svc power stayon usb; input keyevent KEYCODE_WAKEUP" }
}
"backend" {
    # fix4b 后端（与被测代码同源）+ dev worker（Windows 无 scheduler 形态，见 handoff 坑账）
    Write-Host "[提示] 与媒体窗共用 8000 端口前先打招呼：$((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count) 个监听者已在"
    Start-Process -WorkingDirectory "D:\GuangH-App\.wt\fix4b\backend" `
        -FilePath python -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WindowStyle Minimized
    Start-Process -WorkingDirectory "D:\GuangH-App\.wt\fix4b\backend" `
        -FilePath python -ArgumentList "-c","from app.workers.worker import get_worker_class; from app.core.queue import QUEUE_HIGH, QUEUE_LOW, get_queue; import logging; logging.basicConfig(level=logging.INFO); w=get_worker_class()([get_queue(QUEUE_HIGH),get_queue(QUEUE_LOW)]); w.work()" -WindowStyle Minimized
    Start-Sleep -Seconds 6
    (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz).Content
}
"logs" {
    adb -s $Serial logcat -d -t 400 | Select-String "yishu" | Tee-Object "$ev\r2_logcat_$ts.log"
}
"manual" {
    # 逐项验收口径（tracker19 §4 行），每条 PASS 需 evidence 三要素
    @(
      "D-18/19  logcat 见 'initBackgroundTasks ok' + 周期任务登记；前台服务通知在场（会话 B）",
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
    Write-Host "`n证据: adb -s $Serial exec-out screencap -p > $ev\r2_<项>_$ts.png ；日志 -Stage logs 自动落盘"
}
}
