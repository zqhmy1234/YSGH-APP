#!/usr/bin/env python3
"""真值 JSON → 本地库（PG `contents` + 分类 + Qdrant 索引）—— 全链路测试用

【为什么需要这个脚本】
`research/rag_benchmark/run_eval.py` 走的是**独立 Qdrant collection**
（`_index(None, ...)`，db 参数传 None），**完全绕过 PG**。所以"把真值灌成
contents 再走真实检索链路"是一条**此前不存在**的路径 —— 本脚本补上它，
让「建库 → 灌库 → 检索」这条全链路能真跑起来。

【数据从哪来】
`research/truth-data/{b,c,d}/{batch}_v*.json`（由 `scripts/convert_truth_xlsx.py` 产出）。
D 批目前没有正式产物（缺事件共识，见该脚本的 skeleton_d.json），故默认只灌 b,c。

【幂等】
content_id / user_id 都由 uuid5 稳定派生，重跑条数与内容不变：
  content_id = uuid5(NAMESPACE_URL, f"truth:{batch}:{collect_id}")   ← 与 build_truth_corpus 一致
  user_id    = uuid5(NAMESPACE_URL, f"truth-user:{user_id_hashed}")

【用法】
    export TRUTH_HASH_SALT='<与转换脚本同一个 salt>'      # 非必需，仅 --dry-run 打印用
    python scripts/load_truth_to_db.py --batches b,c --dry-run     # 只看将写入什么
    python scripts/load_truth_to_db.py --batches b,c --classify --index
    python scripts/load_truth_to_db.py --batches b,c --self-check  # 灌完自检检索命中

前置服务：PG（必需）、Qdrant + BGE-M3（--index / --self-check 需要）。
退出码：0 = 成功；1 = 有跳过/失败；2 = 环境错误（无数据 / 连不上库）。
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # 既支持直跑，也支持被按包导入（backend/tests 会 import）
    from gate_io import force_utf8  # noqa: E402
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8  # noqa: E402

force_utf8()

DATA_DIR = ROOT / "research" / "truth-data"
# 批 → content_type。值必须用**规范值**（见 pipeline.register_content_handler：
# text / voice / photo），不要用 "image"。
BATCH_CONTENT_TYPE = {"b": "text", "c": "voice", "d": "photo"}
ID_FIELD = {"b": "fragment_id", "c": "clip_id", "d": "set_id"}
SOURCE_TAG = "truth"  # 与生产 "app" 区分，便于回滚与识别


# ---------------------------------------------------------------- 纯函数（可单测）


def stable_content_id(batch: str, collect_id: str) -> str:
    """稳定 content_id —— **必须**与 scripts/build_truth_corpus._stable_uuid 一致。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"truth:{batch}:{collect_id}"))


def stable_user_id(user_id_hashed: str) -> str:
    """脱敏代号 → 稳定 uuid。

    `contents.user_id` 是 `UUID(as_uuid=False)`，`sha256:...` 串不能直接当 uuid 用。
    同一 `user_id_hashed` 恒定映射到同一 uuid，保住"按用户隔离评测"的语义。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"truth-user:{user_id_hashed}"))


def item_text(batch: str, rec: dict) -> str:
    """采集条目 → 入索引的正文。B 用 text，C 用 transcript，D 用主题拼装。"""
    if batch == "b":
        return rec.get("text") or ""
    if batch == "c":
        return rec.get("transcript") or ""
    parts: list[str] = []
    for key in ("expected_l1", "expected_l3"):
        for item in rec.get(key) or []:
            if item.get("theme") or item.get("title"):
                parts.append(str(item.get("theme") or item.get("title")))
    for ref in (rec.get("photo_refs") or [])[:5]:
        parts.append(str(ref))
    return "照片事件：" + "；".join(parts or [rec.get("set_id") or "未标注主题"])


def load_batch_records(batch: str, data_dir: Path = DATA_DIR) -> tuple[list[dict], Path | None]:
    """读 {batch}/{batch}_v*.json（取版本号最大的一个）。"""
    files = sorted((data_dir / batch).glob(f"{batch}_v*.json"))
    if not files:
        return [], None
    path = files[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data if isinstance(data, list) else []), path


# ---------------------------------------------------------------- 落库


def ensure_user(db, uid: str) -> bool:
    """确保 users 行存在。返回 True 表示本次新建。"""
    from app.db.models.auth import User

    if db.get(User, uid) is not None:
        return False
    # unionid / phone 留 NULL —— PG 的唯一约束对多 NULL 不冲突
    db.add(User(id=uid, nickname="truth-dataset", status=1))
    db.flush()
    return True


def upsert_content(db, rec: dict, batch: str, uid: str) -> tuple[str, bool]:
    """写一条 contents。返回 (content_id, 是否新建)。"""
    from app.db.models.content import Content

    collect_id = rec.get(ID_FIELD[batch]) or ""
    cid = stable_content_id(batch, collect_id)
    text = item_text(batch, rec).strip()
    taken_at = _parse_iso(rec.get("collected_at"))

    existing = db.get(Content, cid)
    if existing is None:
        content = Content(
            id=cid,
            user_id=uid,
            content_type=BATCH_CONTENT_TYPE[batch],
            text=text,
            taken_at=taken_at,
            source=SOURCE_TAG,
            status="ready",
        )
        if batch == "c":
            duration = rec.get("duration_s")
            content.duration = int(duration) if isinstance(duration, (int, float)) else None
            emotion = rec.get("emotion")
            if emotion:
                # 人工听感标注，来源标 human（与 ASR 推断区分）
                content.emotion = {"emotion": emotion, "source": "human"}
        db.add(content)
        db.flush()
        return cid, True

    existing.text = text
    existing.taken_at = taken_at
    existing.user_id = uid
    existing.status = "ready"
    return cid, False


def _parse_iso(value: str | None):
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def classify_inline(db, content) -> None:
    """复用 pipeline 的分类（SetFit）—— 失败静默，不阻断灌库。"""
    from app.services.pipeline import _classify_content

    _classify_content(db, content, content.text or "")


def index_inline(db, content) -> None:
    """复用 pipeline 的索引入口（dense+sparse → Qdrant yishu_contents）。"""
    from app.services.pipeline import _index_content

    _index_content(db, content, content.text)


# ---------------------------------------------------------------- 自检


def self_check(db, records: list[tuple[str, str, str, str]]) -> dict:
    """用每条内容自己的正文去搜，看它能否被召回 —— 验证链路通不通。

    records: [(content_id, user_id, batch, text)]
    ⚠️ 检索按 user_id 隔离（`search(..., user_id=...)` 会把过滤条件收窄到该用户），
    所以**必须按各自的 user_id 分别搜** —— 用一个全局 uid 会让其他用户的内容
    必然查不到，把链路正常误报成漏检。
    ⚠️ 这测的是「链路可用性」，**不是门禁达标** —— 用自己的句子搜自己属最简单情形。
    """
    from app.schemas.search import SearchQuery
    from app.services.rag import search

    hit_top1 = 0
    hit_top3 = 0
    total = 0
    failures: list[str] = []
    for cid, uid, _batch, text in records:
        if not text or not uid:
            continue
        total += 1
        result = search(SearchQuery(q=text[:200], limit=10), db=db, user_id=uid)
        ids = [h.content_id for h in result.hits]
        if cid in ids[:1]:
            hit_top1 += 1
        if cid in ids[:3]:
            hit_top3 += 1
        else:
            failures.append(cid)
    return {
        "n": total,
        "hit@1": round(hit_top1 / total, 4) if total else 0.0,
        "hit@3": round(hit_top3 / total, 4) if total else 0.0,
        "missed": failures[:20],
    }


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description="真值 JSON → 本地库（全链路测试）")
    parser.add_argument("--batches", default="b,c", help="要灌的批，逗号分隔（默认 b,c）")
    parser.add_argument("--truth-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--classify", action=argparse.BooleanOptionalAction, default=True,
                        help="跑 SetFit 分类回写 content_class（默认开）")
    parser.add_argument("--index", action=argparse.BooleanOptionalAction, default=True,
                        help="写 Qdrant 向量索引（默认开，需 BGE-M3 + Qdrant）")
    parser.add_argument("--aggregate", action=argparse.BooleanOptionalAction, default=False,
                        help="跑事件聚合（默认关，需更多前置数据）")
    parser.add_argument("--self-check", action="store_true",
                        help="灌完后用内容自检索自检（需 --index）")
    parser.add_argument("--limit", type=int, default=0, help="每批最多灌 N 条（0=不限）")
    parser.add_argument("--dry-run", action="store_true", help="只解析与打印，不连库")
    args = parser.parse_args()

    batches = [b.strip() for b in args.batches.split(",") if b.strip()]
    unknown = [b for b in batches if b not in BATCH_CONTENT_TYPE]
    if unknown:
        print(f"❌ 未知批次 {unknown}（可选：{sorted(BATCH_CONTENT_TYPE)}）")
        return 2

    loaded: dict[str, list[dict]] = {}
    for batch in batches:
        records, path = load_batch_records(batch, args.truth_dir)
        if path is None:
            print(f"⚠ 批 {batch}: 无真值文件（先跑 scripts/convert_truth_xlsx.py）")
            continue
        if args.limit:
            records = records[: args.limit]
        loaded[batch] = records
        print(f"[{batch}] {len(records)} 条 ← {path.name}")

    if not loaded:
        print("❌ 没有任何可灌的数据。")
        return 2

    if args.dry_run:
        for batch, records in loaded.items():
            for rec in records:
                cid = stable_content_id(batch, rec.get(ID_FIELD[batch]) or "")
                uid = stable_user_id(rec.get("user_id_hashed") or "")
                print(f"  {batch} {cid} user={uid}"
                      f" type={BATCH_CONTENT_TYPE[batch]} len(text)={len(item_text(batch, rec))}")
        print("\n（--dry-run：未连库）")
        return 0

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 无法导入后端 DB 会话（检查 backend/.env 的 DATABASE_URL）：{exc}")
        return 2

    db = SessionLocal()
    created = 0
    updated = 0
    users = 0
    check_rows: list[tuple[str, str, str, str]] = []  # (content_id, user_id, batch, text)
    try:
        for batch, records in loaded.items():
            for rec in records:
                uid = stable_user_id(rec.get("user_id_hashed") or "")
                if not rec.get("user_id_hashed"):
                    print(f"  ⚠ {batch} {rec.get(ID_FIELD[batch])}: 缺 user_id_hashed，跳过")
                    continue
                users += 1 if ensure_user(db, uid) else 0
                cid, is_new = upsert_content(db, rec, batch, uid)
                created += 1 if is_new else 0
                updated += 0 if is_new else 1
                check_rows.append((cid, uid, batch, item_text(batch, rec).strip()))
        db.flush()

        if args.classify:
            from app.db.models.content import Content

            for cid, _uid, _batch, _text in check_rows:
                classify_inline(db, db.get(Content, cid))
        db.commit()

        if args.index:
            from app.db.models.content import Content

            for cid, _uid, _batch, _text in check_rows:
                index_inline(db, db.get(Content, cid))

        if args.aggregate:
            from app.services.events import aggregate_user

            for uid in {stable_user_id(r.get("user_id_hashed") or "") for rs in loaded.values() for r in rs}:
                if uid:
                    aggregate_user(db, uid, mode="full")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"❌ 落库失败：{exc}")
        return 1
    finally:
        pass

    print(f"\n✅ contents 新建 {created} / 更新 {updated}｜users 新建 {users}"
          f"｜分类 {'开' if args.classify else '关'}｜索引 {'开' if args.index else '关'}")

    if args.self_check:
        if not args.index:
            print("⚠ --self-check 需要 --index，已跳过")
        else:
            report = self_check(db, check_rows)
            print(f"自检检索：{report['n']} 条 ｜ hit@1={report['hit@1']} ｜ hit@3={report['hit@3']}")
            if report["missed"]:
                print(f"  未进 top3：{report['missed']}")
            print("  ⚠ 这是**链路可用性**自检（用内容自己搜自己），不是门禁达标结论。")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
