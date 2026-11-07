"""百炼（DashScope）真实链路验证矩阵（2026-08-29 拿 key 后补充开发 · WP-B 扩展版）

check_dashscope.py 只做单次 ping；本脚本把全部 key 消耗链路逐条真实过一遍，
输出 JSON（不含任何密钥材料），作为「真实 AI 档」证据留档。

覆盖链路（--only 可选子集）：
  rewrite       查询改写（qwen-flash，真实链路 vs 规则兜底判定）
  route         查询路由（qwen-flash，image/text/mixed 合法性）
  rerank        LLM 精排真实判定（解析成功率 + 换序生效 + 时延；08-28 实测解析
                失败回退原序的回归监控点——解析失败时原始输出前 200 字入证据）
  guard_chat    护栏 chat 链路（dashscope.moderate：正常放行 + 规则/LLM 拦截）
  guard_managed 托管护栏（qwen_response_check，X-DashScope-DataInspection 直发）
  fail_closed   fail-safe：伪造错 key → moderate 必须拒发（SAF-005，不发真实请求）
  vl_caption    Qwen3-VL 图片塔（需 --image 给真实图片路径；顺带时延记录）
  caption_cache 以图搜图 caption 缓存 + VL 失败过期兜底（注入模拟，零成本）
  event_merge   L2 主题事件 LLM 归并裁决（qwen-flash，真实/降级判定）

用法（密钥只经注入，不落盘不回显）：
  infisical run --env=dev -- python scripts/check_dashscope_matrix.py
  # 或本地调试（用 backend/.env 的 key，须 MOCK_EXTERNAL_AI=false）：
  python scripts/check_dashscope_matrix.py --image D:\\path\\to\\photo.jpg
  # 子集 + 证据落盘：
  python scripts/check_dashscope_matrix.py --only rerank,guard_chat --json .cowork-temp\\dashscope_matrix.json

退出码：0=全部 pass（skip 不算），1=有 fail，2=未配置 key/mock（应经 infisical run 注入）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

ALL_CASES = [
    "rewrite",
    "route",
    "rerank",
    "guard_chat",
    "guard_managed",
    "fail_closed",
    "vl_caption",
    "caption_cache",
    "event_merge",
]


def _now() -> float:
    return time.perf_counter()


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# ---------------- 各链路探针（全部只读，输出不带密钥） ----------------


def _probe_rewrite() -> dict:
    from app.core.config import settings
    from app.services.external.dashscope import rewrite_query

    if settings.mock_external_ai or not settings.dashscope_api_key:
        return {"status": "skip", "detail": "mock 档/未配 key"}
    t0 = _now()
    try:
        out = rewrite_query("上次说的那个驼奶的功效来着，帮我找找")
        return {"status": "pass" if out.strip() else "fail", "out": out[:120], "latency_ms": _ms(t0)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}


def _probe_route() -> dict:
    from app.core.config import settings
    from app.services.external.dashscope import route_query

    if settings.mock_external_ai or not settings.dashscope_api_key:
        return {"status": "skip", "detail": "mock 档/未配 key"}
    t0 = _now()
    try:
        r = route_query("去年在西湖拍的照片")
        ok = r in ("image", "text", "mixed")
        return {"status": "pass" if ok else "fail", "route": r, "latency_ms": _ms(t0)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}


def _probe_rerank() -> dict:
    """LLM 精排真实链路：解析成功率（硬判定）+ 换序生效（软复核）。

    08-28 评测实测「输出解析失败回退原序」——解析失败判定 = 全候选无
    rerank_reason；失败时捕获 LLM 原始输出前 200 字入证据（语义误排只警示
    不判 fail，避免探针因模型抖动假阳性）。
    """
    from app.core.config import settings
    from app.services.llm_ops import rerank as rerank_mod

    if settings.mock_external_ai or not settings.dashscope_api_key or not settings.rerank_llm_enabled:
        return {"status": "skip", "detail": "mock 档/未配 key/开关关"}
    hits = [
        {"id": "a", "text": "周五晚上十一点还在改 PPT，咖啡第三杯", "score": 0.91},
        {"id": "b", "text": "2025 年 3 月，在杭州西湖边喝下午茶，吃了桂花糕", "score": 0.86},
        {"id": "c", "text": "周一部门例会纪要：推进检索链路联调", "score": 0.83},
        {"id": "d", "text": "考研倒计时 100 天，图书馆占座", "score": 0.81},
    ]
    raw_box: dict = {}
    orig_chat = rerank_mod.chat_text

    def cap_chat(system: str, user: str, *a, **kw) -> str:
        out = orig_chat(system, user, *a, **kw)
        raw_box["raw"] = out
        return out

    rerank_mod.chat_text = cap_chat
    t0 = _now()
    try:
        out = rerank_mod.llm_rerank("去年三月在杭州西湖吃了什么", hits, candidates=4, top_k=2)
        latency = _ms(t0)
        judged = [h for h in out if h.get("rerank_reason")]
        if not judged:
            return {
                "status": "fail",
                "latency_ms": latency,
                "detail": "解析失败回退原序（无 rerank_reason）",
                "raw_head": str(raw_box.get("raw", ""))[:200],
            }
        top = out[0]["id"]
        return {
            "status": "pass",
            "order": [h["id"] for h in out],
            "top": top,
            "judged_n": len(judged),
            "latency_ms": latency,
            "note": "" if top == "b" else "首位非预期候选（语义误排，人工复核）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}
    finally:
        rerank_mod.chat_text = orig_chat


def _probe_guard_chat() -> dict:
    from app.core.config import settings
    from app.services.external.dashscope import moderate

    if settings.mock_external_ai or not settings.dashscope_api_key:
        return {"status": "skip", "detail": "mock 档/未配 key"}
    t0 = _now()
    try:
        ok_pass = moderate("今天去公园散步，拍了些樱花照片")
        ok_block = moderate("赌博网站推广，加我微信带你日入过万")
        passed = ok_pass.get("pass") is True and ok_block.get("pass") is False
        return {
            "status": "pass" if passed else "fail",
            "benign": ok_pass.get("reason") or "pass",
            "violation": ok_block.get("reason"),
            "latency_ms": _ms(t0),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}


def _probe_guard_managed() -> dict:
    from app.core.config import settings
    from app.services.llm_ops.guard_managed import qwen_response_check

    if settings.mock_external_ai or not settings.dashscope_api_key:
        return {"status": "skip", "detail": "mock 档/未配 key"}
    t0 = _now()
    try:
        r = qwen_response_check("今天去公园散步，拍了些樱花照片")
        return {
            "status": "pass" if r.get("detector") == "managed" else "fail",
            "pass": r.get("pass"),
            "reason": r.get("reason"),
            "detector": r.get("detector"),
            "latency_ms": _ms(t0),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}


def _probe_fail_closed() -> dict:
    """fail-safe（SAF-005）：非 mock + key 置空 + 生产档 → moderate 必须拒发。

    纯门控验证：dashscope.moderate 在 key 空时直接走 fail-closed 分支，
    不发任何真实请求（零成本）。
    """
    from app.services.external import dashscope as ds_mod

    keep = (ds_mod.settings.mock_external_ai, ds_mod.settings.dashscope_api_key, ds_mod.settings.app_env)
    try:
        ds_mod.settings.mock_external_ai = False
        ds_mod.settings.dashscope_api_key = ""
        ds_mod.settings.app_env = "production"
        r = ds_mod.moderate("一段普通文本内容")
        ok = r.get("pass") is False and r.get("reason") == "guard-unavailable"
        return {"status": "pass" if ok else "fail", "reason": r.get("reason")}
    finally:
        ds_mod.settings.mock_external_ai, ds_mod.settings.dashscope_api_key, ds_mod.settings.app_env = keep


def _probe_vl_caption(image: str) -> dict:
    from app.core.config import settings
    from app.services.external.dashscope import image_caption

    if settings.mock_external_ai or not settings.dashscope_api_key:
        return {"status": "skip", "detail": "mock 档/未配 key"}
    if not image:
        return {"status": "skip", "detail": "未给 --image"}
    if not Path(image).is_file():
        return {"status": "fail", "detail": f"图片不存在: {image}"}
    t0 = _now()
    try:
        cap = image_caption(image).strip()
        return {"status": "pass" if cap else "fail", "caption": cap[:120], "latency_ms": _ms(t0)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}


def _probe_caption_cache() -> dict:
    """caption 缓存 + VL 失败过期兜底（08-29 加固）：注入模拟，零成本零密钥。"""
    import hashlib
    import tempfile

    from app.services.external import dashscope as ds_mod
    from app.services.rag import image as img_mod

    calls = {"n": 0}

    def fake_ok(path: str) -> str:
        calls["n"] += 1
        return "缓存探针描述：西湖游船"

    def fake_boom(path: str) -> str:
        calls["n"] += 1
        raise RuntimeError("connection reset (模拟)")

    keep = ds_mod.image_caption
    img_mod._caption_cache.clear()
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"matrix-caption-probe-20260829")
            p = f.name
        # 1) 缓存命中：两次调用只打一次 VL
        ds_mod.image_caption = fake_ok
        img_mod._cached_image_caption(p)
        img_mod._cached_image_caption(p)
        cache_hit = calls["n"] == 1
        # 2) 过期条目 + VL 失败 → 返回旧 caption（08-29 加固点）
        digest = hashlib.sha256(b"matrix-caption-probe-20260829").hexdigest()
        ts, cap = img_mod._caption_cache[digest]
        img_mod._caption_cache[digest] = (ts - 25 * 3600, cap)
        ds_mod.image_caption = fake_boom
        stale_out = img_mod._cached_image_caption(p)
        stale_ok = stale_out == "缓存探针描述：西湖游船"
        ok = cache_hit and stale_ok
        return {
            "status": "pass" if ok else "fail",
            "vl_calls": calls["n"],
            "cache_hit_once": cache_hit,
            "stale_fallback": stale_ok,
        }
    finally:
        ds_mod.image_caption = keep
        img_mod._caption_cache.clear()


def _probe_event_merge() -> dict:
    from app.core.config import settings
    from app.services.llm_ops.event_merge import merge_verdict

    if settings.mock_external_ai or not settings.dashscope_api_key:
        return {"status": "skip", "detail": "mock 档/未配 key"}
    cand = {
        "cluster": ["cnt-1", "cnt-2", "cnt-3", "cnt-4", "cnt-5"],
        "time_range": ["2025-03-14T09:00:00", "2025-03-16T18:00:00"],
        "place_hint": "杭州市西湖区",
        "tag_hint": ["旅行", "拍照"],
        "ocr_summary": "",
        "cover_content_id": "",
    }
    t0 = _now()
    try:
        r = merge_verdict(cand)
        ok = r.get("llm") == "real" and r.get("confidence", 0) > 0
        return {
            "status": "pass" if ok else "fail",
            "llm": r.get("llm"),
            "verdict": r.get("verdict"),
            "confidence": r.get("confidence"),
            "title": r.get("title"),
            "latency_ms": _ms(t0),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}


# ---------------- 主入口 ----------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help=f"逗号子集，默认全部：{','.join(ALL_CASES)}")
    ap.add_argument("--image", default="", help="Qwen3-VL 探针用的本地图片路径")
    ap.add_argument("--json", default="", help="证据 JSON 落盘路径（不含密钥）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)

    from app.core.config import settings

    if settings.mock_external_ai or not settings.dashscope_api_key:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "detail": "未配置 DASHSCOPE key 或 MOCK_EXTERNAL_AI=true；请用 infisical run --env=dev 注入",
                },
                ensure_ascii=False,
            )
        )
        return 2

    want = {c.strip() for c in args.only.split(",") if c.strip()} or set(ALL_CASES)
    report: dict = {
        "_meta": {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "workspace_configured": bool(settings.dashscope_workspace_id),
            "note": "百炼真实链路验证矩阵；仅报密钥名/状态，无密钥值",
        }
    }

    probes = {
        "rewrite": _probe_rewrite,
        "route": _probe_route,
        "rerank": _probe_rerank,
        "guard_chat": _probe_guard_chat,
        "guard_managed": _probe_guard_managed,
        "fail_closed": _probe_fail_closed,
        "vl_caption": lambda: _probe_vl_caption(args.image),
        "caption_cache": _probe_caption_cache,
        "event_merge": _probe_event_merge,
    }
    for name in ALL_CASES:
        if name not in want:
            continue
        t0 = _now()
        try:
            report[name] = probes[name]()
        except Exception as exc:  # noqa: BLE001 —— 探针必须捕获一切
            report[name] = {"status": "fail", "detail": f"{type(exc).__name__}: {str(exc)[:150]}"}
        report[name]["probe_ms"] = _ms(t0)

    failed = [k for k, v in report.items() if k != "_meta" and v.get("status") == "fail"]
    passed = [k for k, v in report.items() if k != "_meta" and v.get("status") == "pass"]
    skipped = [k for k, v in report.items() if k != "_meta" and v.get("status") == "skip"]
    report["_meta"].update({"pass": passed, "fail": failed, "skip": skipped})

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
