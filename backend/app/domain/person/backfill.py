"""B5-03 content_persons 关系回填（2026-11-24 收口）。

边界（严格）：
  - 只关联**用户已确认存在**的 Person（create_person 的 user_confirmed/imported），
    绝不因为文本匹配而新建 Person——那才是 B5-03 禁止的 AI 推断。
  - 匹配是确定性子串（display_name 出现在 content.text），不是语义推断；
    epistemic 记 user_confirmed（人名用户给的），source 记 backfill_name。
  - 复合主键 (content_id, person_id) 天然幂等；已关联的不重复写。
  - 只处理 alive 内容；软删内容跳过。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Content
from app.db.models.person import ContentPerson, Person
from app.services.query_helpers import alive_clause


def backfill_person_contents(
    db: Session, user_id: str, person: Person, *, limit: int = 500
) -> int:
    """把用户内容中显式提到该人名字的记录关联到 content_persons。

    返回新增关联行数（幂等：重复调用返回 0）。失败不吞异常，由调用方决定降级。
    """
    name = (person.display_name or "").strip()
    # 单字名/两字常见名会子串误命中大量无关内容（审判 P0：宁可少关联不可错关联）
    if len(name) < 2 or limit <= 0:
        return 0
    rows = db.execute(
        select(Content.id)
        .where(
            Content.user_id == user_id,
            alive_clause(Content),
            Content.text.is_not(None),
            Content.text.contains(name),  # 确定性子串匹配，参数化绑定
        )
        .limit(limit)
    ).scalars().all()
    if not rows:
        return 0
    added = 0
    for cid in rows:
        stmt = (
            insert(ContentPerson)
            .values(
                content_id=cid,
                person_id=person.id,
                epistemic="user_confirmed",
                source="backfill_name",
            )
            .on_conflict_do_nothing(index_elements=["content_id", "person_id"])
        )
        added += db.execute(stmt).rowcount or 0
    return added


__all__ = ["backfill_person_contents"]
