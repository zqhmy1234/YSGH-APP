#requires -Version 5.1
<#
  忆述光华 · 本机联调全栈状态一览（2026-10-11）

  用途：一条命令回答"现在到底能不能测"。此前要靠人肉回忆 11 步链路里哪一步没做
        （漏 worker ⇒ 内容卡 processing；漏隧道 ⇒ 手机直连失败；隧道重启 ⇒ 旧 APK 失效）。

  用法：
      pwsh -File scripts\dev\status.ps1

  退出码：0 = 全部就绪；1 = 有缺项（逐项已标注）。
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [int]$BackendPort = 8010,
    [int]$AgentPort = 8300
)

$ErrorActionPreference = 'Continue'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $here = $null
    if ($PSScriptRoot) { $here = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $here = (Get-Location).Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
}

$missing = 0
function Row($name, $ok, $detail) {
    if ($ok) { Write-Host ("  [ OK ] {0,-22} {1}" -f $name, $detail) -ForegroundColor Green }
    else { Write-Host ("  [MISS] {0,-22} {1}" -f $name, $detail) -ForegroundColor Red; $script:missing++ }
}

function Test-Listening([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c -and @($c).Count -gt 0)
}
function Test-Http([string]$Url) {
    try { return ((Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 6).StatusCode -eq 200) } catch { return $false }
}

Write-Host ''
Write-Host '忆述光华 · 本机联调状态' -ForegroundColor White
Write-Host ("仓库根 = $RepoRoot")
Write-Host ''

Write-Host '── 底座（Docker / PG）──' -ForegroundColor Cyan
$dockerOk = $false
try { docker info *> $null; $dockerOk = ($LASTEXITCODE -eq 0) } catch { $dockerOk = $false }
Row 'Docker daemon' $dockerOk $(if ($dockerOk) { 'running' } else { '未运行（Redis/Qdrant 起不来）' })
foreach ($c in @('yishu-redis', 'yishu-qdrant')) {
    $st = ''
    if ($dockerOk) { $st = (docker inspect -f '{{.State.Status}}' $c 2>$null) }
    Row "容器 $c" ($st -eq 'running') $(if ($st) { $st } else { '不可查' })
}
Row 'Redis PING' ((Test-Listening 6379)) '端口 6379 监听'

Write-Host ''
Write-Host '── 应用服务 ──' -ForegroundColor Cyan
$b = Test-Http "http://127.0.0.1:$BackendPort/healthz"
Row "backend :$BackendPort" $b $(if ($b) { '/healthz OK' } else { '无响应' })
$a = Test-Http "http://127.0.0.1:$AgentPort/health"
Row "agent :$AgentPort" $a $(if ($a) { '/health OK' } else { '无响应（AI 页会 502）' })
$worker = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'app\.workers\.worker' }).Count -gt 0
Row 'RQ worker' $worker $(if ($worker) { '运行中' } else { '未运行（内容会一直卡 processing）' })

Write-Host ''
Write-Host '── 出包/联调态 ──' -ForegroundColor Cyan
$cfg = Join-Path $RepoRoot 'client\utils\config.uts'
$envVal = ''
$hostVal = ''
$baseVal = ''
if (Test-Path $cfg) {
    $t = Get-Content $cfg -Raw -Encoding UTF8
    $envVal = [regex]::Match($t, "export const ENV:\s*string\s*=\s*'([^']*)'").Groups[1].Value
    $hostVal = [regex]::Match($t, "export const REAL_DEVICE_HOST:\s*string\s*=\s*'([^']*)'").Groups[1].Value
    $baseVal = [regex]::Match($t, "const PROD_BASE_URL:\s*string\s*=\s*'([^']*)'").Groups[1].Value
}
$isProdMode = ($envVal -eq 'prod' -and $hostVal -eq '')
Write-Host ("  [INFO] 客户端环境               ENV='{0}' ｜ REAL_DEVICE_HOST='{1}'" -f $envVal, $hostVal)
Write-Host ("  [INFO] PROD_BASE_URL            {0}" -f $baseVal)
if ($isProdMode) { Write-Host '  [ OK ] 出包态（prod + 空 host）' -ForegroundColor Green }
else { Write-Host '  [INFO] 联调态（dev + localhost；出包前需 write_base_url.ps1 切 prod）' -ForegroundColor Yellow }

$urlFile = Join-Path $RepoRoot 'deploy\.tunnel-url'
if (Test-Path $urlFile) {
    $tu = (Get-Content $urlFile -Raw).Trim()
    $match = ($baseVal.TrimEnd('/') -eq $tu)
    Row '隧道地址一致' $match "$tu $([char]0x2192) PROD_BASE_URL"
    if ($tu -match 'trycloudflare\.com|loca\.lt|localtunnel|serveo') {
        Write-Host '  [WARN] 临时隧道地址：隧道重启即换址 ⇒ 已分发 APK 全部失效' -ForegroundColor Yellow
    }
}
else {
    Write-Host '  [INFO] 未记录隧道地址（deploy\.tunnel-url 不存在：还没起隧道？）' -ForegroundColor Yellow
}
Row '内测登录开关' (@(Select-String -Path (Join-Path $RepoRoot 'backend\.env') -Pattern '^ALLOW_DEVICE_LOGIN=true' -ErrorAction SilentlyContinue).Count -gt 0) 'backend/.env ALLOW_DEVICE_LOGIN=true'

Write-Host ''
Write-Host '── 服务间接线（台账 §7.12：静默降级显性化）──' -ForegroundColor Cyan
# 为什么单独一段：上面只回答"端口通不通"，回答不了"通着却不干活"——
# backend 与 agent 的共享密钥是否同一把、W1 镜像/W2 检索是否在持续降级。
# 这三条都是"接口 200 但功能是假的"（AI 说已记住、App 时间轴却空白）。
$probe = Join-Path $RepoRoot 'scripts\dev\health_probe.py'
if (Test-Path $probe) {
    try {
        & python $probe
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  [MISS] 服务间接线存在不一致（上方 [FAIL]）' -ForegroundColor Red
            $missing++
        }
    }
    catch {
        Write-Host '  [WARN] 未找到可用的 python，跳过接线探针' -ForegroundColor Yellow
    }
}
else {
    Write-Host '  [INFO] scripts\dev\health_probe.py 不存在（跳过）' -ForegroundColor Yellow
}

Write-Host ''
if ($missing -eq 0) {
    Write-Host '  ✅ 全栈就绪，可以真机测试' -ForegroundColor Green
    exit 0
}
else {
    Write-Host ("  ❌ 有 {0} 项缺失（上方 [MISS]）" -f $missing) -ForegroundColor Red
    Write-Host '     起全栈：pwsh -File scripts\dev\start_all.ps1'
    exit 1
}
