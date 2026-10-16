"""图片向量重建 —— 图片塔切换后重灌 image_vec（R-4 · 2026-10-14）

## 什么时候必须跑

`image_vec` 同一槽位里可能有两种**互不相通**的向量空间（`bge_caption` / `vision`）。
切换 `image_tower_backend` 之后，旧点仍带着旧空间的向量；此时若直接切配置，
新查询向量会与旧点混检索、**余弦分不可比 → 排序全错（不报错、不崩溃）**。

**正确顺序（不可颠倒）**：

    1. 先跑本脚本把已有图片点全部重灌进新空间   ← 你在这里
    2. 再改 IMAGE_TOWER_BACKEND / settings.image_tower_backend
    3. 跑 research/rag_benchmark/run_image_probe.py 复测延迟

> 为什么不能先切：检索端 `vector_store._with_image_space` 对**缺 `image_space` 键**的点
> 是 fail-open 的（兼容旧数据，不丢点）。所以先切配置的话，过渡窗口内旧空间向量仍会被
> 检到 —— 不会崩，但排序质量暂时下降。先重灌则稳态下缺失集为空，隔离干净。

## 目标

    --target prod    生产：PG contents 里 content_type='photo' 且 cos_key 非空 →
                     storage.get_object(cos_key) → 编码 → 写回**生产 collection**
    --target bench   评测：从 yishu_benchmark 读出已有的图片点，按 corpus.json 的本地
                     图片重编码（图片不在本机时降级用 caption 文本通道，脚本会提示）

用法：
    python scripts/rebuild_image_index.py --target bench --dry-run
    MOCK_EXTERNAL_AI=false infisical run --env=dev --silent -- \
        python scripts/rebuild_image_index.py --target prod
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

try:
    from gate_io import force_utf8
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

BENCH = ROOT / "research" / "rag_benchmark"
CACHE = ROOT / ".cowork-temp" / "image_vec_rebuild.json"


def _load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _reembed_and_write(content_id: str, image_path: str | None, caption: str | None,
                       payload: dict, collection: str | None) -> str:
    """重编码并写回（幂等：upsert_image_vec 走读回合并，不冲掉 text_vec/text_sparse）"""
    from app.services.image_tower import encode_image_vector
    from app.services.vector_store import get_store

    vec, space = encode_image_vector(image_path=image_path, caption=caption)
    get_store().upsert_image_vec(
        content_id=content_id, vec=vec,
        payload={**payload, "image_space": space}, collection=collection,
    )
    return space


def _rebuild_prod(max_n: int, dry_run: bool, cache: dict) -> tuple[int, int]:
    """生产：PG 里逐条照片回源 COS → 重编码。cos_key 就在 contents 行上，无需 join。"""
    from app.db.models import Content
    from app.db.session import SessionLocal
    from app.services.external.storage import get_storage_backend
    from app.services.vector_store import default_collection
    from sqlalchemy import select

    done = failed = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(Content).where(Content.content_type == "photo",
                                  Content.cos_key.is_not(None)).limit(max_n)
        ).scalars().all()
        print(f"[prod] 待处理 {len(rows)} 条（collection={default_collection()}）")
        for c in rows:
            cid = str(c.id)
            if cid in cache and cache[cid].get("space"):
                continue
            if dry_run:
                print(f"  [dry] {cid}  cos_key={c.cos_key}  caption={(c.text or '')[:24]}")
                continue
            tmp: Path | None = None
            try:
                data = get_storage_backend().get_object(c.cos_key)
                tmp = Path(tempfile.gettempdir()) / f"yishu_rebuild_{cid}.jpg"
                tmp.write_bytes(data)
                space = _reembed_and_write(cid, str(tmp), c.text,
                                           {"text": c.text, "content_type": "photo"}, None)
                cache[cid] = {"space": space}
                done += 1
            except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断整批（点保留旧向量，不丢）
                failed += 1
                print(f"  ✗ {cid}: {type(exc).__name__}: {exc}")
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
    return done, failed


def _rebuild_bench(max_n: int, dry_run: bool, cache: dict) -> tuple[int, int]:
    """评测库：读已有图片点的 payload（含 caption），按 corpus.json 的本地图重编码。"""
    from app.services.vector_store import get_store

    corpus = BENCH / "corpus.json"
    paths: dict[str, str] = {}
    if corpus.exists():
        data = json.loads(corpus.read_text(encoding="utf-8"))
        items = data.get("items", data) if isinstance(data, dict) else data
        paths = {it["id"]: it.get("path", "") for it in items}
        print(f"[bench] corpus.json 提供 {len(paths)} 条图片路径")
    else:
        print("⚠️ corpus.json 不存在（gitignore）—— 无本地图片，将降级用 caption 文本通道")

    store = get_store()
    points, _ = store.client.scroll("yishu_benchmark", limit=max_n, with_payload=True,
                                    with_vectors=False)
    print(f"[bench] 待处理 {len(points)} 个点（collection=yishu_benchmark）")
    done = failed = 0
    for p in points:
        pl = p.payload or {}
        cid = str(pl.get("content_id") or p.id)
        if cid in cache and cache[cid].get("space"):
            continue
        img = paths.get(cid) or None
        if img and not Path(img).exists():
            img = None
        if dry_run:
            print(f"  [dry] {cid}  image={'有' if img else '无(caption 降级)'}  "
                  f"caption={(pl.get('text') or '')[:24]}")
            continue
        try:
            space = _reembed_and_write(cid, img, pl.get("text"),
                                       {k: v for k, v in pl.items() if k != "image_space"},
                                       "yishu_benchmark")
            cache[cid] = {"space": space}
            done += 1
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断整批
            failed += 1
            print(f"  ✗ {cid}: {type(exc).__name__}: {exc}")
    return done, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["bench", "prod", "all"], default="bench")
    ap.add_argument("--max", type=int, default=100000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.core.config import settings
    from app.services.image_tower import current_image_space

    print(f"目标空间 = {current_image_space()}（image_tower_backend={settings.image_tower_backend}）")
    if settings.image_tower_backend == "bge_caption":
        print("⚠️ 当前后端是 bge_caption —— 重灌只会把点重新标成 bge_caption 空间，")
        print("   这本身没坏处，但通常你要的是先切到 dashscope_vision 再重灌（顺序见文件头）。")

    cache = _load_cache()
    total_done = total_failed = 0
    for tgt in (["bench", "prod"] if args.target == "all" else [args.target]):
        fn = {"bench": _rebuild_bench, "prod": _rebuild_prod}[tgt]
        d, f = fn(args.max, args.dry_run, cache)
        total_done += d
        total_failed += f
        print(f"[{tgt}] 重建 {d} 条 / 失败 {f}")

    if not args.dry_run:
        _save_cache(cache)
    print(f"\n合计重建 {total_done} / 失败 {total_failed}"
          f"{'（dry-run，未落盘）' if args.dry_run else f'；断点缓存 {CACHE}'}")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
