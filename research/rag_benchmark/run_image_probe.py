"""以图搜图 P95 探针（A-4 · 2026-10-14）

## 为什么单独写一个

审计 `docs/审计_未关闭缺陷_20260923.md:68` 指出：**真实 `search_by_image` 路径全仓无评测脚本**。
已有的 `scripts/eval_image_search.py` 走的是**文本** `search()`（文字搜图），不是以图搜图；
历史上被引用的 2013ms 正是它测出来的，且审计明令「复测前勿引用任一数值」。

本探针调**真实生产路径** `app.services.rag.search_by_image`：图片 → caption（Qwen3-VL）
→ 向量 → `image_vec` 检索，端到端测延迟。

## 口径说明（别误读）

- 查询图**自带语料**：默认把 `--images` 目录下的图先灌进独立 collection，再拿**同一批图**
  当查询。好处是不依赖已丢失的 `corpus.json`，完全可复现；代价是**只证「延迟 + 链路可查」，
  不是检索质量基线**（质量需要留出标注，另立项）。
- **冷 / 热两种口径分开报**：`rag/image.py` 的 caption 缓存按图片字节 sha256 记 24h，
  重复查询会跳过 Qwen3-VL 往返 —— 只报一个数会严重误导（这正是历史 7629ms→2013ms 的来源）。
  `p95_ms_cold` = 每次查询前清空缓存（含 VL 往返）；`p95_ms_cached` = 第二次起（只有编码+检索）。

用法：
  MOCK_EXTERNAL_AI=false infisical run --env=dev --silent -- python -m research.rag_benchmark.run_image_probe --max 20
  python -m research.rag_benchmark.run_image_probe --max 20 --no-index    # 复用已灌好的库
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:  # 既支持直跑，也支持被按包导入
    from gate_io import force_utf8
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8

force_utf8()

REPO = Path(__file__).resolve().parent.parent.parent
BENCH = REPO / "research" / "rag_benchmark"
sys.path.insert(0, str(REPO / "backend"))  # 让 app.* 可导入（脚本从仓库根直跑）
OUT = BENCH / "image_query_report.json"
PROBE_COLLECTION = "yishu_benchmark_imgprobe"
GATE_P95_MS = 3000


def _use_synthetic_caption() -> None:
    """把 `dashscope.image_caption` 换成确定性桩 —— 无 key 时也能验证链路。

    ⚠️ 这样测出的延迟**不含 Qwen3-VL 网络往返**，只是「编码 + 检索」的下界；
    真实的冷口径必须配 `MOCK_EXTERNAL_AI=false` + DASHSCOPE_API_KEY 重跑。
    桩按文件名生成 caption ⇒ 同图同 caption ⇒ self_hit@1 仍可验证。
    """
    from app.services.external import dashscope as ds

    ds.image_caption = lambda path, *a, **kw: f"测试图片 {Path(path).stem}"  # type: ignore[assignment]


def _collect_images(src: Path, limit: int) -> list[Path]:
    pats = ("*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG")
    files: list[Path] = []
    for p in pats:
        files.extend(sorted(src.glob(p)))
    return files[:limit]


def _index(images: list[Path]) -> None:
    """把探针图灌进独立 collection（caption → 图片塔向量 → image_vec）。"""
    from app.services.external.dashscope import image_caption
    from app.services.image_tower import encode_image_vector
    from app.services.vector_store import get_store

    store = get_store()
    store.ensure_collection(PROBE_COLLECTION)
    for img in images:
        caption = image_caption(str(img))
        vec, space = encode_image_vector(image_path=str(img), caption=caption)
        store.upsert_image_vec(
            content_id=f"imgprobe-{img.stem}",
            vec=vec,
            payload={"content_type": "photo", "text": caption, "image_space": space,
                     "benchmark": "imgprobe"},
            collection=PROBE_COLLECTION,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default=str(REPO / ".cowork-temp" / "test_photos"),
                    help="查询图目录（默认 .cowork-temp/test_photos）")
    ap.add_argument("--max", type=int, default=20, help="最多取多少张")
    ap.add_argument("--no-index", action="store_true", help="跳过灌库，复用已有 collection")
    ap.add_argument("--no-vl", action="store_true",
                    help="用确定性桩替代 Qwen3-VL caption（无 key 时验证链路；测出的延迟不含 VL 往返）")
    args = ap.parse_args()

    if args.no_vl:
        _use_synthetic_caption()
        print("⚠️ --no-vl：跳过 Qwen3-VL，测出的延迟**不含 VL 网络往返**，只是编码+检索的下界")

    src = Path(args.images)
    if not src.is_dir():
        print(f"❌ 图片目录不存在：{src}")
        return 2
    images = _collect_images(src, args.max)
    if not images:
        print(f"❌ {src} 下没有图片")
        return 2

    from app.schemas.search import SearchQuery
    from app.services import rag
    from app.services.rag import search_by_image

    if not args.no_index:
        print(f"[probe] 灌库 {len(images)} 张 → {PROBE_COLLECTION} ...", flush=True)
        _index(images)

    def _query(img: Path) -> tuple[float, int]:
        t0 = time.perf_counter()
        res = search_by_image(str(img), SearchQuery(q="[image]", limit=5),
                              collection=PROBE_COLLECTION)
        ms = (time.perf_counter() - t0) * 1000
        hits = getattr(res, "hits", []) or []
        self_hit = 1 if hits and f"imgprobe-{img.stem}" == str(getattr(hits[0], "content_id", "")) else 0
        return ms, self_hit

    # ---- 冷口径：每次查询前清空 caption 缓存（含 Qwen3-VL 往返）----
    cold: list[float] = []
    self_hits = 0
    for img in images:
        rag.image._caption_cache.clear()
        ms, hit = _query(img)
        cold.append(ms)
        self_hits += hit
        print(f"[cold] {img.name:<24} {ms:8.1f}ms  self_hit@1={hit}", flush=True)

    # ---- 热口径：缓存已在（只剩编码 + 检索）----
    warm: list[float] = [_query(img)[0] for img in images]

    def _p95(xs: list[float]) -> float:
        return sorted(xs)[max(0, int(len(xs) * 0.95) - 1)]

    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    rep = {
        "_meta": {
            "note": "以图搜图真实路径（search_by_image）P95 探针 —— 取代历史 2013ms 旧值"
                    "（那是【文字搜图】的值，且审计要求复测前勿引用任一旧值）",
            "collection": PROBE_COLLECTION,
            "n_images": len(images),
            "images_dir": str(src),
            "gate_p95_ms": GATE_P95_MS,
            "nl_vl_skipped": bool(args.no_vl),
            "caveat": "self-query 只证「延迟 + 链路可查」，**不是检索质量基线**（质量需留出标注）"
                      + ("；本次 --no-vl，冷口径不含 Qwen3-VL 往返，为下界" if args.no_vl else ""),
        },
        "cold": {"p95_ms": round(_p95(cold), 1), "avg_ms": round(_avg(cold), 1),
                 "gate_p95": _p95(cold) < GATE_P95_MS},
        "cached": {"p95_ms": round(_p95(warm), 1), "avg_ms": round(_avg(warm), 1),
                   "gate_p95": _p95(warm) < GATE_P95_MS},
        "self_hit@1": round(self_hits / len(images), 3),
    }
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"[probe] n={len(images)}  冷 P95 {rep['cold']['p95_ms']}ms "
          f"({'✅' if rep['cold']['gate_p95'] else '❌'} <{GATE_P95_MS}ms)"
          f" | 热 P95 {rep['cached']['p95_ms']}ms"
          f" | self_hit@1 {rep['self_hit@1']}")
    print(f"[probe] 报告: {OUT}")
    return 0 if rep["cached"]["gate_p95"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
