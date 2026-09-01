# 忆述光华 · PostgreSQL 备份脚本（S1-08 / 决策 #13）
# 目标：RPO≤24h（WAL 后 ≤5min）、RTO≤4h（DR-001/005/006）
# 用法：设置环境变量后运行（Windows PowerShell）：
#   $env:PGPASSWORD="***"; .\scripts\backup_pg.ps1
# 产物：backups/yishu_YYYYMMDD_HHMMSS.dump + WAL 归档说明

param(
    [string]$PGBin = "C:\Program Files\PostgreSQL\17\bin",
    [string]$BackupDir = "D:\GuangH-App\backups",
    [string]$DbName = "yishu",
    [string]$DbUser = "yishu_app",
    [string]$DbHost = "localhost"
)

$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpFile = Join-Path $BackupDir "yishu_$ts.dump"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

Write-Host "=== 备份 $DbName @ $DbHost ==="

# 1. 逻辑备份（pg_dump -Fc 压缩格式，每日基线）
& "$PGBin\pg_dump.exe" -U $DbUser -h $DbHost -d $DbName -Fc -f $dumpFile
if ($LASTEXITCODE -ne 0) { throw "pg_dump 失败" }
Write-Host "逻辑备份完成: $dumpFile ($([math]::Round((Get-Item $dumpFile).Length/1MB,1)) MB)"

# 2. WAL 归档提示（PITR 到分钟级，RPO≤5min）
#   生产环境需配置 postgresql.conf: wal_level=replica + archive_mode=on + archive_command
#   本地开发 MVP：每日 dump 已满足 RPO≤24h；WAL 归档在部署阶段启用（T4 职责）
Write-Host "提示: 生产 WAL 归档（RPO≤5min）在部署阶段启用（wal_level=replica + archive_command）"

# 3. 完整性校验（DR-007：坏备份被哈希发现）
$hash = Get-FileHash $dumpFile -Algorithm SHA256
Write-Host "SHA256: $($hash.Hash)"
$hash.Hash | Out-File "$dumpFile.sha256"

# 4. 保留策略：保留最近 7 个全量备份，更早的清理（MVP 够用）
$old = Get-ChildItem $BackupDir -Filter "yishu_*.dump" | Sort-Object Name -Descending | Select-Object -Skip 7
foreach ($f in $old) {
    Write-Host "清理旧备份: $($f.Name)"
    Remove-Item $f.FullName -Force
    Remove-Item "$($f.FullName).sha256" -Force -ErrorAction SilentlyContinue
}

Write-Host "=== 备份完成 ==="
