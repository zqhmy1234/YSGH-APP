#requires -Version 5.1
<#
  忆述光华 · 客户端环境三件套一键回写（固定地址模式 · 2026-10-11）

  背景：`client/utils/config.uts` 的三个常量（ENV / REAL_DEVICE_HOST / PROD_BASE_URL）此前
  全靠手改——10-09 真机联调把它们改成 dev 态后随代码提交，出包时忘改回 ⇒ 正式包内连
  `http://127.0.0.1:8010`（手机自己的回环地址）⇒ 所有接口全挂、每页取不到数据，表现为
  「各个页面都无法正常体验、数据流被切断」（见 docs/决策台账.md §4.18）。本脚本把这三行
  的切换变成一条命令，消灭"忘改"这一根因。

  用法：
      # 出包态：ENV='prod' + REAL_DEVICE_HOST='' + PROD_BASE_URL=<隧道地址>
      pwsh -File scripts\dev\write_base_url.ps1                     # 地址取自 deploy\.tunnel-url
      pwsh -File scripts\dev\write_base_url.ps1 -BaseUrl https://xxx.ngrok-free.dev

      # 真机联调态（adb reverse + 标准基座）：ENV='dev' + REAL_DEVICE_HOST='localhost'
      pwsh -File scripts\dev\write_base_url.ps1 -Restore

  写入契约（三行唯一定位，**必须无 BOM**：preflight_pack.ps1 用文本正则读该文件）：
      export const ENV: string = '<dev|prod>'
      export const REAL_DEVICE_HOST: string = '<...>'
      const PROD_BASE_URL: string = '<https url>'

  退出码：0 = 已写入且校验通过；1 = 失败。
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [string]$BaseUrl = '',
    [switch]$Restore
)

$ErrorActionPreference = 'Continue'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $here = $null
    if ($PSScriptRoot) { $here = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $here = (Get-Location).Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
}

function Info($m) { Write-Host "  [INFO] $m" -ForegroundColor Gray }
function Ok($m) { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Bad($m, $fix) {
    Write-Host "  [FAIL] $m" -ForegroundColor Red
    if ($fix) { Write-Host "         -> $fix" -ForegroundColor Yellow }
}

$cfgPath = Join-Path $RepoRoot 'client\utils\config.uts'
if (-not (Test-Path $cfgPath)) {
    Bad "找不到 $cfgPath" '确认 -RepoRoot 指向仓库根'
    exit 1
}

Write-Host ''
Write-Host '忆述光华 · 客户端环境三件套回写' -ForegroundColor White

# ── 目标值 ────────────────────────────────────────────────────────────────
if ($Restore) {
    $targetEnv = 'dev'
    $targetHost = 'localhost'
    $targetBase = $null      # 保留现值（联调态不关心 PROD_BASE_URL）
    Info '模式 = 真机联调态（ENV=dev / REAL_DEVICE_HOST=localhost / PROD_BASE_URL 保持原值）'
}
else {
    $targetEnv = 'prod'
    $targetHost = ''
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        $urlFile = Join-Path $RepoRoot 'deploy\.tunnel-url'
        if (Test-Path $urlFile) {
            $BaseUrl = (Get-Content $urlFile -Raw).Trim()
            Info "地址取自 $urlFile"
        }
    }
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        Bad '未提供地址：既没传 -BaseUrl，也没有 deploy\.tunnel-url' `
            '先起隧道：pwsh -File scripts\dev\tunnel.ps1 -Provider ngrok -StaticDomain <你的静态域名>'
        exit 1
    }
    if ($BaseUrl -notmatch '^https?://') {
        Bad "地址必须以 http(s):// 开头：$BaseUrl" '隧道地址形如 https://xxx.ngrok-free.dev（不带结尾斜杠）'
        exit 1
    }
    $BaseUrl = $BaseUrl.TrimEnd('/')
    $targetBase = $BaseUrl
    Info "模式 = 出包态（ENV=prod / REAL_DEVICE_HOST='' / PROD_BASE_URL=$BaseUrl）"
}

$text = Get-Content -Path $cfgPath -Raw -Encoding UTF8
$orig = $text

# ── 三行替换（各自必须命中且仅命中一次，否则不猜、直接失败）────────────────
function Set-Const([string]$content, [string]$pattern, [string]$replacement, [string]$label) {
    $m = [regex]::Matches($content, $pattern)
    if ($m.Count -ne 1) {
        Bad "$label 匹配到 $($m.Count) 处（应为 1 处）" "检查 config.uts 是否被改过结构；本脚本依赖正则唯一定位"
        return $null
    }
    return [regex]::Replace($content, $pattern, $replacement)
}

$step = Set-Const $text `
    "export const ENV:\s*string\s*=\s*'[^']*'" `
    "export const ENV: string = '$targetEnv'" 'ENV'
if ($null -eq $step) { exit 1 }
$text = $step

$step = Set-Const $text `
    "export const REAL_DEVICE_HOST:\s*string\s*=\s*'[^']*'" `
    "export const REAL_DEVICE_HOST: string = '$targetHost'" 'REAL_DEVICE_HOST'
if ($null -eq $step) { exit 1 }
$text = $step

if ($null -ne $targetBase) {
    $step = Set-Const $text `
        "const PROD_BASE_URL:\s*string\s*=\s*'[^']*'" `
        "const PROD_BASE_URL: string = '$targetBase'" 'PROD_BASE_URL'
    if ($null -eq $step) { exit 1 }
    $text = $step
}

if ($text -eq $orig -and -not $Restore) {
    Info '三件套已是目标值，无需改动'
}

# ── 写回（**无 BOM**：preflight 的正则按 UTF8 读，BOM 会让首行匹配偏移）──────
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($cfgPath, $text, $utf8NoBom)
Ok "已写入 $cfgPath（UTF-8 无 BOM）"

# ── 回读校验 ─────────────────────────────────────────────────────────────
$check = Get-Content -Path $cfgPath -Raw -Encoding UTF8
$e1 = [regex]::Match($check, "export const ENV:\s*string\s*=\s*'([^']*)'").Groups[1].Value
$e2 = [regex]::Match($check, "export const REAL_DEVICE_HOST:\s*string\s*=\s*'([^']*)'").Groups[1].Value
$e3 = [regex]::Match($check, "const PROD_BASE_URL:\s*string\s*=\s*'([^']*)'").Groups[1].Value
Ok "回读：ENV='$e1' ｜ REAL_DEVICE_HOST='$e2' ｜ PROD_BASE_URL='$e3'"

if ($e1 -ne $targetEnv -or $e2 -ne $targetHost -or ($null -ne $targetBase -and $e3 -ne $targetBase)) {
    Bad '回读值与目标不符' '文件可能被其它进程占用；重跑一次或人工核对'
    exit 1
}

# ── 交付说明（每次回写都打印，避免"忘了本机要常开"）───────────────────────
if (-not $Restore) {
    Write-Host ''
    Write-Host '  ── 出包前必须确认（缺一即"装了却用不了"）──' -ForegroundColor Cyan
    Write-Host '    · 后端 8010 在跑        ：pwsh -File scripts\dev\start_all.ps1'
    Write-Host '    · RQ worker 在跑        ：同上（含 worker）'
    Write-Host '    · agent 8300 在跑       ：同上（AI 页依赖）'
    Write-Host "    · 隧道在跑且地址一致    ：$e3"
    Write-Host '    · 内测登录开关          ：backend/.env 的 ALLOW_DEVICE_LOGIN=true'
    Write-Host '    · 前置门全绿            ：pwsh -File deploy\scripts\preflight_pack.ps1 -AllowTunnel'
    Write-Host ''
    Write-Host '  ── 交付给测试同学时必须写明 ──' -ForegroundColor Cyan
    Write-Host '    · 本机（这台电脑）必须**保持开机**且上述服务常驻；关机 = 所有已装 APK 立刻不可用'
    if ($e3 -match 'trycloudflare\.com|loca\.lt|localtunnel|serveo') {
        Write-Host '    · ⚠️ 当前是**临时隧道地址**：隧道重启即换址 ⇒ 旧 APK 全部失效，必须重出包' -ForegroundColor Yellow
    }
    else {
        Write-Host '    · 当前是固定地址（ngrok 静态域名/named tunnel）：隧道重启不换址，旧 APK 一直可用'
    }
}

Write-Host ''
exit 0
