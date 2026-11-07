# agent 服务 Windows 启动脚本（2026-10-04 新增）
#
# 为什么需要这个脚本（踩坑记录）：
#   本机 shell 里**长期存在**一个陈旧的 DASHSCOPE_API_KEY 系统环境变量（Token Plan 的
#   sk-sp- key）。而 llm_provider / db.py 都用 `load_dotenv(override=False)`，
#   即**进程环境变量优先于 .env**（.env.example 写明的设计，为容器/K8s 注入留口）。
#   于是：改了 agent/.env 也不生效，服务仍拿系统里的旧 key →
#   ① 旧 key 是套餐专属形态 ⇒ base_url 被推成 token-plan 端点，配上 .env 里的
#      workspace header ⇒ 400 "header value must match the workspace bound to the API key"；
#   ② 就算绕开header，套餐未开通模型 ⇒ 403 AccessDenied.Unpurchased。
#   同一个陈旧变量还会污染**后端**：后端护栏用系统里的旧 key 调百炼 ⇒ 401 ⇒
#   fail-closed ⇒ **每一句 AI 回复都被拦成 422**（表现为"AI 很不稳定"）。
#
# 所以启动前必须清掉继承来的 DASHSCOPE_*，让服务只认自己的 .env。
# 后端同理：启动前清 DASHSCOPE_*（或用本脚本的 -ClearDashScope 段）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File agent\scripts\local_run.ps1
param(
    [int]$Port =8300,
    [switch]$NoCleanEnv
)

$ErrorActionPreference = 'Stop'
$agentRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$srcDir = Join-Path $agentRoot 'src'

if (-not $NoCleanEnv) {
    $stale = Get-ChildItem Env: | Where-Object { $_.Name -like 'DASHSCOPE*' }
    if ($stale) {
        Write-Host '[env] 清理继承来的陈旧变量（agent 只认 agent/.env）:'
        $stale | ForEach-Object {
            Write-Host ("      - " + $_.Name)
            Remove-Item("Env:\" + $_.Name) -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host '[env] 无DASHSCOPE_* 残留'
    }
}

# 端口占用预检：避免"服务起不来"被误判成代码问题
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Warning ("[port] {0} 已被占用（PID {1}）——先停掉旧进程再启动" -f $Port, $busy[0].OwningProcess)
}

if (-not (Test-Path (Join-Path $agentRoot '.env'))) {
    throw "缺少 agent/.env —— 复制 .env.example 并填 key"
}

Write-Host ("[run] uvicorn main:app  127.0.0.1:{0}  (cwd={1})" -f $Port, $srcDir)
Set-Location $srcDir
python -m uvicorn main:app --host 127.0.0.1 --port $Port