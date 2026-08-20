"""纠错闭环服务（B5-c 三层裁决 · F2 补全）

三层裁决（B5-c-2 已收敛）：
  新内容 → ① 个人规则层（correction_log 向量相似 >0.8 且同类型）→ 应用个人纠错结果
         → ② 未命中 → 全局 SetFit 分类（公共语义）
         → ③ 共性纠错回流（is_global_candidate ≥50 条触发全局微调，脚本侧）

向量存储：Qdrant `corrections` collection（B5-c-3：复用 B2 管线零新增；
schema.sql 注记：content_embedding 列 MVP 以 qdrant_point_id 替代）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from qdrant_client import QdrantClient
from qdrant_client.http import models
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CorrectionLog
from app.services.embedding import encode_dense
from app.services.vector_store import VECTOR_SIZE, get_qdrant_client

logger = logging.getLogger("yishu.correction")

CORRECTION_COLLECTION = "corrections"
SIMILARITY_THRESHOLD = 0.8   # B5-c-3：余弦 >0.8 判同类
MAX_PER_USER = 500           # B5-c-1：保留最近 500 条/用户
GLOBAL_RETRAIN_THRESHOLD = 50  # B5-c-4：共性纠错 ≥50 条才触发 SetFit 微调


def _get_store() -> QdrantClient:
    """Qdrant 客户端（P2-04 收敛：统一走 vector_store.get_qdrant_client 单例）

    corrections 集合幂等建；地址走配置（修复硬编码）。
    """
    client = get_qdrant_client()
    existing = [c.name for c in client.get_collections().collections]
    if CORRECTION_COLLECTION not in existing:
        client.create_collection(
            collection_name=CORRECTION_COLLECTION,
            vectors_config={
                "text_vec": models.VectorParams(
                    size=VECTOR_SIZE, distance=models.Distance.COSINE
                )
            },
        )
    return client


def _point_id(user_id: str, content_id: str) -> str:
    """纠错点 ID（user+content 稳定派生，同内容纠错覆盖旧点 = 最后一次为准）"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{user_id}:{content_id}"))


def record_correction(
    db: Session,
    user_id: str,
    content_id: str,
    text: str,
    new_label: str,
    old_label: str | None = None,
    source: str = "active",
    content_type: str = "text",
    confidence: float | None = None,
) -> CorrectionLog:
    """记录纠错（第①层数据源）：BGE-M3 向量 → Qdrant + correction_log 落库

    生命周期（B5-c-1）：同内容多次纠错以最后一次为准（Qdrant 同点覆盖；
    DB 查询取最新）；保留最近 MAX_PER_USER 条/用户。
    """
    store = _get_store()
    dense = encode_dense([text])[0]
    pid = _point_id(user_id, content_id)

    store.upsert(
        collection_name=CORRECTION_COLLECTION,
        points=[
            models.PointStruct(
                id=pid,
                vector={"text_vec": dense},
                payload={
                    "user_id": user_id,
                    "content_id": content_id,
                    "content_type": content_type,
                    "old_label": old_label,
                    "new_label": new_label,
                    "source": source,
                    "text": text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        ],
    )

    # DB 落库（同 content 旧记录逻辑上被新记录取代）
    row = CorrectionLog(
        user_id=user_id,
        content_id=content_id,
        content_type=content_type,
        qdrant_point_id=pid,
        old_label=old_label,
        new_label=new_label,
        source=source,
        confidence=confidence,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # 保留最近 MAX_PER_USER 条/用户（B5-c-1：旧的归档防膨胀）
    _trim_user_corrections(db, user_id)
    return row


def _trim_user_corrections(db: Session, user_id: str) -> None:
    """裁剪：用户纠错超过 MAX_PER_USER 时删除最旧记录（含 Qdrant 点）"""
    count = db.scalar(
        select(func.count()).select_from(CorrectionLog).where(CorrectionLog.user_id == user_id)
    )
    if count <= MAX_PER_USER:
        return
    keep_ids = [
        r[0]
        for r in db.execute(
            select(CorrectionLog.id)
            .where(CorrectionLog.user_id == user_id)
            .order_by(CorrectionLog.created_at.desc())
            .limit(MAX_PER_USER)
        )
    ]
    doomed = db.execute(
        select(CorrectionLog).where(
            CorrectionLog.user_id == user_id, CorrectionLog.id.not_in(keep_ids)
        )
    ).scalars()
    store = _get_store()
    for row in doomed:
        if row.qdrant_point_id:
            try:
                store.delete(
                    collection_name=CORRECTION_COLLECTION,
                    points_selector=models.PointIdsList(points=[row.qdrant_point_id]),
                )
            except Exception:  # noqa: BLE001 —— 裁剪失败不阻断主流程
                logger.warning("裁剪纠错点失败: %s", row.qdrant_point_id)
        db.delete(row)
    db.commit()


# 三道防噪音闸门（B5-c-5 · 审查 MAJOR 修复 2026-08-20）
PASSIVE_SOURCE_MIN_CONSISTENT = 3   # 被动确认（echo/org）需 ≥3 次一致才生效
REVERT_WINDOW_DAYS = 3               # 3 天回改窗口：A→B 后 3 天内 B→A 视为抖动不生效
PASSIVE_SOURCES = ("echo", "org")


def _passive_confirmation_count(
    db: Session, user_id: str, old_label: str, new_label: str, content_type: str
) -> int:
    """被动来源一致纠错累计次数：同一 (user, old→new) 对在被动来源下的次数"""
    return db.scalar(
        select(func.count())
        .select_from(CorrectionLog)
        .where(
            CorrectionLog.user_id == user_id,
            CorrectionLog.old_label == old_label,
            CorrectionLog.new_label == new_label,
            CorrectionLog.content_type == content_type,
            CorrectionLog.source.in_(PASSIVE_SOURCES),
        )
    ) or 0


def _recent_revert(
    db: Session, user_id: str, old_label: str, new_label: str, content_type: str
) -> bool:
    """回改检测：3 天内是否存在反向纠错 (new→old)——若存在说明用户在来回改，当前方向不生效"""
    window_start = datetime.now(timezone.utc) - timedelta(days=REVERT_WINDOW_DAYS)
    cnt = db.scalar(
        select(func.count())
        .select_from(CorrectionLog)
        .where(
            CorrectionLog.user_id == user_id,
            CorrectionLog.old_label == new_label,
            CorrectionLog.new_label == old_label,
            CorrectionLog.content_type == content_type,
            CorrectionLog.created_at >= window_start,
        )
    ) or 0
    return cnt > 0


def apply_personal_rule(
    db: Session,
    user_id: str,
    text: str,
    content_type: str = "text",
) -> dict | None:
    """第①层个人规则：纠错向量相似 >0.8 且同类型 → 返回最新纠错结果

    命中条件（B5-c-1/3）：先同类型（照片纠错只匹配照片）→ 余弦 >0.8。
    返回 {label, new_label, old_label, similarity, correction_id} 或 None。
    """
    store = _get_store()
    dense = encode_dense([text])[0]
    hits = store.query_points(
        collection_name=CORRECTION_COLLECTION,
        query=dense,
        using="text_vec",
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                models.FieldCondition(
                    key="content_type", match=models.MatchValue(value=content_type)
                ),
            ]
        ),
        limit=10,
    ).points
    if not hits or hits[0].score < SIMILARITY_THRESHOLD:
        return None
    hit = hits[0]
    source = hit.payload.get("source")
    # 三道防噪音闸门（B5-c-5 · 审查 MAJOR 修复）：
    # ① 被动来源（echo/org 被动确认）需同一 (user, old→new) 一致 ≥3 次才生效——
    #    随手误点一次不改变全局行为（峰宝确认口径）
    # ② 3 天回改检测：窗口内存在反向纠错 → 判定用户在来回改，不生效
    if source in PASSIVE_SOURCES:
        old_label = hit.payload.get("old_label")
        new_label = hit.payload.get("new_label")
        if old_label and new_label:
            consistent = _passive_confirmation_count(
                db, user_id, old_label, new_label, content_type
            )
            if consistent < PASSIVE_SOURCE_MIN_CONSISTENT:
                logger.info(
                    "被动纠错未达一致阈值 %d/%d（user=%s old=%s→new=%s），不生效",
                    consistent, PASSIVE_SOURCE_MIN_CONSISTENT, user_id, old_label, new_label,
                )
                return None
            if _recent_revert(db, user_id, old_label, new_label, content_type):
                logger.info("3 天回改窗口内存在反向纠错，不生效（user=%s）", user_id)
                return None
    return {
        "label": hit.payload.get("new_label"),
        "new_label": hit.payload.get("new_label"),
        "old_label": hit.payload.get("old_label"),
        "similarity": round(float(hit.score), 4),
        "correction_id": str(hit.id),
        "content_id": hit.payload.get("content_id"),
        "source": source,
    }


def arbitrate(
    db: Session,
    user_id: str,
    text: str,
    content_type: str = "text",
) -> dict:
    """三层裁决主入口（B5-c-2）：

    ① 个人规则命中 → 个人纠错结果（用户纠错立即生效）
    ② 未命中 → 全局 SetFit 分类
    ③ 共性纠错回流由 mark_global_candidates + 微调脚本负责（第③层）
    """
    personal = apply_personal_rule(db, user_id, text, content_type)
    if personal is not None:
        return {
            "label": personal["label"],
            "label_cn": _label_cn(personal["label"]),
            "layer": "personal",
            "similarity": personal["similarity"],
            "source": personal["source"],
        }
    from app.services.classifier import classify

    result = classify(text)
    return {
        "label": result["label"],
        "label_cn": result["label_cn"],
        "confidence": result["confidence"],
        "layer": "global",
    }


def arbitrate_job(user_id: str, text: str, content_type: str = "text") -> dict:
    """RQ 任务：三层裁决（P2-01 推理移 worker——含 SetFit ~27s，API 只入队）"""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return arbitrate(db, user_id, text, content_type)
    finally:
        db.close()


def mark_global_candidates(db: Session) -> int:
    """第③层共性纠错扫描：同一 (old→new) 纠错对出现 ≥2 个不同用户 → 全局候选

    返回新标记数量（B5-c-4：累计 ≥50 触发全局微调，脚本侧决策）。
    """
    rows = db.execute(select(CorrectionLog)).scalars()
    pairs: dict[tuple, set[str]] = {}
    for row in rows:
        key = (row.old_label, row.new_label, row.content_type)
        pairs.setdefault(key, set()).add(row.user_id)

    marked = 0
    for (old_label, new_label, content_type), users in pairs.items():
        if len(users) >= 2:
            updated = db.execute(
                CorrectionLog.__table__.update()
                .where(
                    CorrectionLog.old_label == old_label,
                    CorrectionLog.new_label == new_label,
                    CorrectionLog.content_type == content_type,
                    CorrectionLog.is_global_candidate.is_(False),
                )
                .values(is_global_candidate=True)
            )
            marked += updated.rowcount or 0
    db.commit()
    return marked


def global_candidate_count(db: Session) -> int:
    """共性纠错候选数（≥GLOBAL_RETRAIN_THRESHOLD 提示触发全局微调）"""
    return db.scalar(
        select(func.count())
        .select_from(CorrectionLog)
        .where(CorrectionLog.is_global_candidate.is_(True))
    ) or 0


def _label_cn(label: str) -> str:
    """标签英文 → 中文（审查 P1-09：引用 classifier 权威词表，消除重复定义）"""
    from app.services.classifier import LABEL_CN_MAP

    return LABEL_CN_MAP.get(label, label)
