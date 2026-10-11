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
    [int]$PackTimeoutSec = 1500
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
    $hx = Get-Process HBuilderX -ErrorAction SilentlyContinue
    if ($hx) {
        $cpu1 = ($hx | Measure-Object -Property CPU -Sum).Sum
        Start-Sleep -Seconds 5
        $hx2 = Get-Process HBuilderX -ErrorAction SilentlyContinue
        $cpu2 = ($hx2 | Measure-Object -Property CPU -Sum).Sum
        if ($cpu2 -gt ($cpu1 + 1)) {
            Bad 'HBuilderX 正在忙（CPU 5 秒内持续增长）' '等它空闲（或关掉正在跑的运行/打包任务）再出包，否则并发会互杀'
            exit 1
        }
        Info 'HBuilderX 存在且空闲（单实例并发预检通过）'
    }
    else { Info 'HBuilderX 未运行（cli 会自行拉起）' }

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
            $hit = Select-String -Path $packLog -Pattern '打包成功|安装包下载地址' -ErrorAction SilentlyContinue | Select-Object -Last 1
            if ($hit) { $doneLine = $hit.Line.Trim(); break }
            $failHit = Select-String -Path $packLog -Pattern '打包失败|错误' -ErrorAction SilentlyContinue | Select-Object -Last 1
            if ($failHit) { Bad ("云打包失败：" + $failHit.Line.Trim()) "看 $packLog"; Remove-Item $cfgPath -Force -ErrorAction SilentlyContinue; exit 1 }
        }
    }
    Remove-Item $cfgPath -Force -ErrorAction SilentlyContinue   # 用完即删（口令不留盘）
    if (-not $doneLine) {
        Bad "云打包在 $PackTimeoutSec 秒内未给出结果" "看 $packLog（HBuilderX 是否掉线/是否需登录）"
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
    $sha = (Get-FileHash $dist -Algorithm SHA256).Hash
    Ok "已投放 $dist"
    Ok "SHA256 = $sha"
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
