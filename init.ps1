# 忆述光华 Harness 初始化（Windows PowerShell 版，等价于 init.sh）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== 忆述光华 Harness 初始化 ==="

# 1. 交付文档完整性检查（18 份必须存在）
$docs = @(
  "忆述光华_交付文档/忆述光华_开工总结README.md",
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

# 2. Harness 文件检查
Write-Host "--- 检查 harness 文件 ---"
foreach ($f in @("AGENTS.md", "feature_list.json", "progress.md", "init.sh", "session-handoff.md")) {
  if (Test-Path $f) { Write-Host "  OK  $f" }
  else { Write-Host "  MISSING  $f"; $missing = $true }
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
Write-Host "1. 读 feature_list.json 看特性状态"
Write-Host "2. 选一个未完成特性，只实现该特性"
Write-Host "3. 完成前重跑本脚本验证"
