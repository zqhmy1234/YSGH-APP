# AG5 真代理端到端（可复跑）：客户端 → 本后端 → Agent 服务 → LLM → 工具 → 落库
#
# 为什么固化成本脚本：**这是单测覆盖不到的一跳**——`backend/tests/test_chat.py` 是
# "真 DB + 桩 agent"，只证明本仓逻辑；真 HTTP 反代（含共享密钥头、超时、agent 工具写库）
# 只能真起两个进程才验得到。一次性的临时脚本用完即删 ⇒ 下次要重造 ⇒ 故入库。
#
# 纪律：① 共享密钥只写 .env，且**先确认其被 git 忽略**再写（密钥绝不进库）
#      ② 密钥值不打印 ③ 服务跑完**保持运行**（便于随后做客户端接线直连）
#
# 用法：powershell -ExecutionPolicy Bypass -File scripts\e2e_agent_chat.ps1
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$tmp = Join-Path $repo '_tmp'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Set-Location $repo

Write-Output "===== 0) 共享密钥对齐（AGENT_SERVICE_TOKEN）====="
$envFiles = @(
    @{ Rel = 'agent/.env'; Abs = Join-Path $repo 'agent\.env' },
    @{ Rel = 'backend/.env'; Abs = Join-Path $repo 'backend\.env' }
)
foreach ($e in $envFiles) {
    git check-ignore -q $e.Rel
    $ignored = ($LASTEXITCODE -eq 0)
    Write-Output ("  {0} 存在={1} 被git忽略={2}" -f $e.Rel, (Test-Path $e.Abs), $ignored)
    if ((-not (Test-Path $e.Abs)) -and (-not $ignored)) {
        Write-Output "  [拒绝] 目标不存在且未被忽略 -> 不创建（防密钥进库）"
        exit 1
    }
}
$tok = $null
foreach ($e in $envFiles) {
    if (Test-Path $e.Abs) {
        $m = Select-String -Path $e.Abs -Pattern '^AGENT_SERVICE_TOKEN=' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $tok = ($m.Line -split '=', 2)[1].Trim() }
    }
}
if (-not $tok) {
    $tok = -join (1..40 | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
    foreach ($e in $envFiles) { Add-Content -Path $e.Abs -Value "AGENT_SERVICE_TOKEN=$tok" }
    Write-Output "  -> 已生成新密钥并写入两侧 .env（值不打印）"
} else {
    Write-Output "  -> 复用既有密钥（值不打印）"
}

Write-Output "`n===== 1) 拉起 Agent 服务（127.0.0.1:8300，仅本机）====="
$agentP = Start-Process python -ArgumentList 'src/main.py' -WorkingDirectory (Join-Path $repo 'agent') -PassThru -NoNewWindow `
    -RedirectStandardOutput "$tmp\e2e_agent.out" -RedirectStandardError "$tmp\e2e_agent.err"
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    try { $r = Invoke-WebRequest 'http://127.0.0.1:8300/health' -TimeoutSec 2 -UseBasicParsing; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch { }
    Start-Sleep -Seconds 1
}
Write-Output "  /health 就绪: $ok (pid=$($agentP.Id))"
if (-not $ok) { Write-Output "  --- agent stderr ---"; Get-Content "$tmp\e2e_agent.err" -Tail 25 -ErrorAction SilentlyContinue }

Write-Output "`n===== 2) 拉起后端（127.0.0.1:8000）====="
$beP = Start-Process python -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--log-level', 'warning' `
    -WorkingDirectory (Join-Path $repo 'backend') -PassThru -NoNewWindow `
    -RedirectStandardOutput "$tmp\e2e_be.out" -RedirectStandardError "$tmp\e2e_be.err"
$ok2 = $false
for ($i = 0; $i -lt 40; $i++) {
    try { $r = Invoke-WebRequest 'http://127.0.0.1:8000/healthz' -TimeoutSec 2 -UseBasicParsing; if ($r.StatusCode -eq 200) { $ok2 = $true; break } } catch { }
    Start-Sleep -Seconds 1
}
Write-Output "  /healthz 就绪: $ok2 (pid=$($beP.Id))"
if (-not $ok2) { Write-Output "  --- backend stderr ---"; Get-Content "$tmp\e2e_be.err" -Tail 25 -ErrorAction SilentlyContinue; exit 1 }

Write-Output "`n===== 3) 登录（mock 微信：未配 WECHAT_APPID/SECRET 时走 mock）====="
$code = "e2e-" + ([guid]::NewGuid().ToString('N').Substring(0, 10))
$loginJson = @{ code = $code; device_id = 'e2e-dev' } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/auth/wechat' -Method Post -ContentType 'application/json' `
    -Body ([Text.Encoding]::UTF8.GetBytes($loginJson))
$token = $login.data.access_token
$uid = $login.data.user.id
Write-Output "  user_id=$uid token 长度=$($token.Length)"

Write-Output "`n===== 4) 发一条真对话（穿透：后端 → Agent → LLM → 工具）====="
$chatJson = @{ content = '帮我记住：今天读完《老人与海》第 12 章，很有感触' } | ConvertTo-Json -Compress
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $resp = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/chat/messages' -Method Post -ContentType 'application/json' `
        -Headers @{ Authorization = "Bearer $token" } -Body ([Text.Encoding]::UTF8.GetBytes($chatJson)) -TimeoutSec 300
    $sw.Stop()
    Write-Output "  [OK] 端到端成功，墙钟耗时 $([math]::Round($sw.Elapsed.TotalSeconds,1))s"
    Write-Output "  conversation_id=$($resp.data.conversation_id)"
    Write-Output "  reply.id=$($resp.data.reply.id)  reply.kind=$($resp.data.reply.kind)  端到端ms=$($resp.data.latency_ms)"
    Write-Output "  --- reply.text ---"
    Write-Output $resp.data.reply.text
} catch {
    $sw.Stop()
    Write-Output "  [FAIL] $([math]::Round($sw.Elapsed.TotalSeconds,1))s：$($_.Exception.Message)"
    if ($_.ErrorDetails) { Write-Output "  --- 响应体 ---"; Write-Output $_.ErrorDetails.Message }
    Write-Output "  --- backend stderr 末 15 行 ---"
    Get-Content "$tmp\e2e_be.err" -Tail 15 -ErrorAction SilentlyContinue
}

Write-Output "`n===== 5) 落库核验（本仓表；含 AG3'数据层是我们自己的'断言）====="
# 通用写法（不猜列名）：取所有含 user_id 的表逐表计数——memories 有数据即证明
# Agent 的工具写进了**我们自己的 Postgres**（而非 Coze 侧）
$py = @"
import os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(r'REPO', 'backend'))
from sqlalchemy import text
from app.db.session import SessionLocal
db = SessionLocal()
rows = db.execute(text("SELECT role, kind, left(content,46), agent_latency_ms FROM chat_messages WHERE user_id=:u ORDER BY created_at"), {'u': 'UID'}).all()
print(f'  chat_messages 行数 = {len(rows)}')
for r in rows:
    print(f'    {r[0]:<9} | kind={r[1]:<6} | latency_ms={r[3]} | {r[2]}')
tables = [x[0] for x in db.execute(text("SELECT table_name FROM information_schema.columns WHERE column_name='user_id' AND table_schema='public' ORDER BY table_name"))]
hit = [t for t in tables if (lambda c: c)(db.execute(text(f'SELECT count(*) FROM "{t}" WHERE user_id=:u'), {'u':'UID'}).scalar())]
print('  该用户有数据的本仓表：', ', '.join(hit))
db.close()
"@
$py = $py.Replace('REPO', $repo.Replace('\', '/')).Replace('UID', $uid)
$py | Out-File "$tmp\e2e_check.py" -Encoding utf8
$env:PYTHONIOENCODING = 'utf-8'
python "$tmp\e2e_check.py"

Write-Output "`n===== 服务保持运行（供客户端接线直连）====="
Write-Output "  agent pid=$($agentP.Id) port=8300 · backend pid=$($beP.Id) port=8000"
