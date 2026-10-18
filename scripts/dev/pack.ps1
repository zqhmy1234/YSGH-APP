#requires -Version 5.1
<#
  忆述光华 · 出包链一键化（2026-10-11）

  把此前**全手工**的 11 步出包链固化成可复现脚本（缺一步就是一次废包或一次"装了却用不了"）：

      前置门 → 云打包（自有证书）→ 四项尸检 → 投放分发页 → 交付清单

  为什么必须脚本化：这 11 步散落在 skill 与文档里，靠人记住；2026-10-04 首次出包就踩了
  「签名参数写错静默用 DCloud 共享证书（已装用户装不上、只能卸载重装）」「临时隧道地址重启即失效」
  两个坑。脚本把**判据**变成命令，而不是记忆。

  ⚠️ 前提（本机会自动检查）：
    · 出包机必须能编译客户端（HBuilderX 存在且**单实例空闲**——`cli pack` 与其它窗口并发会互杀）
    · 签名口令文件在**仓库外**：%USERPROFILE%\.yishu-signing\keystore-password-*.txt（键值 path/alias/passwd）
    · 客户端三件套已是出包态（ENV='prod' / REAL_DEVICE_HOST=''）——由 preflight 把关，
      推荐先用 scripts\dev\write_base_url.ps1 一键回写

  用法：
      pwsh -File scripts\dev\pack.ps1                 # 全链（含云打包，3~10 分钟）
      pwsh -File scripts\dev\pack.ps1 -AllowTunnel    # 固定地址隧道模式（ngrok 静态域名）
      pwsh -File scripts\dev\pack.ps1 -SkipPack -Apk <已下载的 apk>   # 只做尸检（复用已有包）
      pwsh -File scripts\dev\pack.ps1 -SkipPack -Apk <apk> -Deploy    # 尸检 + 投放
      pwsh -File scripts\dev\pack.ps1 -PreflightOnly  # 只跑前置门

  退出码：0 = 全绿；1 = 有 FAIL（禁止继续）。
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [switch]$AllowTunnel,
    [switch]$PreflightOnly,
    [switch]$SkipPack,
    [string]$Apk = '',
    [switch]$Deploy,
    [string]$SigningDir = "$env:USERPROFILE\.yishu-signing",
    # 2026-10-15（台账 §4.20 ㉙）：默认值 1500s（25min）**在 DCloud 免费队列下必然超时** ✗ ——
    # 实测 21:17 提交时排到第 **98** 位（预计 20+ 分钟），脚本到 25min 就 `exit 1`，
    # 而 `-RedirectStandardOutput $packLog` 会**截断** pack.log ⇒ **产物 URL 一并丢失** ✗✗
    # （这正是"包总出不来"的机制原因）。默认放宽到 3600s（60min，覆盖免费队列常态）；
    # 队列特别长时可显式加大：`-PackTimeoutSec 7200`。付费加速链接见 pack.log。
    [int]$PackTimeoutSec = 3600
)

$ErrorActionPreference = 'Continue'
# 硬失败计数（2026-10-11 核验修复）：四项尸检与投放此前只 `Bad(...)` 打印、**不改退出码**
# ⇒ 签名无效/权限缺失/包内地址不符也照样「尸检通过」并投放、脚本返回 0。
# 那正是"签名错→已装用户装不上（只能卸载重装、数据全丢）"这类事故的最后守门点，必须阻断。
$script:hardFail = 0

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $here = $null
    if ($PSScriptRoot) { $here = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $here = (Get-Location).Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
}
$logDir = Join-Path $RepoRoot '.cowork-temp'
New-Item -ItemType Directory -Force $logDir | Out-Null

function Step($m) { Write-Host ''; Write-Host "== $m ==" -ForegroundColor Cyan }
function Info($m) { Write-Host "  [INFO] $m" -ForegroundColor Gray }
function Ok($m) { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Bad($m, $fix) {
    Write-Host "  [FAIL] $m" -ForegroundColor Red
    if ($fix) { Write-Host "         -> $fix" -ForegroundColor Yellow }
}

Write-Host ''
Write-Host '忆述光华 · 出包链' -ForegroundColor White
Info "仓库根 = $RepoRoot"

# ─────────────────────────── 1. 前置门 ───────────────────────────
Step '1. 出包前置门（含客户端编译门）'
$pre = Join-Path $RepoRoot 'deploy\scripts\preflight_pack.ps1'
if (-not (Test-Path $pre)) {
    Bad "缺少 $pre" '该文件是出包门的一部分'
    exit 1
}
$preArgs = @('-ExecutionPolicy', 'Bypass', '-File', $pre)
if ($AllowTunnel) { $preArgs += '-AllowTunnel' }
& powershell @preArgs
if ($LASTEXITCODE -ne 0) {
    Bad "前置门未通过（退出码 $LASTEXITCODE）" '按上方 FAIL 逐条修复；**禁止带 FAIL 打包**'
    exit 1
}
Ok '前置门全绿'
if ($PreflightOnly) { exit 0 }

# ─────────────────────────── 2. 云打包 ───────────────────────────
$apkPath = $Apk
if ($SkipPack) {
    Info "按 -SkipPack 跳过云打包（复用 -Apk $Apk）"
    if ([string]::IsNullOrWhiteSpace($apkPath) -or -not (Test-Path $apkPath)) {
        Bad '未提供有效的 -Apk 路径' '要么去掉 -SkipPack 走云打包，要么给出已下载的 apk 绝对路径'
        exit 1
    }
}
else {
    Step '2. 云打包（HBuilderX CLI · 自有证书）'
    $cli = $env:HBUILDERX_CLI
    if (-not $cli) { $cli = 'D:\HBuilderX\cli.exe' }
    if (-not (Test-Path $cli)) {
        Bad "找不到 HBuilderX CLI：$cli" '设环境变量 HBUILDERX_CLI，或安装 HBuilderX 到默认路径'
        exit 1
    }

    # 单实例空闲预检：cli pack 与其它窗口的 cli pack/launch 并发会互杀（2026-10-04 实测）
    #
    # ⚠️ 2026-10-12 修一个**自锁缺陷**：本文件 §1 刚跑过客户端编译门（会唤醒 HBuilderX 并让它忙一阵），
    #    紧接着这里做"5 秒 CPU 不增长"的一次性判定 ⇒ **每次都会判自己忙**、永远打不了包。
    #    改为**轮询等待空闲**（最多 90 秒）：既保留防并发保护，又不会自我阻断。
    $idle = $false
    $hx = Get-Process HBuilderX -ErrorAction SilentlyContinue
    if (-not $hx) {
        $idle = $true
        Info 'HBuilderX 未运行（cli 会自行拉起）'
    }
    else {
        for ($i = 1; $i -le 18; $i++) {
            $h1 = Get-Process HBuilderX -ErrorAction SilentlyContinue
            if (-not $h1) { $idle = $true; break }
            $c1 = ($h1 | Measure-Object -Property CPU -Sum).Sum
            Start-Sleep -Seconds 5
            $h2 = Get-Process HBuilderX -ErrorAction SilentlyContinue
            if (-not $h2) { $idle = $true; break }
            $c2 = ($h2 | Measure-Object -Property CPU -Sum).Sum
            $delta = $c2 - $c1
            if ($delta -lt 1.0) {
                $idle = $true
                Info ("HBuilderX 已空闲（第 {0} 次探测，5 秒 CPU 增量 {1:N2}）" -f $i, $delta)
                break
            }
            Info ("HBuilderX 仍忙（第 {0}/18 次，5 秒 CPU 增量 {1:N2}）—— 编译门刚跑完属正常，继续等…" -f $i, $delta)
        }
        if (-not $idle) {
            Bad 'HBuilderX 持续繁忙（等待 90 秒仍未空闲）' '关掉正在跑的运行/打包/索引任务再出包，否则并发会互杀'
            exit 1
        }
    }

    # 口令文件（仓库外；键值形式 path/alias/passwd）
    $pwFile = Get-ChildItem (Join-Path $SigningDir 'keystore-password-*.txt') -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pwFile) {
        Bad "找不到签名口令文件（$SigningDir\keystore-password-*.txt）" '口令必须走仓库外配置文件；见 skills/apk-cloud-release/SKILL.md §3'
        exit 1
    }
    $kv = @{}
    foreach ($line in Get-Content $pwFile.FullName) {
        if ($line -match '^\s*([A-Za-z_]+)\s*[:=]\s*(.+?)\s*$') { $kv[$matches[1].ToLower()] = $matches[2] }
    }
    if (-not $kv['passwd'] -or -not $kv['alias'] -or -not $kv['path']) {
        Bad '口令文件缺少 path / alias / passwd 三项' '按 skills/apk-cloud-release 的格式补齐（值回显属违规，勿贴聊天）'
        exit 1
    }

    # ⚠️ androidpacktype=0 才是**自有证书**；在 json 里另写嵌套 keystore 字段**不生效**
    #    （会静默落到 DCloud 共享证书 ⇒ 已装用户无法覆盖安装，只能卸载重装、数据全丢）。
    $cfg = [ordered]@{ android = [ordered]@{
            packagename   = 'com.yishu.guanghua'
            androidpacktype = 0
            certalias     = $kv['alias']
            certfile      = $kv['path']
            certpassword  = $kv['passwd']
            storepassword = $kv['passwd']
        } }
    $cfgPath = Join-Path $SigningDir 'pack-config.json'
    [System.IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
    Info "已生成打包配置（仓库外）：$cfgPath"

    $packLog = Join-Path $logDir 'pack.log'
    $client = Join-Path $RepoRoot 'client'
    Start-Process $cli -ArgumentList @('pack', '--project', $client, '--platform', 'android', '--config', $cfgPath) `
        -RedirectStandardOutput $packLog -RedirectStandardError "$packLog.err" -WindowStyle Hidden | Out-Null
    Info "云打包已启动（3~10 分钟），日志 $packLog"

    $deadline = (Get-Date).AddSeconds($PackTimeoutSec)
    $doneLine = ''
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        if (Test-Path $packLog) {
            # ⚠️ 2026-10-12 修误判：**不能匹配裸 `打包成功`**——日志里有一句提示语
            #    「打包成功后会自动返回下载链接」，会让脚本在**刚提交云端时**就认为完成并提前退出
            #    （实测踩到：01:17 提交、01:18 就"完成"，实际云端才刚开始打包）。
            #    只认真正的终态：`安装包下载地址`（打包完成返回）或显式失败。
            $hit = Select-String -Path $packLog -Pattern '安装包下载地址|download_url|打包失败' -ErrorAction SilentlyContinue | Select-Object -Last 1
            if ($hit) { $doneLine = $hit.Line.Trim(); break }
            # 真失败的判据：出现 `打包失败` 或云端返回 error（不要用裸"错误"——日志里有"错误码"之类的正常提示）
            $failHit = Select-String -Path $packLog -Pattern '打包失败|打包步骤.*失败' -ErrorAction SilentlyContinue | Select-Object -Last 1
            if ($failHit) { Bad ("云打包失败：" + $failHit.Line.Trim()) "看 $packLog"; Remove-Item $cfgPath -Force -ErrorAction SilentlyContinue; exit 1 }
        }
    }
    Remove-Item $cfgPath -Force -ErrorAction SilentlyContinue   # 用完即删（口令不留盘）
    if (-not $doneLine) {
        Bad "云打包在 $PackTimeoutSec 秒内未给出结果" "看 $packLog（HBuilderX 是否掉线/是否需登录）"
        # 2026-10-15（台账 §4.20 ㉙）：超时**不等于**云端失败 —— 云端任务仍在跑，只是本地没等到。
        # 但 `-RedirectStandardOutput` 会截断 pack.log ⇒ **下一次运行会把本次的产物 URL 覆盖掉** ✗，
        # 故此处必须把自救路径写清楚（否则包做出来了却拿不到）。
        Write-Host '  ℹ️ 云端任务**不会**因为本地超时而取消，产物通常仍会产出。拿 URL 的两条路：' -ForegroundColor Yellow
        Write-Host '     ① DCloud 控制台 → 该次云打包记录（推荐，最稳）；' -ForegroundColor Yellow
        Write-Host '     ② 立刻 `Get-Content .cowork-temp\pack.log -Tail 20` 看是否已出现 `安装包下载地址`（出现即拷贝走，别重跑脚本 —— 重跑会截断日志 ✗）' -ForegroundColor Yellow
        Write-Host "     下次加大等待：pwsh -File scripts\dev\pack.ps1 -AllowTunnel -PackTimeoutSec 7200" -ForegroundColor Yellow
        exit 1
    }
    Ok "云打包完成：$doneLine"
    Write-Host '  ⚠️ 请**人工**确认日志里证书类型是「自有证书」（不是「Android云端证书」）——' -ForegroundColor Yellow
    Write-Host '     签名错了会让已安装用户无法覆盖安装（只能卸载重装、数据全丢）。' -ForegroundColor Yellow

    if ([string]::IsNullOrWhiteSpace($apkPath)) {
        Write-Host ''
        Write-Host '  下载 APK 后重跑本脚本做尸检与投放：' -ForegroundColor Cyan
        Write-Host '    pwsh -File scripts\dev\pack.ps1 -SkipPack -Apk <下载的 apk 路径> -Deploy'
        Write-Host ''
        exit 0
    }
}

# ─────────────────────────── 3. 四项尸检 ───────────────────────────
Step '3. 四项尸检（签名 / 包内地址 / 权限 / 大小时间）'
$sdkBt = Get-ChildItem "$env:LOCALAPPDATA\Android\Sdk\build-tools" -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
$apksigner = if ($sdkBt) { Join-Path $sdkBt.FullName 'apksigner.bat' } else { '' }
$aapt2 = if ($sdkBt) { Join-Path $sdkBt.FullName 'aapt2.exe' } else { '' }

# ① 包内后端地址（无需 SDK 也能查：纯文本搜二进制）
$urlFile = Join-Path $RepoRoot 'deploy\.tunnel-url'
$expectHost = ''
if (Test-Path $urlFile) { $expectHost = ((Get-Content $urlFile -Raw).Trim() -replace '^https?://', '') }
if ($expectHost) {
    $hit = (Select-String -Path $apkPath -Pattern $expectHost -SimpleMatch -Quiet)
    if ($hit) { Ok "包内含期望地址主机：$expectHost" }
    else { $script:hardFail++; Bad "包内**找不到**期望地址主机 $expectHost" '多半是没跑 write_base_url.ps1 就打包了（包内地址焊死，改不了）' }
}
else { Info '未记录隧道地址（deploy\.tunnel-url 不存在）⇒ 跳过地址比对，请人工确认包内 PROD_BASE_URL' }

# ② 签名
if ($apksigner -and (Test-Path $apksigner)) {
    $verify = & $apksigner verify --print-certs $apkPath 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Ok 'apksigner verify 通过（签名有效）'
        ($verify -split "`n" | Select-String -Pattern 'SHA-256|Signer #1 certificate DN' | Select-Object -First 2) |
            ForEach-Object { Info $_.Line.Trim() }
        Write-Host '      请与 docs/图标与签名证书_20260921.md §五 的登记值逐项核对' -ForegroundColor Yellow
    }
    else { $script:hardFail++; Bad 'apksigner verify 失败（签名无效）' '用自有证书重打（androidpacktype=0）' }
}
else { Info "未找到 apksigner（$sdkBt）⇒ 跳过签名校验（请人工核）" }

# ③ 权限
if ($aapt2 -and (Test-Path $aapt2)) {
    $perm = & $aapt2 dump permissions $apkPath 2>&1 | Out-String
    foreach ($p in @('RECORD_AUDIO', 'READ_MEDIA_IMAGES', 'ACCESS_FINE_LOCATION', 'INTERNET')) {
        if ($perm -match $p) { Ok "权限含 $p" } else { $script:hardFail++; Bad "权限缺 $p" '权限缺失=功能静默失效；检查 client/manifest.json' }
    }
}
else { Info '未找到 aapt2 ⇒ 跳过权限校验（请人工核）' }

# ④ 大小与时间
$fi = Get-Item $apkPath
Ok ("APK 大小 {0:N1} MB ｜ 修改时间 {1}" -f ($fi.Length / 1MB), $fi.LastWriteTime)

# ─────────────────────────── 3.5 硬失败闸门 ───────────────────────────
# 尸检有任何硬失败 ⇒ 绝不投放、绝不返回 0（这是"签名错/地址错却发出去"的最后一道闸）
if ($script:hardFail -gt 0) {
    Write-Host ''
    Bad "尸检有 $($script:hardFail) 项硬失败" '修掉根因再投放；**不要把这种包发给测试同学**'
    exit 1
}

# ─────────────────────────── 4. 投放 ───────────────────────────
if ($Deploy) {
    Step '4. 投放分发页'
    $dist = Join-Path $RepoRoot 'deploy\download\apk\yishu-beta-latest.apk'
    if (Test-Path $dist) {
        $bak = Join-Path (Split-Path $dist) ("yishu-beta-OLD-" + (Get-Date -Format 'yyyyMMdd-HHmm') + ".apk")
        Copy-Item $dist $bak -Force
        Info "旧包已备份：$(Split-Path $bak -Leaf)"
    }
    Copy-Item $apkPath $dist -Force
    # ⚠️ 2026-10-12：原实现直接 `(Get-FileHash $dist).Hash` 插值 ⇒ 偶发拿到空值
    #    （刚拷完文件仍被占用/索引未刷新时会抛，而 ErrorActionPreference=Continue 让它**静默**变成空串，
    #     表现为交付说明里 `SHA256 =` 后面什么都没有 —— 而 sha256 是交付三项里唯一可校验的一项）。
    $sha = ''
    try { $sha = (Get-FileHash $dist -Algorithm SHA256).Hash }
    catch { Warn2 "哈希计算失败（文件可能仍被占用）：$($_.Exception.Message)" }
    Ok "已投放 $dist"
    if ($sha) { Ok "SHA256 = $sha" } else { Warn2 'SHA256 未取到 —— 请稍后手工核对（Get-FileHash）再交付' }
    Write-Host '  还需人工：回填 deploy/download/index.html 的文件大小/更新日期，并确认二维码' -ForegroundColor Yellow
}
else {
    Info '未加 -Deploy ⇒ 只做尸检不投放（投放：加 -Deploy 重跑，或手工拷到 deploy\download\apk\）'
}

# ─────────────────────────── 5. 交付清单 ───────────────────────────
Step '5. 交付给测试同学时必须写明的三件事'
Write-Host '  1) APK 路径 + sha256（见上）'
Write-Host '  2) **本机必须保持开机**，且这些服务常驻：backend 8010 / RQ worker / agent 8300 /'
Write-Host '     Redis+Qdrant(Docker) / 隧道（关电脑 = 所有已装 APK 立刻不可用）'
Write-Host '  3) 地址时效：固定地址（ngrok 静态域名）≠ 临时隧道 —— 后者**重启即失效、必须重出包**'
Write-Host ''
exit $(if ($script:hardFail -gt 0) { 1 } else { 0 })
