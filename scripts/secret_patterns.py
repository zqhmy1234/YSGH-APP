"""门禁共享的**疑似密钥模式集**（唯一来源 · D14-9 · 2026-09-24 重构波 B10-l）。

为什么合并：`scripts/review_agent.py`（提交门禁）与 `scripts/audit_security.py`（安全审计）
此前**各维护一套**媒体模式，且**互有缺口**：
  · review 认得 `AKIA…`（AWS）/`ghp_…`/`sk_live_…`/通用配置键赋值，但**不认** `AKID…`（腾讯云）；
  · audit 认得 `AKID…`/`\\bsk-…\\b`（DashScope）/私钥，但不认 GitHub/Slack/Stripe/Google 等形态。
⇒ **同一份密钥可能被其中一个放行**（"两套规则各自为政"＝假安全感）。
本模块给出**并集**，两处共同 import：**改规则只改这里**（改一处、两处生效）。

用法：
    from secret_patterns import SECRET_PATTERNS   # 字符串形式；需要 compiled 时自行 re.compile
"""
from __future__ import annotations

SECRET_PATTERNS: list[str] = [
    # ── 原 review_agent（提交门禁）──
    r"sk-[A-Za-z0-9]{20,}",        # OpenAI/DeepSeek 风格 key（窄版；宽版见下）
    r"AKIA[0-9A-Z]{16}",           # AWS access key
    r"-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
    r"password\s*=\s*['\"][^'\"]+['\"]",
    r"secret\s*=\s*['\"][^'\"]{8,}['\"]",
    # 2026-08-27（批次 H2 R7 · T9）：补齐常见密钥形态盲区
    r"ghp_[A-Za-z0-9]{36}",                       # GitHub PAT
    r"github_pat_[A-Za-z0-9_]{22,}",              # GitHub fine-grained PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}",              # Slack token
    r"AIza[0-9A-Za-z_-]{35}",                     # Google API key
    r"sk_live_[0-9a-zA-Z]{20,}",                  # Stripe secret key
    r"rk_live_[0-9a-zA-Z]{20,}",                  # Stripe restricted key
    r"glpat-[A-Za-z0-9-]{20,}",                   # GitLab PAT
    # 通用配置键赋值（小写精确匹配，误报面已用仓库全量探测验证 = 0）
    r"(client_secret|access_token|api[_-]?key|secret_key|private_key)\s*=\s*['\"][^'\"]{8,}['\"]",
    # ── 原 audit_security（安全审计）——此前**提交门禁看不到**，D14-9 合并 ──
    r"\bsk-[A-Za-z0-9_\-]{16,}\b",                # DashScope（宽版：含 -/_ 且 16 起）
    r"\bAKID[A-Za-z0-9]{10,}\b",                  # 腾讯云 SecretId（与 AWS 的 AKIA 是**不同前缀**）
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",        # 私钥（更宽：任意算法）
]
