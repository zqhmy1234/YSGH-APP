#requires -Version 5.1
<#
  忆述光华 · 本机服务栈一键启动（2026-10-11）

  用途：把「手机装 APK 直连本机后端」所需的**全部本机进程**一次起齐，幂等可重跑。
        此前这一步散落在 .cowork-temp/rt_*.ps1（gitignore 的临时件）与各文档的散文里，
        换机器/隔天就漏起一个（最常见漏 worker ⇒ 内容永远卡 processing；漏 agent ⇒ AI 页 502）。

  起什么（顺序即依赖顺序）：
    1) Docker 依赖      yishu-redis(6379) / yishu-qdrant(6333)
    2) backend          127.0.0.1:8010   （cwd=backend）
    3) RQ worker        （cwd=backend，队列 high/low）
    4) agent            127.0.0.1:8300   （cwd=agent/src；**规范端口 8300**）
    5) 健康探针         逐个探活，失败打印原因（不静默）

  用法：
      pwsh -File scripts\dev\start_all.ps1
      pwsh -File scripts\dev\start_all.ps1 -SkipDocker          # Docker 已在跑
      pwsh -File scripts\dev\start_all.ps1 -MockExternalAi      # 百炼凭据失效期过渡（审核链 mock 直通）

  端口口径（2026-10-11 统一，见 docs/决策台账.md）：
      backend 8010 ｜ agent **8300**（规范值；历史临时件曾用 8399，那是当时 backend/.env
      未设 AGENT_SERVICE_BASE_URL 时的临时覆盖，**不是**规范值）

  启动日志：.cowork-temp/run_{backend,worker,agent}.log（不入库）

  退出码：0 = 全部就绪；1 = 有服务未就绪（打印逐项状态）。
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [string]$BackendHost = '0.0.0.0',
    [int]$BackendPort = 8010,
    [int]$AgentPort = 8300,
    [switch]$SkipDocker,
    [switch]$SkipAgent,
    [switch]$MockExternalAi,
    [string]$LlmApiKeyEnvName = 'deepseek-ugsimulator',
    [string]$Python = '',
    [string]$LocalKeysFile = ''
)

$ErrorActionPreference = 'Continue'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $here = $null
    if ($PSScriptRoot) { $here = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $here = (Get-Location).Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
}
$logDir = Join-Path $RepoRoot '.cowork-temp'
New-Item -ItemType Directory -Force $logDir | Out-Null

if ([string]::IsNullOrWhiteSpace($Python)) {
    $pythonCandidates = @(
        (Join-Path (Split-Path $RepoRoot -Parent) 'ysgh_venv\Scripts\python.exe'),
        (Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot '.venv\Scripts\python.exe')
    )
    $Python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($Python) -or -not (Test-Path $Python)) {
    Write-Host "  [FAIL] 找不到项目虚拟环境 Python" -ForegroundColor Red
    Write-Host "         -> 用 -Python <python.exe> 指定，或创建 backend/.venv" -ForegroundColor Yellow
    exit 1
}
& $Python -c "import uvicorn, redis" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [FAIL] 指定 Python 缺少 uvicorn/redis：$Python" -ForegroundColor Red
    Write-Host "         -> 先安装 backend/requirements.txt（不要在系统 Python 3.14 上直接跑）" -ForegroundColor Yellow
    exit 1
}

if ([string]::IsNullOrWhiteSpace($LocalKeysFile)) {
    $candidateKeys = Join-Path (Split-Path $RepoRoot -Parent) 'photo_pipeline\_local_keys.env'
    if (Test-Path $candidateKeys) { $LocalKeysFile = $candidateKeys }
}
if (-not [string]::IsNullOrWhiteSpace($LocalKeysFile) -and (Test-Path $LocalKeysFile)) {
    foreach ($line in Get-Content $LocalKeysFile -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $keyName = $Matches[1]
            $keyValue = $Matches[2]
            if (-not [string]::IsNullOrWhiteSpace($keyValue)) {
                Set-Item -Path ("Env:" + $keyName) -Value $keyValue
            }
        }
    }
}

function Info($m) { Write-Host "  [INFO] $m" -ForegroundColor Gray }
function Ok($m) { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn2($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Bad($m, $fix) {
    Write-Host "  [FAIL] $m" -ForegroundColor Red
    if ($fix) { Write-Host "         -> $fix" -ForegroundColor Yellow }
}
function Step($m) { Write-Host ''; Write-Host "== $m ==" -ForegroundColor Cyan }

function Test-Listening([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $c -and @($c).Count -gt 0)
}

function Test-Http([string]$Url) {
    try {
        $r = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 6
        return ($r.StatusCode -eq 200)
    }
    catch { return $false }
}

Write-Host ''
Write-Host '忆述光华 · 本机服务栈' -ForegroundColor White
Info "仓库根 = $RepoRoot ｜ backend ${BackendHost}:$BackendPort ｜ agent :$AgentPort"
Info "Python = $Python"
if (-not [string]::IsNullOrWhiteSpace($LocalKeysFile) -and (Test-Path $LocalKeysFile)) {
    Info "本地密钥文件已注入进程环境（不回显值）：$LocalKeysFile"
}

# ─────────────────────────── 0. 前置检查 ───────────────────────────
Step '0. 前置'
if (Test-Listening $BackendPort) { Info "backend :$BackendPort 已在监听（将跳过启动）" }
if (Test-Listening $AgentPort) { Info "agent :$AgentPort 已在监听（将跳过启动）" }

# ─────────────────────────── 1. Docker 依赖 ───────────────────────────
Step '1. Docker 依赖（Redis / Qdrant）'
if ($SkipDocker) {
    Info '按 -SkipDocker 跳过'
}
else {
    $dockerOk = $false
    try { docker info *> $null; $dockerOk = ($LASTEXITCODE -eq 0) } catch { $dockerOk = $false }
    if (-not $dockerOk) {
        Warn2 'Docker daemon 不可用：Redis/Qdrant 未起 ⇒ 入库与检索会失败（"时间轴空"的头号原因）'
        Info '启动 Docker Desktop 后重跑本脚本，或加 -SkipDocker 表示已知情'
    }
    else {
        foreach ($c in @('yishu-redis', 'yishu-qdrant')) {
            $state = (docker inspect -f '{{.State.Status}}' $c 2>$null)
            if ($state -eq 'running') { Ok "$c 已在运行" }
            else {
                docker start $c *> $null
                Start-Sleep -Seconds 2
                $state2 = (docker inspect -f '{{.State.Status}}' $c 2>$null)
                if ($state2 -eq 'running') { Ok "$c 已启动" } else { Warn2 "$c 启动失败（当前状态：$state2）" }
            }
        }
        $pong = (docker exec yishu-redis redis-cli ping 2>$null)
        if ($pong -match 'PONG') { Ok 'Redis PING → PONG' } else { Warn2 'Redis 未响应 PING' }
    }
}

# ─────────────────────────── 2. backend ───────────────────────────
Step "2. backend（${BackendHost}:$BackendPort）"
if (Test-Listening $BackendPort) {
    Ok "已在监听，跳过"
}
else {
    $env:PYTHONIOENCODING = 'utf-8'
    # ⚠️ 指向**本脚本用的 agent 端口**（backend/.env 通常未设该项 ⇒ 走 config.py 默认 8300；
    #    这里显式对齐，避免"agent 起了但 backend 找的是另一个端口"这种静默错配）。
    $env:AGENT_SERVICE_BASE_URL = "http://127.0.0.1:$AgentPort"
    # agent 共享密钥：从 agent/.env 读（两边必须一致，否则 401）
    $agentEnvPath = Join-Path $RepoRoot 'agent\.env'
    if (Test-Path $agentEnvPath) {
        $tokLine = Select-String -Path $agentEnvPath -Pattern '^AGENT_SERVICE_TOKEN=' -ErrorAction SilentlyContinue
        if ($tokLine) {
            $env:AGENT_SERVICE_TOKEN = ($tokLine.Line -split '=', 2)[1].Trim()
            Info 'AGENT_SERVICE_TOKEN 已从 agent/.env 注入'
        }
        else { Warn2 'agent/.env 里没有 AGENT_SERVICE_TOKEN（agent 侧默认不校验，但生产必须配）' }
    }
    else { Warn2 'agent/.env 不存在 ⇒ 无法注入 AGENT_SERVICE_TOKEN' }
    if ($MockExternalAi) {
        $env:MOCK_EXTERNAL_AI = 'true'
        Warn2 'MOCK_EXTERNAL_AI=true：审核链走 mock 直通（**仅限百炼凭据失效期过渡**，续期后必须关掉）'
    }
    $out = Join-Path $logDir 'run_backend.log'
    Start-Process $Python -WorkingDirectory (Join-Path $RepoRoot 'backend') `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', $BackendHost, '--port', "$BackendPort", '--log-level', 'warning') `
        -RedirectStandardOutput $out -RedirectStandardError "$out.err" -WindowStyle Hidden | Out-Null
    Info "已启动（日志 $out）"
}

# ─────────────────────────── 3. RQ worker ───────────────────────────
Step '3. RQ worker（异步管线：AI 分类/地名/聚合）'
$workerRunning = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'app\.workers\.worker' }).Count -gt 0
if ($workerRunning) {
    Ok 'worker 已在运行，跳过'
}
else {
    $env:PYTHONIOENCODING = 'utf-8'
    $out = Join-Path $logDir 'run_worker.log'
    Start-Process $Python -WorkingDirectory (Join-Path $RepoRoot 'backend') `
        -ArgumentList @('-m', 'app.workers.worker') `
        -RedirectStandardOutput $out -RedirectStandardError "$out.err" -WindowStyle Hidden | Out-Null
    Info "已启动（日志 $out）"
}

# ─────────────────────────── 4. agent ───────────────────────────
Step "4. agent（127.0.0.1:$AgentPort）"
if ($SkipAgent) {
    Info '按 -SkipAgent 跳过'
}
elseif (Test-Listening $AgentPort) {
    Ok '已在监听，跳过'
}
else {
    $src = Join-Path $RepoRoot 'agent\src'
    if (-not (Test-Path (Join-Path $RepoRoot 'agent\.env'))) {
        Bad '缺少 agent/.env' '复制 agent/.env.example 为 agent/.env 并填 key（本文件不入库）'
    }
    else {
        $env:PYTHONIOENCODING = 'utf-8'
        # 清继承来的陈旧 DASHSCOPE_*（踩坑记录见 agent/scripts/local_run.ps1 头注释：
        # 系统里的旧 key 会覆盖 .env，导致 401/403 与"AI 回复总被拦"）
        Get-ChildItem Env: | Where-Object { $_.Name -like 'DASHSCOPE*' } | ForEach-Object {
            Remove-Item ("Env:\" + $_.Name) -ErrorAction SilentlyContinue
        }
        # LLM 凭据：优先用 agent/.env（load_dotenv 不覆盖已存在的进程变量），
        # 若系统里有约定的 key 变量则注入（本机既有做法，来源见 .cowork-temp/rt_agent.ps1）
        $sysKey = [System.Environment]::GetEnvironmentVariable($LlmApiKeyEnvName)
        if ($sysKey) {
            $env:AGENT_LLM_API_KEY = $sysKey
            $env:AGENT_LLM_BASE_URL = 'https://api.deepseek.com/v1'
            $env:AGENT_LLM_MODEL = 'deepseek-chat'
            Info "已按系统变量 $LlmApiKeyEnvName 注入 LLM 凭据（DeepSeek）"
        }
        else {
            Info "系统变量 $LlmApiKeyEnvName 不存在 ⇒ 用 agent/.env 里的 LLM 配置"
        }
        $out = Join-Path $logDir 'run_agent.log'
        Start-Process $Python -WorkingDirectory $src `
            -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', "$AgentPort", '--log-level', 'info') `
            -RedirectStandardOutput $out -RedirectStandardError "$out.err" -WindowStyle Hidden | Out-Null
        Info "已启动（日志 $out）"
    }
}

# ─────────────────────────── 5. 健康探针 ───────────────────────────
Step '5. 健康探针'
$deadline = (Get-Date).AddSeconds(40)
$backendOk = $false
$agentOk = $false
while ((Get-Date) -lt $deadline -and (-not ($backendOk -and $agentOk))) {
    Start-Sleep -Seconds 2
    if (-not $backendOk) { $backendOk = Test-Http "http://127.0.0.1:$BackendPort/healthz" }
    if (-not $SkipAgent -and -not $agentOk) { $agentOk = Test-Http "http://127.0.0.1:$AgentPort/health" }
}
if ($backendOk) { Ok "backend /healthz OK" } else { Bad "backend /healthz 未通过" "看 .cowork-temp/run_backend.log.err（常见：PG/Redis 未起、依赖缺失）" }
if ($SkipAgent) { Info 'agent 已按开关跳过' } elseif ($agentOk) { Ok "agent /health OK" } else { Bad "agent /health 未通过" "看 .cowork-temp/run_agent.log.err（常见：agent/.env 的 LLM key 无效）" }
if ($workerRunning -or (Test-Path (Join-Path $logDir 'run_worker.log'))) { Ok 'worker 已就绪（无 HTTP 探针；看 .cowork-temp/run_worker.log）' }

Write-Host ''
if ($backendOk -and ($SkipAgent -or $agentOk)) {
    Write-Host '  ✅ 服务栈就绪。下一步：' -ForegroundColor Green
    Write-Host '     · 起隧道（手机可直连）：pwsh -File scripts\dev\tunnel.ps1 -Provider ngrok -StaticDomain <你的静态域名>'
    Write-Host '     · 回写出包三件套      ：pwsh -File scripts\dev\write_base_url.ps1'
    Write-Host '     · 看全栈状态          ：pwsh -File scripts\dev\status.ps1'
    Write-Host ''
    exit 0
}
else {
    Write-Host '  ❌ 有服务未就绪（见上方逐项状态与日志路径）' -ForegroundColor Red
    Write-Host ''
    exit 1
}
