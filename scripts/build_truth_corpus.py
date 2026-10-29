#!/usr/bin/env python3
"""B/C/D 采集语料 ingestion —— 评测 corpus 构建（RAG评测体系 §6.2/§7）

产品部采集的 B（文字碎片）/ C（语音片段）/ D（照片事件）批 → 评测 corpus：
- 每条采集条目分配稳定 content_id（uuid5：uuid5(NAMESPACE_URL, f"truth:{batch}:{采集id}")）
- 模态对齐：B→text、C→voice、D→image（D 无正文文本，text 用主题/照片引用拼装）
- 输出 research/rag_benchmark/truth_corpus/{corpus_b_truth,corpus_c_truth,
  corpus_d_truth,corpus_manifest}.json
- corpus_manifest.json：采集 id ↔ uuid 映射 + 模态 + 关联事件（A 批 expected 重映射用）
- A 批 expected 重映射：--remap-a 读 research/truth-data/a/a_v*.json → expected 的
  采集 id 替换为 uuid → 写 a_remapped.json

现状（无真实采集数据时）：B/C/D 批为空 → 打印提示并退出 0（CI 有数据后运行）。
corpus 规模目标（§7.2）：每模态 ≥60 条起；模态占比逼近 文:图:语音 = 4:4:2。

用法：
  python scripts/build_truth_corpus.py
  python scripts/build_truth_corpus.py --remap-a
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

try:  # 既支持直跑 python scripts/x.py，也支持被按包导入（from scripts.x import …）
    from gate_io import force_utf8  # noqa: E402
except ModuleNotFoundError:  # 仅「按包导入」路径会走到这里（sys.path 里没有 scripts/）
    from scripts.gate_io import force_utf8  # noqa: E402

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "truth-data"
OUT_DIR = ROOT / "research" / "rag_benchmark" / "truth_corpus"

BATCH_DIRS = {"b": "b", "c": "c", "d": "d"}
MODAL_MAP = {"b": "text", "c": "voice", "d": "image"}
# D 批（照片事件）正文占位前缀：D 无自然语言正文，用事件主题拼装 caption
_D_TEXT_PREFIX = "照片事件："


def _load_batch(batch: str) -> tuple[list[dict], Path | None]:
    """读 {batch}_v*.json（版本号最大），返回 (records, path)；无数据 → ([], None)"""
    files = sorted((DATA_DIR / BATCH_DIRS[batch]).glob(f"{batch}_v*.json"))
    if not files:
        return [], None
    path = files[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data if isinstance(data, list) else []), path


def _stable_uuid(batch: str, collect_id: str) -> str:
    """稳定 uuid5：同一采集条目在多次 ingestion 间 id 不变（幂等，A 批引用稳定）"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"truth:{batch}:{collect_id}"))


def _item_text(rec: dict, batch: str, manifest: dict) -> str:
    """采集条目 → 检索用正文（B/C 原文；D 用主题 + 照片引用拼装 caption）"""
    if batch == "b":
        return rec.get("text") or ""
    if batch == "c":
        return rec.get("transcript") or ""
    # D 批：优先 expected_l1 主题，否则照片引用文件名
    parts: list[str] = []
    for l1 in rec.get("expected_l1") or []:
        if l1.get("theme"):
            parts.append(str(l1["theme"]))
    for l3 in rec.get("expected_l3") or []:
        if l3.get("theme"):
            parts.append(str(l3["theme"]))
    refs = rec.get("photo_refs") or []
    if refs and not parts:
        parts.append("、".join(str(r) for r in refs[:5]))
    if not parts:
        parts.append(rec.get("set_id") or "未标注主题")
    return _D_TEXT_PREFIX + "；".join(parts)


def build_corpus() -> dict:
    """主流程：B/C/D 批 → corpus 文件 + manifest（幂等可重跑）"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "_meta": {
            "version": 1,
            "note": "采集 id ↔ 稳定 uuid 映射 + 模态（A 批 expected 经此重映射）",
            "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "entries": {},
    }
    total: dict[str, int] = {}
    any_data = False

    for batch in ("b", "c", "d"):
        records, path = _load_batch(batch)
        total[batch] = len(records)
        if not records:
            print(f"[{batch}] 无采集数据（{DATA_DIR / BATCH_DIRS[batch]}/ 为空或缺失，模板见 templates/）")
            continue
        any_data = True
        items: list[dict] = []
        modal = MODAL_MAP[batch]
        for rec in records:
            collect_id = rec.get("fragment_id") or rec.get("clip_id") or rec.get("set_id") or ""
            if not collect_id:
                print(f"  ⚠ 跳过无 id 条目: {rec}")
                continue
            cid = _stable_uuid(batch, collect_id)
            text = _item_text(rec, batch, manifest).strip()
            if not text:
                print(f"  ⚠ 跳过无正文条目 {collect_id}（{batch}）")
                continue
            items.append({
                "id": cid,
                "collect_id": collect_id,
                "text": text,
                "content_type": modal,
                "label": rec.get("label") or (modal if modal != "image" else "mixed"),
                "source_batch": batch,
            })
            # 2026-10-27 修复：B / C 两批存在同名采集 id（a-0001…a-0009、a'-0001… 等），
            # 原来只以 collect_id 为键会让后一批覆盖前一批（manifest 丢条目、A 批重映射撞车）。
            # 键改为 `批次:采集id`，uuid 本来就含 batch，语义一致。
            manifest["entries"][f"{batch}:{collect_id}"] = {
                "uuid": cid,
                "batch": batch,
                "collect_id": collect_id,
                "modal": modal,
                "event_id": None,  # D 批事件关联（未来 B3 事件聚合回填）
            }
        (OUT_DIR / f"corpus_{batch}_truth.json").write_text(
            json.dumps({"_meta": {"version": 1, "note": f"采集语料 {batch} 批", "count": len(items)}, "items": items},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[{batch}] {len(records)} 条采集 → {len(items)} 条 corpus（{modal}）: "
              f"{OUT_DIR / f'corpus_{batch}_truth.json'}")

    if not any_data:
        print("⚠ 无任何 B/C/D 采集数据——corpus 未生成（模板见 research/truth-data/templates/，"
              "CI 有真实数据后运行本脚本）")
    (OUT_DIR / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"manifest: {OUT_DIR / 'corpus_manifest.json'}（{len(manifest['entries'])} 条映射）")
    print(f"模态分布: {total}（目标 文:图:语音=4:4:2，每模态 ≥60 条）")
    return manifest


def _ref_batches(ref_batch: str) -> list[str]:
    """'B批, C批' → ['b', 'c']（A 批 ref_batch 列的中文批次声明）"""
    s = (ref_batch or "").upper()
    return [b for b in ("b", "c", "d") if b.upper() in s]


def _map_collect_id(rid: str, ref_batch: str, entries: dict) -> str | None:
    """采集 id → uuid。

    优先按 ref_batch 限定批次；未声明批次时要求**全库唯一**命中。
    返回 None 表示找不到或指向不明（保留原 id，由调用方计数）。
    """
    batches = _ref_batches(ref_batch)
    if batches:
        hits = [entries[f"{b}:{rid}"]["uuid"] for b in batches if f"{b}:{rid}" in entries]
    else:
        hits = [ent["uuid"] for key, ent in entries.items() if key.endswith(f":{rid}")]
    if len(hits) == 1:
        return hits[0]
    return None


def remap_a(manifest: dict) -> int:
    """A 批 expected 重映射：采集 id → 稳定 uuid（写 a_remapped.json）

    2026-10-27：按 A 批 `ref_batch` 消歧。B/C 同名 id（a-0004 等）在旧实现里
    会互相覆盖，现已按 `批次:采集id` 建 manifest，并在重映射时按 ref_batch 限定。
    """
    a_files = sorted((DATA_DIR / "a").glob("a_v*.json"))
    if not a_files:
        print("A 批无数据，跳过重映射")
        return 0
    path = a_files[-1]
    queries = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest.get("entries", {})
    remapped = unmapped = 0
    for q in queries:
        new_ids: list[str] = []
        for rid in q.get("expected") or []:
            if rid in {ent["uuid"] for ent in entries.values()}:
                new_ids.append(rid)  # 已经是 uuid（幂等）
                continue
            uuid = _map_collect_id(rid, q.get("ref_batch") or "", entries)
            if uuid is None:
                new_ids.append(rid)
                unmapped += 1
                print(f"  ⚠ 无法重映射 {q.get('query_id')} → {rid}"
                      f"（ref_batch={q.get('ref_batch') or '未声明'}）")
            else:
                new_ids.append(uuid)
        if new_ids != (q.get("expected") or []):
            q["expected"] = new_ids
            remapped += 1
    out = DATA_DIR / "a" / "a_remapped.json"
    out.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"A 批重映射完成: {len(queries)} 条查询（{remapped} 条已替换；{unmapped} 个 id 未映射）→ {out}")
    return remapped


def main() -> int:
    parser = argparse.ArgumentParser(description="B/C/D 采集语料 → 评测 corpus（RAG评测体系 §7）")
    parser.add_argument("--remap-a", action="store_true", help="按 manifest 重映射 A 批 expected 为 uuid")
    args = parser.parse_args()

    manifest = build_corpus()
    if args.remap_a:
        remap_a(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
