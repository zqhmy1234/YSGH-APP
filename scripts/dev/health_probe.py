#!/usr/bin/env python
"""本机全栈「服务间接线」探针（2026-10-14 · 台账 §7.12）。

## 它回答的问题

`scripts/dev/status.ps1` 能回答"端口通不通"，但回答不了**"通着却不干活"**：

  1. backend 与 agent 的共享密钥**是不是同一把**？不一致时一侧 401/503、
     另一侧**静默降级**，而两侧日志都只显示"已配置"，没人能看出差异。
  2. agent 的 W1 记忆镜像 / W2 检索合并**有没有在持续降级**？降级的表现是
     「AI 说已记住、卡片也出了，但 App 时间轴 / 检索一片空白」。
  3. `backend/app/services/external/agent.py:health()` 这条**写了很久、零调用方**的
     探活函数，到底通不通？

三条都是"接口 200 但功能是假的"的典型。本探针把它们变成**一条命令、可判定**的事实。

## 用法

    python scripts/dev/health_probe.py            # 人类可读
    python scripts/dev/health_probe.py --json     # 机器可读（供脚本汇总）
    python scripts/dev/health_probe.py --no-net   # 只看配置对账，不发网络请求

## 退出码

    0 = 无矛盾（"agent 没起"这类**已由 status.ps1 负责**的存活问题不算失败）
    1 = 存在明确不一致（两侧密钥不同 / agent 报告有降级 / 进程内读到的密钥与配置文件不符）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "backend"
AGENT_ENV = REPO / "agent" / ".env"

# 让本探针能直接 import backend 的应用模块（其中 `app.core.diagnostics.token_fingerprint`
# 是**唯一**的指纹算法真源——探针复用它，避免自己实现一份而与服务端漂移）。
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _read_env(path: Path) -> dict[str, str]:
    """极简 .env 读取（只认 `KEY=VALUE`；不展开变量、不处理多行）。

    刻意不复用 dotenv：本探针要能在**不启动 agent** 的前提下读到它的配置，
    以便与 backend 侧对账——"两侧不一致"正是要查的东西。
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="服务间接线探针（密钥对账 + 降级可见性 + agent 探活）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（供 status.ps1 汇总）")
    ap.add_argument("--no-net", action="store_true", help="跳过网络请求（只看配置对账）")
    args = ap.parse_args()

    from app.core.config import settings
    from app.core.diagnostics import token_fingerprint

    agent_env = _read_env(AGENT_ENV)
    report: dict[str, Any] = {"checks": [], "ok": True}

    def add(name: str, ok: bool, detail: str, level: str = "fail") -> None:
        """记一条检查。`level='warn'` 表示"不通过也不翻转总判定"。"""
        report["checks"].append({"name": name, "ok": ok, "detail": detail, "level": level})
        if not ok and level == "fail":
            report["ok"] = False

    # ── 1. 两侧密钥对账（本批核心：§7.12 的"看不见的配置差异"）───────────────
    backend_token = settings.agent_service_token or ""
    agent_inbound = agent_env.get("AGENT_SERVICE_TOKEN", "")
    # 出站口径与 `yishu.security.backend_channel_token` 一致：AGENT_BACKEND_TOKEN 优先
    agent_outbound = agent_env.get("AGENT_BACKEND_TOKEN") or agent_inbound
    fp_backend = token_fingerprint(backend_token)
    fp_inbound = token_fingerprint(agent_inbound)
    fp_outbound = token_fingerprint(agent_outbound)

    both_configured = bool(backend_token) and bool(agent_inbound)
    add(
        "两侧密钥已配置",
        both_configured,
        f"backend fp={fp_backend} ｜ agent AGENT_SERVICE_TOKEN fp={fp_inbound}",
    )
    same = both_configured and fp_backend == fp_inbound == fp_outbound
    add(
        "两侧密钥同值",
        same,
        f"backend={fp_backend} ｜ agent 入站={fp_inbound} ｜ agent 出站={fp_outbound}"
        + (
            ""
            if same
            else " ⇒ 不同值：一侧 401/503、另一侧静默降级（AI 说已记住但 App 时间轴空白）"
        ),
    )

    # ── 2. agent → backend 地址口径（指错只会降级、不会报错，故必须显式核）──
    # ⚠️ 真源是 `backend/.env` 的 `API_PORT`（后端**真实监听口**，与门禁
    # `check_port_consistency` 同一口径）。**不要**用 `settings.port`——那是
    # `config.py:30` 里未被使用的默认值 8000，拿它比对会得出"agent 指错端口"的
    # **假告警**（本探针首跑即踩到；口径坑记于台账 §4.20）。
    backend_env = _read_env(BACKEND_DIR / ".env") or _read_env(BACKEND_DIR / ".env.example")
    expected_port = int(backend_env.get("API_PORT") or 8010)
    agent_base = agent_env.get("AGENT_BACKEND_BASE_URL") or "http://127.0.0.1:8010"
    add(
        "agent→backend 地址口径",
        agent_base.rstrip("/").endswith(f":{expected_port}"),
        f"agent 用 {agent_base} ｜ backend/.env API_PORT={expected_port}",
        level="warn",
    )

    # ── 3. 降级可见性 + agent 探活（需 agent 在线；不在线不算失败，存活归 status.ps1）──
    if not args.no_net:
        from app.services.external.agent import health as agent_health

        probe = agent_health()
        reachable = bool(probe.get("reachable"))
        body = probe.get("body") if reachable and isinstance(probe.get("body"), dict) else {}
        add(
            "agent 探活（agent.health）",
            reachable,
            f"reachable={reachable} status={probe.get('status')}"
            + ("" if reachable else f" ｜ {probe.get('body')}"),
            level="warn",
        )
        if reachable:
            degradation = body.get("degradation") or {}
            total = int(degradation.get("total", 0) or 0)
            add(
                "增强路径未降级",
                total == 0,
                f"degradation={json.dumps(degradation, ensure_ascii=False)}"
                + ("" if total == 0 else " ⇒ 有路径在降级：记忆镜像 / 检索合并未生效"),
            )
            # 进程**实际**读到的值 vs 配置文件写的值（防"改了 .env 但进程没重启"）
            add(
                "agent 进程读到同一密钥",
                body.get("inbound_token_fp") == fp_backend,
                f"agent 进程 inbound fp={body.get('inbound_token_fp')} ｜ backend fp={fp_backend}",
            )
            add(
                "agent 出站通道已配置",
                bool(body.get("backend_token_configured")),
                f"backend_base_url={body.get('backend_base_url')}",
                level="warn",
            )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("")
        print("服务间接线探针（台账 §7.12：静默降级显性化）")
        print(f"仓库根 = {REPO}")
        print("")
        for item in report["checks"]:
            mark = "[ OK ]" if item["ok"] else ("[WARN]" if item["level"] == "warn" else "[FAIL]")
            print(f"  {mark} {item['name']:<22} {item['detail']}")
        print("")
        if report["ok"]:
            print("  ✅ 服务间接线无矛盾")
        else:
            print("  ❌ 存在不一致（上方 [FAIL]）——处置见 docs/决策台账.md §7.12")
        print("")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
