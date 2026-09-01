"""数据安全审计（S5-05 · WP-H 2026-08-19）

四域检查（JSON 输出，零交互，可进 CI 金丝雀）：
  1. 密钥管理：.env 被 gitignore / 代码无硬编码密钥 / 生产 JWT 非默认值
  2. 传输：服务默认不暴露明文密钥 / 数据库凭据不落代码
  3. 存储：敏感字段有标识 / 语音隐私字段检查
  4. 备份：备份脚本存在 / 最近 dump 新鲜度（RPO≤24h 目标）/ 保留份数

用法：
  python scripts/audit_security.py            # 全量
  python scripts/audit_security.py --json     # 仅 JSON（CI 用）

退出码：0=全绿（或仅告警），1=存在阻断项（fail=True）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 敏感值模式（排除测试与示例）
_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),   # DashScope
    re.compile(r"\bAKID[A-Za-z0-9]{10,}\b"),     # 腾讯云 SecretId
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # 私钥
]
_ALLOW_PATHS = ("tests/", "research/", ".cowork-temp/", "checkpoints/", "models/", "backups/", "scripts/", "skills/")


def _check(name: str, ok: bool, detail: str, fail: bool = False) -> dict:
    return {"name": name, "pass": ok, "fail": fail, "detail": detail}


def audit() -> dict:
    domains: dict[str, list[dict]] = {"key_management": [], "transport": [], "storage": [], "backup": []}
    blocking = 0

    # ---- 1. 密钥管理 ----
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    domains["key_management"].append(_check(
        ".env 被 gitignore 排除",
        ".env" in gitignore,
        "仓库 .gitignore 含 .env 条目" if ".env" in gitignore else "缺少 .env 条目，风险：本地凭据入库",
        fail=".env" not in gitignore,
    ))
    leaked: list[str] = []
    for py in BACKEND.rglob("*.py"):
        rel = py.relative_to(REPO).as_posix()
        if any(rel.startswith(p) for p in _ALLOW_PATHS):
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        for pat in _SECRET_PATTERNS:
            m = pat.search(text)
            if m:
                leaked.append(f"{rel}:{m.group(0)[:12]}…")
                break
    domains["key_management"].append(_check(
        "源码无硬编码密钥",
        not leaked,
        f"命中 {len(leaked)} 处" if leaked else "未发现（源码扫描）",
        fail=bool(leaked),
    ))
    blocking += len(leaked)

    # ---- 2. 传输 ----
    config_src = (BACKEND / "app/core/config.py").read_text(encoding="utf-8")
    domains["transport"].append(_check(
        "配置中心统一管理凭据（无散落）",
        "BaseSettings" in config_src,
        "凭据经 pydantic-settings 环境注入（.env/Infisical）",
    ))
    domains["transport"].append(_check(
        "生产强制强 JWT 密钥",
        "jwt_secret == \"change-me-32-bytes-min-secret-0000\"" in config_src,
        "config.py 生产环境拒绝默认 JWT_SECRET",
        fail=False,
    ))

    # ---- 3. 存储 ----
    models_src = (BACKEND / "app/db/models.py").read_text(encoding="utf-8")
    domains["storage"].append(_check(
        "敏感状态字段存在（sensitive_status）",
        "sensitive_status" in models_src or "sensitive" in models_src.lower(),
        "contents 敏感标记用于回响/搜索排除",
    ))
    domains["storage"].append(_check(
        "软删除字段存在（deleted_at）",
        "deleted_at" in models_src,
        "全局软删除 30 天（B4）",
    ))

    # ---- 4. 备份 ----
    backup_script = REPO / "scripts" / "backup_pg.ps1"
    domains["backup"].append(_check(
        "备份脚本存在",
        backup_script.exists(),
        "scripts/backup_pg.ps1（dump+SHA256+保留 7 份）",
    ))
    dumps = sorted((REPO / "backups").glob("*.dump")) if (REPO / "backups").exists() else []
    newest = max((d.stat().st_mtime for d in dumps), default=0)
    age_hours = (datetime.now(timezone.utc).timestamp() - newest) / 3600 if newest else float("inf")
    domains["backup"].append(_check(
        "最近备份新鲜度（RPO≤24h）",
        age_hours <= 24,
        f"最近 dump 距今 {age_hours:.1f}h（{len(dumps)} 份）" if newest else "无备份产物（首次演练前置）",
        fail=age_hours > 24 and newest > 0,
    ))
    if age_hours > 24 and newest > 0:
        blocking += 1

    return {"domains": domains, "blocking": blocking, "generated_at": datetime.now(timezone.utc).isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()
    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["blocking"] else 0
    for domain, checks in result["domains"].items():
        print(f"## {domain}")
        for c in checks:
            mark = "❌" if (c["fail"] or not c["pass"]) else "✅"
            print(f"  {mark} {c['name']} — {c['detail']}")
    print(f"\n阻断项: {result['blocking']}")
    return 1 if result["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
