"""端云对账服务（S5-04 · WP-D 2026-08-19）

基于 B4 同步的权威状态做快照比对，输出差异报告供客户端修复：
  - missing_on_cloud：客户端有、云端无 → 客户端应 push
  - missing_on_client：云端有、客户端无 → 客户端应 pull
  - divergent：双方都有但 updated_at 不同 → 按 LWW 判定谁新（旧端拉取新版本）
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SyncFieldVersion
from app.services.sync_common import TOMBSTONE_FIELD, lww_wins, parse_ts


def _cloud_entities(db: Session, user_id: str) -> dict[str, dict]:
    """云端权威状态：entity → {updated_at, deleted, fields: {...}}（按字段 max updated_at）"""
    rows = db.scalars(
        select(SyncFieldVersion).where(SyncFieldVersion.user_id == user_id)
    ).all()
    entities: dict[str, dict] = {}
    for row in rows:
        ent = entities.setdefault(
            row.entity_id,
            {"updated_at": datetime.min.replace(tzinfo=timezone.utc), "deleted": False, "fields": {}},
        )
        ts = row.updated_at
        if lww_wins(ent["updated_at"], ts):
            ent["updated_at"] = ts
        if row.field == TOMBSTONE_FIELD and row.deleted:
            ent["deleted"] = True
        ent["fields"][row.field] = row.value
    return entities


def reconcile_snapshot(
    db: Session,
    user_id: str,
    client_items: list[dict],
) -> dict:
    """对账入口：client_items = [{entity_id, updated_at?, deleted?}]"""
    cloud = _cloud_entities(db, user_id)
    missing_on_cloud: list[dict] = []
    missing_on_client: list[dict] = []
    divergent: list[dict] = []

    for item in client_items:
        entity_id = item.get("entity_id")
        if not entity_id:
            continue
        client_ts = parse_ts(item.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)
        client_deleted = bool(item.get("deleted"))
        cloud_ent = cloud.get(entity_id)
        if cloud_ent is None:
            missing_on_cloud.append({"entity_id": entity_id, "updated_at": item.get("updated_at")})
        elif client_ts != cloud_ent["updated_at"] or client_deleted != cloud_ent["deleted"]:
            newer = "client" if lww_wins(cloud_ent["updated_at"], client_ts) else "cloud"
            divergent.append(
                {
                    "entity_id": entity_id,
                    "client_updated_at": item.get("updated_at"),
                    "cloud_updated_at": cloud_ent["updated_at"].isoformat(),
                    "newer": newer,
                    "action": "push" if newer == "client" else "pull",
                }
            )

    for entity_id, ent in cloud.items():
        if not any(item.get("entity_id") == entity_id for item in client_items):
            missing_on_client.append(
                {"entity_id": entity_id, "updated_at": ent["updated_at"].isoformat(), "deleted": ent["deleted"]}
            )

    return {
        "missing_on_cloud": missing_on_cloud,
        "missing_on_client": missing_on_client,
        "divergent": divergent,
        "summary": {
            "cloud_entities": len(cloud),
            "client_entities": len(client_items),
            "need_push": len(missing_on_cloud) + sum(1 for d in divergent if d["newer"] == "client"),
            "need_pull": len(missing_on_client) + sum(1 for d in divergent if d["newer"] == "cloud"),
        },
    }

