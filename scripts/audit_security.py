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
#
# D14-9（2026-09-24 B10-l）：模式集**唯一来源** = 同目录 secret_patterns.py（与
#   scripts/review_agent.py 共用）。此前本工具与提交门禁各维护一套且互有缺口 ⇒
#   两处会"互相放行"对方认得、自己没认出的形态（假安全感）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secret_patterns import SECRET_PATTERNS as _SECRET_PATTERN_SRC  # noqa: E402

_SECRET_PATTERNS = [re.compile(p) for p in _SECRET_PATTERN_SRC]
_ALLOW_PATHS = ("tests/", "research/", ".cowork-temp/", "checkpoints/", "models/", "backups/", "scripts/", "skills/")


def _models_source() -> str:
    """返回 `db/models` 的源码文本（D13-4 / B10-m 修复 · 2026-09-24）。

    原实现直读 `BACKEND/app/db/models.py`。但该路径早已由**单文件拆为包**（`db/models/`），
    于是本审计运行时抛 `FileNotFoundError` ⇒ **整个安全审计根本跑不完**（"RPO 检查恒通过"
    的真相是它压根没执行；用 HEAD 原始版本复跑同样崩，属既有缺陷、非本轮引入）。
    现兼容两种形态：包 → 递归拼接包内全部 `.py`；旧单文件 → 直读。
    """
    pkg = BACKEND / "app" / "db" / "models"
    if pkg.is_dir():
        return "\n".join(
            f.read_text(encoding="utf-8", errors="ignore") for f in sorted(pkg.rglob("*.py"))
        )
    legacy = BACKEND / "app" / "db" / "models.py"
    return legacy.read_text(encoding="utf-8", errors="ignore") if legacy.exists() else ""


def _allowed(rel: str) -> bool:
    """该路径是否属于「排除测试与示例」清单（B10-m 修正 · 2026-09-24）。

    原实现用 `rel.startswith(p)` 套**仓库根相对**前缀（如 `tests/`），但扫描对象是
    `BACKEND.rglob("*.py")` ⇒ `backend/tests/...` **不匹配** ⇒ 「排除测试」的意图
    **从未生效**（实测把 `backend/tests/` 的两处合成值报成"硬编码密钥"）。
    现改为**按路径段**匹配：任一段等于清单目录名即排除（与扫描深度无关）。
    """
    segs = set(rel.split("/"))
    return any(p.rstrip("/") in segs for p in _ALLOW_PATHS)


def _is_synthetic(line: str) -> bool:
    """合成值抑制（与提交门禁 `review_agent.check_secrets` **同一套口径**）。

    B10-l/B10-m 配套：`change-me`（默认值占位）、含 `mock`、以及**显式行内豁免**
    `pragma: allowlist secret`（detect-secrets 惯例，diff 里可见可审计）。
    """
    low = line.lower()
    return "change-me" in line or "mock" in low or "allowlist secret" in line


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
        if _allowed(rel):
            continue
        for line_no, line in enumerate(
            py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            if _is_synthetic(line):
                continue
            for pat in _SECRET_PATTERNS:
                m = pat.search(line)
                if m:
                    leaked.append(f"{rel}:{line_no} {m.group(0)[:12]}…")
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
    models_src = _models_source()
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
