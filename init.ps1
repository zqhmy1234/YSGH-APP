# 忆述光华 Harness 初始化（Windows PowerShell 版，等价于 init.sh）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== 忆述光华 Harness 初始化 ==="

# 1. 交付文档完整性检查（18 份必须存在）
$docs = @(
  "忆述光华_交付文档/忆述光华_开工总结.md",
  "忆述光华_交付文档/忆述光华_MVP方案_v3.md",
  "忆述光华_交付文档/忆述光华_开发决策清单.md",
  "忆述光华_交付文档/忆述光华_开发规划+分工.md",
  "忆述光华_交付文档/忆述光华_测试清单.md",
  "忆述光华_交付文档/忆述光华_数据库Schema设计.md",
  "忆述光华_交付文档/忆述光华_外部API清单与成本.md",
  "忆述光华_交付文档/忆述光华_产品部验收标准更新转达稿.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/01_用户画像系统_B1.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/02_RAG多路检索_B2.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/02b_RAG范式前沿补充.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/03_照片事件聚合_B3.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/04_数据同步与离线优先_B4.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/05a_语音双通道_B5a.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/05b_安全护栏_B5b.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/05c_分类纠错_B5c.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/05d_后台任务与录音_B5d.md",
  "忆述光华_交付文档/忆述光华_深度开发设计/05e_Windows桌面端_B5e.md"
)

Write-Host "--- 检查交付文档（$($docs.Count) 份）---"
$missing = $false
foreach ($f in $docs) {
  if (Test-Path $f) { Write-Host "  OK  $f" }
  else { Write-Host "  MISSING  $f"; $missing = $true }
}

# 2. Harness 文件检查（2026-10-11：与 init.sh **逐项对等**——原两版清单不一致，
#    ps1 少查台账/lessons/主题索引三个关键文档；新增 STATE.md：唯一「当前状态」载体）
Write-Host "--- 检查 harness 文件 ---"
foreach ($f in @(
    "AGENTS.md", "STATE.md", "feature_list.json", "progress.md", "session-handoff.md",
    "init.sh", "init.ps1",
    "docs/决策台账.md", "docs/lessons.md", "docs/lessons-主题索引.md"
  )) {
  if (Test-Path $f) { Write-Host "  OK  $f" }
  else { Write-Host "  MISSING  $f"; $missing = $true }
}

# 2.5 STATE.md 新鲜度（**软告警，不阻断**）——2026-10-11 新增：
# 状态文件漂移是本项目最大的文档债（AGENTS.md 的「当前状态」曾停在 43 天前）。
# 这里只提示，不阻断：漂移要靠"每次启动都看得见"来治，而不是靠拦人。
Write-Host "--- STATE.md 新鲜度 ---"
if (Test-Path STATE.md) {
  $sm = Select-String -Path STATE.md -Pattern "最后更新[^0-9]*([0-9]{4}-[0-9]{2}-[0-9]{2})" | Select-Object -First 1
  $headDate = (git log -1 --format=%cs 2>$null)
  if ($sm) {
    $stateDate = $sm.Matches[0].Groups[1].Value
    if ($headDate -and $stateDate -lt $headDate) {
      Write-Host "  WARN   STATE.md 最后更新 $stateDate 早于最新提交 $headDate —— 状态可能已漂移，请更新 STATE.md" -ForegroundColor Yellow
    } else {
      Write-Host "  OK  STATE.md 最后更新 $stateDate"
    }
  } else {
    Write-Host "  WARN   STATE.md 缺『最后更新: YYYY-MM-DD』行" -ForegroundColor Yellow
  }
} else {
  Write-Host "  WARN   STATE.md 不存在（当前状态无唯一载体）" -ForegroundColor Yellow
}

# 3. Git 状态
Write-Host "--- Git 状态 ---"
if (Test-Path .git) {
  git status --short
  Write-Host "  Git 仓库存在"
} else {
  Write-Host "  WARN: 非 Git 仓库"
}

if ($missing) {
  Write-Host "!!! 初始化失败：存在缺失文件" -ForegroundColor Red
  exit 1
}

Write-Host "=== 初始化通过 ==="
Write-Host ""
Write-Host "下一步："
Write-Host "1. 读 STATE.md —— 唯一「当前状态」（正在做什么 / 下一步 / 阻塞 / 关键数字）"
Write-Host "2. 读 docs/决策台账.md §5 待拍板与 §7 未关闭缺陷"
Write-Host "3. 读 feature_list.json 看特性状态（约定只允许 1 条 in-progress）"
Write-Host "4. 完成前重跑本脚本 + python scripts/review_agent.py --full 验证"
