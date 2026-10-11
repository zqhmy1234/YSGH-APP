#requires -Version 5.1
<#
  忆述光华 · 隧道抽象层（固定地址模式 · 2026-10-11）

  用途：把本机 backend（默认 127.0.0.1:8010）暴露成一个**公网 https 地址**，从而
        「手机装 APK 就能测、不需要数据线"——这是内测期既定的联调方案。

  为什么要有这一层：隧道提供方会变（ngrok 静态域名 / cloudflared named tunnel / 临时 quick tunnel），
  而上层（write_base_url.ps1 / pack.ps1 / 出包流程文档）只关心「拿到一个公网 URL」。
  把差异收敛在本文件，将来换提供方不必改动散落各处的流程。

  用法：
      # 起隧道并打印公网 URL（推荐：ngrok 静态域名，地址永久固定 ⇒ 隧道重启不用重出包）
      pwsh -File scripts\dev\tunnel.ps1 -Provider ngrok -StaticDomain yishu.ngrok-free.app

      # 只看当前已经在跑的隧道（不起新的）
      pwsh -File scripts\dev\tunnel.ps1 -QueryOnly

      # 临时隧道（cloudflared quick tunnel，地址每次重启都变 ⇒ 旧 APK 会失效，仅应急）
      pwsh -File scripts\dev\tunnel.ps1 -Provider cloudflared

  输出：公网 URL 同时写入 deploy/.tunnel-url（不入库，见 .gitignore），供 write_base_url.ps1 读取。

  退出码：0 = 拿到 URL；1 = 失败（含"上游没在监听"这种最常见的白忙一场）。
#>
[CmdletBinding()]
param(
    [ValidateSet('ngrok', 'cloudflared')]
    [string]$Provider = 'ngrok',
    [int]$UpstreamPort = 8010,
    [string]$StaticDomain = '',
    [string]$RepoRoot = '',
    [switch]$QueryOnly,
    [int]$TimeoutSec = 45
)

$ErrorActionPreference = 'Continue'

# ── 仓库根解析（与 preflight_pack.ps1 同款兜底；不在 param 默认值里用 $PSScriptRoot）──
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $here = $null
    if ($PSScriptRoot) { $here = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $here = (Get-Location).Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
}
$urlFile = Join-Path $RepoRoot 'deploy\.tunnel-url'

function Info($m) { Write-Host "  [INFO] $m" -ForegroundColor Gray }
function Ok($m) { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Bad($m, $fix) {
    Write-Host "  [FAIL] $m" -ForegroundColor Red
    if ($fix) { Write-Host "         -> $fix" -ForegroundColor Yellow }
}

function Test-Upstream {
    $ok = $false
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$UpstreamPort/healthz" -UseBasicParsing -TimeoutSec 5
        $ok = ($r.StatusCode -eq 200)
    }
    catch { $ok = $false }
    return $ok
}

function Get-NgrokTunnelUrl {
    # ngrok 自带本地管理 API（默认 4040）：比解析日志稳（日志格式会随版本变）
    foreach ($port in @(4040, 4041, 4042)) {
        try {
            $j = Invoke-RestMethod "http://127.0.0.1:$port/api/tunnels" -TimeoutSec 3
            $t = $j.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1
            if ($t -and $t.public_url) { return $t.public_url }
        }
        catch { }
    }
    return $null
}

function Start-Ngrok {
    if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
        Bad 'ngrok 未安装' '装：winget install ngrok.ngrok（或从 ngrok.com 下载 zip 解压到 PATH）'
        return $null
    }
    $existing = Get-NgrokTunnelUrl
    if ($existing) {
        Info "已有 ngrok 隧道在跑：$existing（复用，不新建）"
        return $existing
    }
    if ([string]::IsNullOrWhiteSpace($StaticDomain)) {
        Info '未传 -StaticDomain：将使用 ngrok 账号下的默认域名（若未配置则为随机地址，重启即变）'
        $ngrokArgs = @('http', "$UpstreamPort", '--log', 'stdout')
    }
    else {
        $ngrokArgs = @('http', "$UpstreamPort", "--url=$StaticDomain", '--log', 'stdout')
    }
    $logPath = Join-Path $RepoRoot '.cowork-temp\tunnel-ngrok.log'
    New-Item -ItemType Directory -Force (Split-Path $logPath) | Out-Null
    Start-Process ngrok -ArgumentList $ngrokArgs -RedirectStandardOutput $logPath -RedirectStandardError "$logPath.err" -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 800
        $u = Get-NgrokTunnelUrl
        if ($u) { return $u }
    }
    Bad "ngrok 在 $TimeoutSec 秒内未给出公网地址（看 $logPath）" `
        '常见原因：未 ngrok config add-authtoken / 静态域名与账号不匹配 / 该域名已被别的隧道占用'
    return $null
}

function Start-Cloudflared {
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Bad 'cloudflared 未安装' '装：winget install Cloudflare.cloudflared'
        return $null
    }
    $logPath = Join-Path $RepoRoot '.cowork-temp\tunnel-cloudflared.log'
    New-Item -ItemType Directory -Force (Split-Path $logPath) | Out-Null
    # ⚠️ quick tunnel：地址每次重启都变 ⇒ 已分发 APK 立刻全空（见 skills/apk-cloud-release 铁律 5）
    Start-Process cloudflared -ArgumentList @('tunnel', '--url', "http://127.0.0.1:$UpstreamPort") `
        -RedirectStandardOutput $logPath -RedirectStandardError "$logPath.err" -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 800
        if (Test-Path $logPath.err) {
            $m = [regex]::Match((Get-Content $logPath.err -Raw -ErrorAction SilentlyContinue),
                'https://[a-z0-9-]+\.trycloudflare\.com')
            if ($m.Success) { return $m.Value }
        }
    }
    Bad "cloudflared 在 $TimeoutSec 秒内未给出公网地址（看 $logPath.err）" '检查网络（本机到 Cloudflare 可能被阻断）'
    return $null
}

Write-Host ''
Write-Host '忆述光华 · 隧道（固定地址模式）' -ForegroundColor White
Info "提供方 = $Provider ｜ 上游 = 127.0.0.1:$UpstreamPort ｜ 仓库根 = $RepoRoot"

$url = $null
if ($QueryOnly) {
    $url = if ($Provider -eq 'ngrok') { Get-NgrokTunnelUrl } else { (Get-Content $urlFile -ErrorAction SilentlyContinue) }
}
else {
    if (-not (Test-Upstream)) {
        Bad "上游 127.0.0.1:$UpstreamPort 没有响应 /healthz" `
            '先把后端栈起起来：pwsh -File scripts\dev\start_all.ps1（隧道指向不存在的服务 = 白忙一场）'
        exit 1
    }
    Ok "上游健康检查通过（http://127.0.0.1:$UpstreamPort/healthz）"
    $url = if ($Provider -eq 'ngrok') { Start-Ngrok } else { Start-Cloudflared }
}

if (-not $url) {
    Bad '未取得公网地址'
    exit 1
}

# 落盘供后续脚本（write_base_url.ps1 / pack.ps1）读取
Set-Content -Path $urlFile -Value $url -Encoding ASCII
Ok "公网地址：$url"
Info "已写入 $urlFile（不入库）"
if ($url -match 'trycloudflare\.com') {
    Write-Host '  [WARN] 这是**临时隧道**地址：重启即换址 ⇒ 已分发 APK 会立刻全部连不上。' -ForegroundColor Yellow
    Write-Host '         内测期请用 ngrok 静态域名（-StaticDomain）或 named tunnel 固定地址。' -ForegroundColor Yellow
}
Write-Host ''
exit 0
