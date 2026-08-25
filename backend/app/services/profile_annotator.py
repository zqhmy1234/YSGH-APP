"""profile_annotator.py —— B1 域：画像标注核心（阈值/池/开放枚举/更新规则/证据锚点）

任务归属：Wave 3 Agent I（B1 画像域）独占本文件。
消费 llm_ops.annotate.annotate() 的命中，按 B1 §2.3 更新规则写入 user_profile.dimensions：
- 置信度双门槛：普通 ≥0.7 / 超细性格 ≥0.8（阈值取自枚举集 confidence_threshold，零硬编码）
- <阈值 → profile_annotation_pool（低置信度事件池，周级批量复核）
- 开放枚举：同义归一（种子值/别名表）→ 直接新增 value（带证据+时间戳，查重防碎片）
- 更新规则：同值强度累加 / 异值替换+旧值进 history（最近 10 条）/ 同日同维度节流（高频用户）
- 证据锚点：内容级写 profile_l2_evidence；维度级 evidence 列表（证据原话 + 时间戳 = 新鲜度戳）
- 集合型维度（multi_value）：同值强度累加、异值追加（relation_core/life_event_major/兴趣等）

存储结构（user_profile.dimensions JSONB）：
- 单值维度：{"value", "strength", "confidence", "updated_at", "first_seen_at", "history", "evidence", "source"}
- 集合型：  {"values": [{"value","strength","confidence","first_seen_at","last_seen_at"}], "evidence", ...}
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import ProfileAnnotationPool, ProfileDimensionHistory, UserProfile
from app.services.llm_ops.annotate import annotate
from app.services.profile_schema import EnumSchema, get_schema

logger = logging.getLogger("yishu.profile_annotator")

# 历史值保留最近 10 条（B1 §2.3 有界列表）
HISTORY_LIMIT = 10
# 维度级证据锚点最多保留条数（新鲜度戳：证据按新旧打折）
MAX_EVIDENCE = 10
# 节流豁免来源：用户主动作答（冷启动访谈）不节流
_THROTTLE_SKIP_SOURCES = {"interview"}
# 池内 raw_text 截断（防超长）
_POOL_TEXT_MAX = 500


# ---------------------------------------------------------------- 对外入口
def annotate_content(db: Session, content, *, schema: EnumSchema | None = None) -> dict:
    """pipeline 钩子驱动：content.text（文本/语音转写/照片 caption）→ annotate → 落画像。

    fail-safe 由 pipeline_ext/profile.py 再兜一层（绝不阻断内容入库）。
    """
    text_value = (content.text or "").strip()
    if not text_value:
        return {"applied": 0, "pooled": 0}
    hits = annotate(text_value, schema=schema)
    return record_hits(
        db,
        content.user_id,
        hits,
        content_id=str(content.id),
        evidence_text=text_value,
        source="annotation",
        schema=schema,
    )


def record_hits(
    db: Session,
    user_id: str,
    hits: list[dict],
    *,
    content_id: str | None = None,
    evidence_text: str | None = None,
    source: str = "annotation",
    schema: EnumSchema | None = None,
) -> dict:
    """批量应用标注命中。

    hits: [{dimension, enum_value, confidence}]（llm_ops.annotate 输出或等构结构）。
    返回 {"applied": [...], "pooled": [...], "throttled": [...], "new_values": [...], "skipped": [...]}。
    """
    schema = schema or get_schema()
    result: dict = {"applied": [], "pooled": [], "throttled": [], "new_values": [], "skipped": []}
    applied_dims: set[str] = set()  # 本批已应用过的维度：批内同维不再节流（同一条内容的多次命中）
    for hit in hits or []:
        dim = hit.get("dimension")
        value = hit.get("enum_value")
        confidence = _to_confidence(hit.get("confidence"))
        if not dim or not value or dim not in schema.dimensions:
            result["skipped"].append({"dimension": dim, "reason": "unknown_dimension"})
            continue
        r = apply_annotation(
            db, user_id, dim, value, confidence,
            content_id=content_id, evidence_text=evidence_text, source=source, schema=schema,
            skip_throttle=(dim in applied_dims),
        )
        action = r.get("action")
        if action in ("added", "strengthened", "replaced"):
            applied_dims.add(dim)
            result["applied"].append(r)
        elif action == "pooled":
            result["pooled"].append(r)
        elif action == "throttled":
            result["throttled"].append(r)
        else:
            result["skipped"].append(r)
        if r.get("is_new"):
            result["new_values"].append(r)
    db.commit()
    return result


def apply_annotation(
    db: Session,
    user_id: str,
    dimension: str,
    enum_value: str,
    confidence: float,
    *,
    content_id: str | None = None,
    evidence_text: str | None = None,
    source: str = "annotation",
    schema: EnumSchema | None = None,
    skip_throttle: bool = False,
) -> dict:
    """单条命中应用（阈值 → 池 / 归一 → 节流 → 更新规则 → 证据锚点）。

    返回 {"action": pooled|strengthened|added|replaced|throttled|skipped, ...}。
    skip_throttle：同一条内容的多次命中（record_hits 批内同维）跳过同日节流，
    避免"一句话提到妈妈和老婆"只写入第一个。
    """
    schema = schema or get_schema()
    spec = schema.get(dimension)
    if spec is None:
        return {"action": "skipped", "dimension": dimension, "enum_value": enum_value, "reason": "unknown_dimension"}

    threshold = spec.threshold()
    if confidence < threshold:
        _add_to_pool(db, user_id, dimension, enum_value, confidence, content_id, evidence_text)
        return {
            "action": "pooled", "dimension": dimension, "enum_value": enum_value,
            "confidence": confidence, "threshold": threshold,
        }

    value = spec.canonicalize(enum_value)
    is_new = value is None
    if is_new:
        value = enum_value  # 开放枚举：直接新增（带证据+时间戳，查重已由 canonicalize 完成）

    profile = get_or_create_profile(db, user_id)
    dims = dict(profile.dimensions or {})
    entry = dims.get(dimension)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # 同日同维度节流（高频用户；用户主动作答 / 批内同维不节流）
    if not skip_throttle and source not in _THROTTLE_SKIP_SOURCES and _same_day_as(entry, now):
        return {"action": "throttled", "dimension": dimension, "enum_value": value, "confidence": confidence}

    if spec.multi_value:
        action, new_entry = _apply_multi(entry, value, confidence, now_iso, source)
    else:
        action, new_entry = _apply_single(entry, value, confidence, now_iso, source)

    # 证据锚点（证据原话 + 时间戳 = 新鲜度戳；"标签进思考、证据进话术"）
    if content_id or evidence_text:
        evidence = list(new_entry.get("evidence") or [])
        evidence.append({"content_id": content_id, "text": (evidence_text or "")[:_POOL_TEXT_MAX], "ts": now_iso})
        new_entry["evidence"] = evidence[-MAX_EVIDENCE:]

    dims[dimension] = new_entry
    profile.dimensions = dims
    profile.version = (profile.version or 1) + 1

    # 证据锚点写 profile_l2_evidence（内容级溯源）
    if content_id:
        _write_l2_evidence(db, dimension, user_id, content_id)

    # DB 级历史（每次应用写一行；写入侧裁剪保留最近 HISTORY_LIMIT 条）
    db.add(ProfileDimensionHistory(user_id=user_id, dimension=dimension, value=value))
    _trim_history(db, user_id, dimension)

    return {
        "action": action, "dimension": dimension, "enum_value": value,
        "confidence": confidence, "is_new": is_new, "threshold": threshold,
    }


# ---------------------------------------------------------------- 更新规则
def _apply_single(entry, value: str, confidence: float, now_iso: str, source: str) -> tuple[str, dict]:
    """单值维度：同值强度累加；异值替换 + 旧值进 history（最近 10 条）"""
    if entry and isinstance(entry, dict) and entry.get("value") == value:
        new_entry = {
            **entry,
            "strength": int(entry.get("strength") or 0) + 1,
            "confidence": confidence,
            "updated_at": now_iso,
            "source": source,
        }
        return "strengthened", new_entry
    history = list(entry.get("history") or []) if isinstance(entry, dict) else []
    action = "added"
    if isinstance(entry, dict) and entry.get("value") and entry["value"] != value:
        history.insert(0, entry["value"])
        history = history[:HISTORY_LIMIT]
        action = "replaced"
    base = entry if isinstance(entry, dict) else {}
    new_entry = {
        **base,
        "value": value,
        "strength": 1,
        "confidence": confidence,
        "updated_at": now_iso,
        "first_seen_at": base.get("first_seen_at") or now_iso,
        "history": history,
        "source": source,
    }
    return action, new_entry


def _apply_multi(entry, value: str, confidence: float, now_iso: str, source: str) -> tuple[str, dict]:
    """集合型维度：同值强度累加；异值追加（relation_core/人生大事/兴趣等）"""
    values = list(entry.get("values") or []) if isinstance(entry, dict) else []
    found_idx = next((i for i, v in enumerate(values) if isinstance(v, dict) and v.get("value") == value), None)
    if found_idx is not None:
        old = values[found_idx]
        values[found_idx] = {
            **old,
            "strength": int(old.get("strength") or 0) + 1,
            "last_seen_at": now_iso,
            "confidence": max(_to_confidence(old.get("confidence")), confidence),
        }
        action = "strengthened"
    else:
        values.append({
            "value": value, "strength": 1, "confidence": confidence,
            "first_seen_at": now_iso, "last_seen_at": now_iso,
        })
        action = "added"
    base = entry if isinstance(entry, dict) else {}
    new_entry = {
        **base,
        "values": values,
        "updated_at": now_iso,
        "first_seen_at": base.get("first_seen_at") or now_iso,
        "source": source,
    }
    return action, new_entry


# ---------------------------------------------------------------- 低置信度池
def _add_to_pool(
    db: Session, user_id: str, dimension: str, enum_value: str,
    confidence: float, content_id: str | None, evidence_text: str | None,
) -> None:
    """<阈值 的标注候选入低置信度事件池（周级批量复核，可作标注样本）"""
    db.add(ProfileAnnotationPool(
        user_id=user_id,
        event_id=content_id,
        raw_text=(evidence_text or enum_value)[:_POOL_TEXT_MAX],
        dimension=dimension,
        candidate_value=enum_value,
        confidence=confidence,
        status="pending",
    ))


# ---------------------------------------------------------------- 证据锚点 + 历史
def _write_l2_evidence(db: Session, dimension: str, user_id: str, content_id: str) -> None:
    """证据锚点写 profile_l2_evidence（内容级，供消费侧溯源「你怎么知道我…」）"""
    db.execute(
        text(
            "INSERT INTO profile_l2_evidence (dimension, user_id, evidence_content_ids) "
            "VALUES (:d, :u, CAST(:c AS jsonb))"
        ),
        {"d": dimension, "u": user_id, "c": json.dumps([content_id], ensure_ascii=False)},
    )


def _trim_history(db: Session, user_id: str, dimension: str) -> None:
    """历史裁剪：每维度仅保留最近 HISTORY_LIMIT 条（写入侧主动裁剪）

    注意 SessionLocal autoflush=False：先 flush 让刚加的 history 行进入查询范围，
    否则 keep_ids 会漏掉新行、越裁越多。
    """
    db.flush()
    keep_ids = db.execute(
        select(ProfileDimensionHistory.id)
        .where(
            ProfileDimensionHistory.user_id == user_id,
            ProfileDimensionHistory.dimension == dimension,
        )
        .order_by(ProfileDimensionHistory.updated_at.desc(), ProfileDimensionHistory.id.desc())
        .limit(HISTORY_LIMIT)
    ).scalars().all()
    if keep_ids:
        db.execute(
            delete(ProfileDimensionHistory).where(
                ProfileDimensionHistory.user_id == user_id,
                ProfileDimensionHistory.dimension == dimension,
                ProfileDimensionHistory.id.notin_(keep_ids),
            )
        )


# ---------------------------------------------------------------- 存储访问
def get_or_create_profile(db: Session, user_id: str) -> UserProfile:
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id, dimensions={}, version=1)
        db.add(profile)
        db.flush()  # 立即落 id —— 同一事务内多次 apply_annotation 各查一次，防重复插入同一 user_id
    return profile


def display_dimensions(dimensions: dict) -> dict[str, list[str]]:
    """结构化 dimensions → {dim: [当前值]}（API 契约 / 复述展示用）

    兼容：单值维度取 value、集合型取 values[].value、旧扁平列表格式原样。
    """
    out: dict[str, list[str]] = {}
    for dim, entry in (dimensions or {}).items():
        if isinstance(entry, dict):
            if isinstance(entry.get("values"), list):
                out[dim] = [str(v.get("value")) for v in entry["values"] if isinstance(v, dict) and v.get("value")]
            elif entry.get("value"):
                out[dim] = [str(entry["value"])]
            else:
                out[dim] = []
        elif isinstance(entry, list):
            out[dim] = [str(v) for v in entry]
    return out


# ---------------------------------------------------------------- 工具
def _same_day_as(entry, now: datetime) -> bool:
    """entry.updated_at 与 now 是否同一天（同日同维度节流）"""
    if not entry or not isinstance(entry, dict):
        return False
    raw = entry.get("updated_at")
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    return last.date() == now.date()


def _to_confidence(raw) -> float:
    try:
        conf = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, conf))
