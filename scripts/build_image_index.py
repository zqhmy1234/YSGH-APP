"""corpus-A 图片塔索引（B2-4 文字搜图 · 2026-08-19）

流程：corpus.json（500 张截图）→ Qwen3-VL caption（缓存断点续跑 + 单张重试）→ BGE-M3 编码
→ upsert yishu_benchmark（content_type=photo, text=caption）→ 生成 corpus-A 查询。

FIX-1（2026-08-26）：索引 content_type 由 "image" 改为规范值 "photo"（与生产
pipeline payload 一致）；检索过滤端双向兼容遗留 "image" 点。

费用：qwen3-vl-plus ≈0.003 元/张 × 500 ≈ 1.5 元（用户已授权推进）。
用法：
  MOCK_EXTERNAL_AI=false infisical run --env=dev --silent -- python scripts/build_image_index.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

BENCH = Path(__file__).resolve().parent.parent / "research" / "rag_benchmark"
CORPUS = BENCH / "corpus.json"
CACHE = Path(__file__).resolve().parent.parent / ".cowork-temp" / "image_captions.json"
QUERIES_OUT = BENCH / "queries" / "queries_image.json"

CAPTION_PROMPT = "用一句话中文描述这张图片的内容和场景，适合作为记忆检索的线索（20-60字）。"


def _load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def main() -> int:
    from app.services.external.dashscope import image_caption

    items = json.loads(CORPUS.read_text(encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("items", items.get("corpus", []))
    print(f"corpus 共 {len(items)} 张", flush=True)

    cache = _load_cache()
    todo = [it for it in items if it["id"] not in cache]
    print(f"待 caption: {len(todo)}（缓存已有 {len(cache)}）", flush=True)

    fails = 0
    for i, it in enumerate(todo, 1):
        # audit #14：单张网络失败重试（3 次，间隔 5s；仍失败跳过，断点续跑）
        cap = None
        last_err = None
        for attempt in range(3):
            try:
                cap = image_caption(it["path"], prompt=CAPTION_PROMPT)
                break
            except Exception as exc:  # noqa: BLE001 —— 单张失败重试
                last_err = exc
                if attempt < 2:
                    print(f"  [RETRY {attempt + 1}] {it['id']}: {type(exc).__name__} {str(exc)[:60]}", flush=True)
                    time.sleep(5)
        if cap:
            cache[it["id"]] = {"caption": cap, "path": it["path"], "taken_at": it.get("taken_at")}
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {it['id']}: {cap[:40]}", flush=True)
        else:
            fails += 1
            print(f"  [FAIL] {it['id']}: {type(last_err).__name__} {str(last_err)[:80]}", flush=True)
        if i % 50 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"caption 完成: {len(cache)}/{len(items)}（失败 {fails}，重跑自动跳过已缓存）", flush=True)

    # 索引 yishu_benchmark（content_type=photo）：text_vec + text_sparse + image_vec
    from app.services.embedding import encode_dense, encode_sparse
    from app.services.vector_store import get_store

    store = get_store()
    store.ensure_collection("yishu_benchmark")
    indexed = []
    for it in items:
        if it["id"] not in cache:
            continue
        caption = cache[it["id"]]["caption"]
        dense = encode_dense([caption])[0]
        sparse = encode_sparse([caption])[0]
        payload = {
            "content_type": "photo",  # FIX-1：规范值（原 "image"，检索端兼容旧点）
            "label": "screenshot",
            "benchmark": "rag-distribution",
            "text": caption,
        }
        store.upsert_content(
            content_id=it["id"],
            text=caption,
            dense=dense,
            sparse=sparse,
            payload=payload,
            collection="yishu_benchmark",
        )
        # B2-4 以图搜图：image_vec = BGE-M3(caption)（caption 向量化方案）
        store.upsert_image_vec(
            content_id=it["id"],
            vec=dense,
            payload=payload,
            collection="yishu_benchmark",
        )
        indexed.append(it["id"])
    print(f"已索引 {len(indexed)} 张图片 caption+image_vec 到 yishu_benchmark", flush=True)

    # 生成 corpus-A 查询（词法关键词 + caption 开头短语）
    queries = []
    import re

    for it in items[:]:
        if it["id"] not in cache:
            continue
        cap = cache[it["id"]]["caption"]
        words = re.findall(r"[\u4e00-\u9fff]{2,6}", cap)
        if not words:
            continue
        # keyword 查询：取最高频有意义的词（去停用词）
        stop = {
            "一张", "这张", "图片", "展示", "呈现", "画面", "内容",
            "可以看到", "这是", "一个", "以及", "其中", "主要",
            "该图", "该图展示", "图中", "截图", "屏幕", "标题", "页面",
            "界面", "设计", "制作", "介绍", "描述", "展示的", "呈现了",
        }
        cand = [w for w in words if w not in stop][:6]
        if len(queries) < 10 and cand:
            queries.append({
                "query": cand[0],
                "expected": [it["id"]],
                "layer": "keyword",
                "expected_label": "screenshot",
            })
    # descriptive 查询：caption 核心短语（去模板开头词，取有信息量的片段；
    # 修复 2026-08-20：原 cap[:24] 截取"这是一张/该图展示了"等模板开头 → 弱查询必 MISS）
    for it in items[:]:
        if it["id"] not in cache or len(queries) >= 15:
            break
        cap = cache[it["id"]]["caption"]
        core = [w for w in re.findall(r"[\u4e00-\u9fff]{2,8}", cap) if w not in stop]
        if core:
            query = core[0] if len(core) == 1 else "".join(core[:2])
        else:
            query = cap[:24]
        queries.append({
            "query": query,
            "expected": [it["id"]],
            "layer": "descriptive",
            "expected_label": "screenshot",
        })
    QUERIES_OUT.write_text(json.dumps({"queries": queries[:15]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"查询已生成: {QUERIES_OUT}（{len(queries[:15])} 条）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
