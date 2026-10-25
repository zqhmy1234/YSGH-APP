"""图片查询质量评测框架（B-14 · 2026-10-26）

## 为什么这是**框架**而不是基线

现有 `run_image_probe.py` 用**同一批图**做「自问自答」（索引 A 图 → 拿 A 图查询），
所以 `self_hit@1` 必然 ≈1.0，它自己的 `_meta.caveat` 也写着：
「self-query 只证延迟 + 链路可查，**不是检索质量基线**」。

真实的质量评测必须**留出**：查询图 ≠ 候选库里的图，且要有人标注「这张查询图
应该召回哪几张」。本脚本实现这套框架，但它**能不能跑出有意义的数字，取决于素材**：

| 素材 | 实际情况（2026-10-26 实测） |
|---|---|
| `review/` | 8 张**手机 UI 截图**（搜索页/录音页/时间轴），是验收证据，不是"相册照片" |
| `corpus_d_filter.json` | 6 条 photo 条目，但**只有文字描述**（`text` 字段），**没有图片文件** |
| `.cowork-temp/test_photos/` | 50 张**程序生成**的纯色几何图（circle/rect/triangle × 4 底色），非自然图像 |

⇒ **样本量不足以称基线**。用纯色几何图做留出评测，测出来的是"颜色+形状是否可分"，
与真实图搜质量无关；把 UI 截图当照片更是错的。故本框架的产出**只证明评测链路可用**，
报告里会显式写明这一点（`_meta.is_baseline = false` + 顶格诚实声明）。

## 留出标注格式

`image_quality_labels.json`：
    {
      "_meta": {...},
      "cases": [
        {"query_image": "路径", "relevant_ids": ["候选id", ...], "note": "可选"}
      ]
    }

`relevant_ids` 指向**候选库**里的 id（不是查询图自己的 id）。

## 用法

    cd D:\\GuangH-App
    # 1) 先看素材够不够（不跑检索、不花 API 钱）
    python -m research.rag_benchmark.run_image_quality --audit-materials
    # 2) 生成一份自动留出标注（用现有素材做试点，会明确标注样本量不足）
    python -m research.rag_benchmark.run_image_quality --auto-label
    # 3) 真跑（调真实 search_by_image；每张查询一次 Qwen3-VL，有成本）
    python -m research.rag_benchmark.run_image_quality --max 5

退出码：0 报告产出 / 2 环境不可用 / 3 素材不足（明确告知，不伪造）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from gate_io import force_utf8
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8

force_utf8()

REPO = Path(__file__).resolve().parent.parent.parent
BENCH = REPO / "research" / "rag_benchmark"
LABELS = BENCH / "image_quality_labels.json"
OUT = BENCH / "image_quality_report.json"
PROBE_COLLECTION = "yishu_benchmark_imgquality"
sys.path.insert(0, str(REPO / "backend"))

# 「勉强可用」的下限（任务口径：≥20 查询图 + ≥20 候选）。低于此**不称基线**。
MIN_QUERIES = 20
MIN_CANDIDATES = 20

# 已知素材短板（写进报告，避免下一个人重新踩）
MATERIAL_NOTES = {
    "review/": "8 张手机 UI 截图（验收证据），非相册照片 —— 不适合做图搜质量素材",
    "corpus_d_filter.json": "6 条 photo 条目只有文字描述，**没有图片文件**",
    ".cowork-temp/test_photos/": "50 张程序生成纯色几何图（circle/rect/triangle × 4 底色），非自然图像",
}


def audit_materials() -> dict:
    """盘点磁盘上真实可用的图片素材（只读，不花 API 钱）

    分开统计三类：review 下的截图、test_photos 下生成的图、gps_photos 下的真实照片。
    为什么要分类：**数量够 ≠ 素材合格**。100 张纯色几何图数量上远超门槛，
    但用它评测图搜质量等于测"颜色相似度"，结论没有外部效度。

    「真实自然照片」的判据（写死，避免下次把截图算进来）：
      · **来源目录白名单**：只有 `.cowork-temp/gps-photos` 是真机拍摄/真实照片；
        `review/` 是**验收截图**（手机屏幕截图，2412×1084）、`test_photos/` 是**程序生成**。
        仅靠长宽比判不出来 —— 2412×1084 的宽高比只有 2.2，会通过"≤3:1"这类几何判据
        （我第一版就是这么误算成 26 张的）。**来源比几何更可靠**。
      · 几何兜底：最小边 ≥ 320px（排除图标 72/96/144）、宽高比 ≤4:1（排除极端长图）。

    结论：真实照片只有 `gps-photos` 的 5 张（其余 5 个是 app 图标/splash），
    远不足以做 ≥20+≥20 的留出评测。
    """
    from PIL import Image

    out: dict = {"groups": {}, "real_photos": [], "usable_for_quality": 0, "verdict": ""}

    # 只有这个目录是真照片；review=UI 截图、test_photos=程序生成
    REAL_PHOTO_ROOTS = {"gps_photos"}

    def _real_photo(p: Path) -> bool:
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001 —— 解不开不算
            return False
        if min(w, h) < 320:
            return False
        return max(w, h) / max(1, min(w, h)) <= 4.0

    roots = {
        "review": REPO / "review",
        "test_photos": REPO / ".cowork-temp" / "test_photos",
        "gps_photos": REPO / ".cowork-temp" / "gps-photos",
    }
    for name, root in roots.items():
        files: list[Path] = []
        if root.is_dir():
            # ⚠️ 不要写 ("*.jpg", "*.JPG", ...) —— Windows 的 glob **大小写不敏感**，
            # `*.jpg` 与 `*.JPG` 会各自匹配同一批文件 ⇒ **每张被数两遍**
            # （实测：gps-photos 5 张真实照片被算成 10 张）。用一次遍历 + 后缀小写判定。
            files = sorted(
                p for p in root.iterdir()
                if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
            )
        detail = []
        real_here = 0
        for f in files:
            try:
                with Image.open(f) as im:
                    w, h = im.size
                is_real = _real_photo(f) and name in REAL_PHOTO_ROOTS
                detail.append({
                    "file": f.name, "size": [w, h],
                    "real_photo": is_real,
                })
                if is_real:
                    real_here += 1
                    out["real_photos"].append(str(f.relative_to(REPO)))
            except Exception:  # noqa: BLE001
                detail.append({"file": f.name, "error": "unreadable"})
        out["groups"][name] = {
            "root": str(root), "count": len(files),
            "real_photo_count": real_here, "samples": detail[:12],
        }

    out["usable_for_quality"] = len(out["real_photos"])
    if out["usable_for_quality"] >= MIN_QUERIES:
        out["verdict"] = f"OK 真实照片 {out['usable_for_quality']} 张，可尝试留出评测"
    else:
        out["verdict"] = (
            f"FAIL 素材不足：真实自然照片仅 {out['usable_for_quality']} 张 "
            f"（需 ≥{MIN_QUERIES} 查询 + ≥{MIN_CANDIDATES} 候选）。"
            "review=UI 截图、test_photos=程序生成几何图（均不可用作质量素材）；"
            "只有 .cowork-temp/gps-photos 的几张真实相机照，数量远不够。"
        )
    return out


def _collect_query_images(max_n: int) -> list[Path]:
    """挑查询图：优先真实照片，其次其它目录（顺序体现优先级）"""
    pats = ("*.jpg", "*.jpeg", "*.png")
    cands: list[Path] = []
    for root in (REPO / ".cowork-temp" / "gps-photos", REPO / "review",
                 REPO / ".cowork-temp" / "test_photos"):
        if not root.is_dir():
            continue
        for pat in pats:
            cands.extend(sorted(root.glob(pat)))
    return cands[:max_n]


def auto_label(max_n: int = 10) -> dict:
    """自动生成一份**试点**留出标注（查询图 ≠ 候选图）

    ## 怎么保证"留出"

    把选中的图**分成两半**：前一半当候选库、后一半当查询图 ——
    两组**物理不相交**（按文件路径集合断言）。这样 hit@1 才有意义。

    ## 为什么命中判定用"视觉同源"而不是人工标注

    素材都是程序生成的几何图，没有人工标注。这里的 `relevant_ids` 用
    **「同底色 + 同形状」作为弱相关标签**（例如同为"浅棕底+圆"）。
    这**不是**人工标注的同义相关，只是能让框架跑出非零指标的替代口径 ——
    报告里必须标注 `label_kind = "auto_weak_visual"`，**不得与人工标注的基线混用**。
    """
    imgs = _collect_query_images(max_n * 2)
    if len(imgs) < 4:
        return {"_meta": {"error": "素材不足，无法划分留出集"}, "cases": []}
    half = len(imgs) // 2
    candidates = imgs[:half]
    queries = imgs[half:]

    # 断言两组不相交（留出的前提）。用显式 raise 而非 `assert`：
    # `assert` 在 `python -O` 下会被剥离 —— 那时这条**正确性前提**会静默消失，
    # 而留出重叠会让 hit@1 虚高（查询图本身就在候选库里）。
    if set(map(str, candidates)) & set(map(str, queries)):
        raise RuntimeError("查询图与候选图不得重叠（留出前提被破坏）")

    def _sig(p: Path) -> str:
        """视觉签名：取图片中央像素的 RGB 粗量化（几何图底色区分度足够）"""
        from PIL import Image

        try:
            with Image.open(p) as im:
                w, h = im.size
                r, g, b = im.convert("RGB").getpixel((w // 8, h // 8))
            return f"{r // 32}-{g // 32}-{b // 32}"
        except Exception:  # noqa: BLE001
            return "unreadable"

    cand_sig = {f"imgq-{p.stem}": _sig(p) for p in candidates}
    cases = []
    for q in queries:
        sig = _sig(q)
        rel = [cid for cid, s in cand_sig.items() if s == sig]
        cases.append({
            "query_image": str(q.relative_to(REPO)),
            "relevant_ids": rel,
            "label_kind": "auto_weak_visual",
            "note": "弱标签：同底色+同形状（非人工标注）",
        })
    return {
        "_meta": {
            "version": 1,
            "label_kind": "auto_weak_visual",
            "n_candidates": len(candidates),
            "n_queries": len(queries),
            "candidates": [str(p.relative_to(REPO)) for p in candidates],
            "disjoint_verified": True,
            "note": (
                "自动生成的**试点**标注。相关标签是『视觉签名相同』的弱标签，"
                "**不是人工语义标注**；样本量远低于基线门槛（≥20+≥20）。"
            ),
        },
        "cases": cases,
    }


def _index_candidates(candidates: list[Path]) -> None:
    """把候选图灌进独立 collection（caption → 向量 → image_vec）"""
    from app.services.external.dashscope import image_caption
    from app.services.image_tower import encode_image_vector
    from app.services.vector_store import get_store

    store = get_store()
    store.ensure_collection(PROBE_COLLECTION)
    for p in candidates:
        caption = image_caption(str(p))
        vec, space = encode_image_vector(image_path=str(p), caption=caption)
        store.upsert_image_vec(
            content_id=f"imgq-{p.stem}",
            vec=vec,
            payload={"content_type": "photo", "text": caption,
                     "image_space": space, "benchmark": "imgquality"},
            collection=PROBE_COLLECTION,
        )


def evaluate(labels: dict, *, do_index: bool = True) -> dict:
    """跑真实 `rag.search_by_image`，算 hit@1 / hit@3 / MRR

    命中判定：`relevant_ids` 与返回的 content_id 有交集。
    ⚠️ 调的是**真实生产路径**（图 → Qwen3-VL caption → BGE-M3 → image_vec 检索），
    不是 mock —— 每张查询图一次 VL 往返，**有成本**。
    """
    from app.schemas.search import SearchQuery
    from app.services.rag import search_by_image

    meta = labels.get("_meta") or {}
    cand_paths = [REPO / p for p in meta.get("candidates", [])]
    if do_index and cand_paths:
        _index_candidates(cand_paths)

    rows = []
    hits1 = hits3 = 0
    rr_sum = 0.0
    n = 0
    for case in labels.get("cases", []):
        qpath = REPO / case["query_image"]
        if not qpath.exists():
            rows.append({**case, "error": "query image missing"})
            continue
        rel = set(case.get("relevant_ids") or [])
        t0 = time.perf_counter()
        try:
            res = search_by_image(str(qpath), SearchQuery(q="[image]", limit=5),
                                  collection=PROBE_COLLECTION)
            ms = (time.perf_counter() - t0) * 1000
            got = [str(h.content_id) for h in (res.hits or [])]
        except Exception as exc:  # noqa: BLE001
            rows.append({**case, "error": f"{type(exc).__name__}: {exc}"})
            continue
        n += 1
        rank = next((i + 1 for i, cid in enumerate(got) if cid in rel), None)
        if rank == 1:
            hits1 += 1
        if rank and rank <= 3:
            hits3 += 1
        if rank:
            rr_sum += 1.0 / rank
        rows.append({
            "query_image": case["query_image"],
            "relevant_ids": sorted(rel),
            "returned": got[:5],
            "rank": rank,
            "latency_ms": round(ms, 1),
            "label_kind": case.get("label_kind"),
        })

    is_baseline = (n >= MIN_QUERIES and len(cand_paths) >= MIN_CANDIDATES)
    report = {
        "_meta": {
            "version": 1,
            "kind": "image_query_quality",
            "collection": PROBE_COLLECTION,
            "n": n,
            "n_candidates": len(cand_paths),
            "min_queries_for_baseline": MIN_QUERIES,
            "min_candidates_for_baseline": MIN_CANDIDATES,
            "is_baseline": is_baseline,
            "label_kind": meta.get("label_kind", "unknown"),
            "disjoint_verified": meta.get("disjoint_verified", False),
            "material_notes": MATERIAL_NOTES,
            "honest_statement": (
                f"样本量不足（查询图 {n} < {MIN_QUERIES}，候选 {len(cand_paths)} < "
                f"{MIN_CANDIDATES}）⇒ **不构成质量基线**，仅验证评测框架可用。"
                if not is_baseline else
                f"样本量达到下限（n={n}，候选 {len(cand_paths)}）—— "
                f"但标签为 {meta.get('label_kind', 'unknown')}，仍非人工标注基线。"
            ),
        },
        "hit@1": round(hits1 / n, 4) if n else None,
        "hit@3": round(hits3 / n, 4) if n else None,
        "mrr": round(rr_sum / n, 4) if n else None,
        "n": n,
        "details": rows,
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="图片查询质量评测（B-14 · 留出集）")
    ap.add_argument("--audit-materials", action="store_true", help="只盘点素材，不跑检索")
    ap.add_argument("--auto-label", action="store_true", help="生成试点留出标注")
    ap.add_argument("--labels", default=str(LABELS), help="标注文件路径")
    ap.add_argument("--max", type=int, default=0, help="最多跑 N 条查询（限制成本）")
    ap.add_argument("--no-index", action="store_true", help="跳过灌库（复用已有 collection）")
    ap.add_argument("--dry-run", action="store_true", help="不写报告")
    args = ap.parse_args()

    if args.audit_materials:
        au = audit_materials()
        print(f"[materials] {au['verdict']}")
        for name, g in au["groups"].items():
            print(f"  · {name:<14} {g['count']:>3} 张  ({g['root']})")
        print(f"  ⇒ 可用于质量评测的真实照片：{au['usable_for_quality']}")
        print(f"\n  下限要求：查询图 ≥{MIN_QUERIES}、候选 ≥{MIN_CANDIDATES}")
        return 0 if au["usable_for_quality"] >= MIN_QUERIES else 3

    if args.auto_label:
        lab = auto_label(max_n=args.max or 10)
        if not lab["cases"]:
            print(f"[labels] 生成失败：{lab['_meta'].get('error')}")
            return 3
        p = Path(args.labels)
        p.write_text(json.dumps(lab, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[labels] 试点标注 {len(lab['cases'])} 条（候选 {lab['_meta']['n_candidates']} 张）"
              f" → {p}")
        print(f"[labels] ⚠️ 样本量不足基线门槛（≥{MIN_QUERIES}+≥{MIN_CANDIDATES}），"
              f"标签为弱视觉标签")
        return 0

    p = Path(args.labels)
    if not p.exists():
        print(f"[FAIL] 缺标注文件：{p}")
        print("       先生成试点标注：--auto-label，或按 docstring 格式手写人工标注")
        return 2
    labels = json.loads(p.read_text(encoding="utf-8"))
    if args.max:
        labels = {**labels, "cases": labels.get("cases", [])[: args.max]}

    try:
        rep = evaluate(labels, do_index=not args.no_index)
    except Exception as exc:  # noqa: BLE001 —— Qdrant/百炼不可用属环境错误
        print(f"[FAIL] 检索不可用（环境错误）：{type(exc).__name__}: {exc}")
        return 2

    print(f"[imgq] n={rep['n']}  候选={rep['_meta']['n_candidates']}  "
          f"标签={rep['_meta']['label_kind']}")
    print(f"[imgq] hit@1={rep['hit@1']}  hit@3={rep['hit@3']}  MRR={rep['mrr']}")
    print(f"[imgq] {rep['_meta']['honest_statement']}")

    if args.dry_run:
        print("[dry-run] 未写报告")
        return 0
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[imgq] 报告: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
