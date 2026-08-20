"""事件聚合：按拍摄时间聚类 + 视觉相似度合并。"""

import os
from datetime import datetime

import numpy as np
from dotenv import load_dotenv

import db_milvus
import db_mysql

load_dotenv()

GAP_MINUTES = int(os.getenv("EVENT_GAP_MINUTES", "30"))
SIM_THRESHOLD = float(os.getenv("EVENT_SIM_THRESHOLD", "0.8"))
# 视觉合并只作用于"略超时间阈值"的间隔（默认 60 分钟 = 2×30），防止跨天误合并
MERGE_MAX_MINUTES = int(os.getenv("EVENT_MERGE_MAX_MINUTES", "60"))


def _parse_time(value):
    if not value:
        return None
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _vectors_by_asset():
    """返回 {asset_id: 归一化向量}（从本地向量库读取）。"""
    store = db_milvus.get_store()
    out = {}
    for aid, vec in zip(store.asset_ids, store.vectors):
        v = np.array(vec, dtype=float)
        n = np.linalg.norm(v)
        if n > 0:
            out[int(aid)] = v / n
    return out


def _cosine(a, b):
    return float(np.dot(a, b))


def _cover_asset(asset_ids, vectors):
    """事件内与其他照片平均相似度最高的那张作为封面。"""
    ids = list(asset_ids)
    vecs = [vectors[i] for i in ids if i in vectors]
    if not vecs:
        return ids[0]
    best, best_score = ids[0], -1.0
    for aid, v in zip(ids, vecs):
        score = sum(_cosine(v, u) for u in vecs) / len(vecs)
        if score > best_score:
            best, best_score = aid, score
    return best


def rebuild_events(user_id: str) -> dict:
    """全量重算某用户的事件分组（幂等：先清空旧分组）。"""
    assets = db_mysql.list_assets(user_id)
    if not assets:
        return {"event_count": 0, "assets": 0}

    vectors = _vectors_by_asset()
    items = []
    for a in assets:
        t = _parse_time(a.get("original_time")) or _parse_time(a.get("created_at"))
        if t is None:
            continue
        items.append({"asset_id": a["id"], "time": t})
    items.sort(key=lambda x: x["time"])
    if not items:
        return {"event_count": 0, "assets": 0}

    # 时间聚类 + 视觉合并
    groups = []
    for item in items:
        if groups:
            last = groups[-1][-1]
            gap_min = (item["time"] - last["time"]).total_seconds() / 60.0
            if gap_min <= GAP_MINUTES:
                groups[-1].append(item)
                continue
            # 视觉合并：间隔"略超"阈值（≤ MERGE_MAX_MINUTES）且相邻照片非常相似 → 仍归同一事件
            if SIM_THRESHOLD > 0 and gap_min <= MERGE_MAX_MINUTES:
                a_vec = vectors.get(last["asset_id"])
                b_vec = vectors.get(item["asset_id"])
                if a_vec is not None and b_vec is not None and _cosine(a_vec, b_vec) >= SIM_THRESHOLD:
                    groups[-1].append(item)
                    continue
        groups.append([item])

    # 落库
    db_mysql.reset_events(user_id)
    summary = []
    for g in groups:
        ids = [x["asset_id"] for x in g]
        start = g[0]["time"].strftime("%Y-%m-%d %H:%M:%S")
        end = g[-1]["time"].strftime("%Y-%m-%d %H:%M:%S")
        cover = _cover_asset(ids, vectors)
        eid = db_mysql.insert_event(user_id, start, end, len(ids), cover)
        for aid in ids:
            db_mysql.assign_asset_to_event(aid, eid)
        summary.append(
            {"event_id": eid, "start": start, "end": end, "photo_count": len(ids), "cover_asset_id": cover}
        )

    return {
        "event_count": len(groups),
        "assets": len(items),
        "events": summary,
        "config": {
            "gap_minutes": GAP_MINUTES,
            "sim_threshold": SIM_THRESHOLD,
            "merge_max_minutes": MERGE_MAX_MINUTES,
        },
    }
