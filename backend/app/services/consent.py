"""B6 用途授权（ConsentPort 实现）。

设计（用户拍板 2026-11-24）：
  - 按「用途」列 5 项，不做数据类别×用途矩阵
  - 默认全部未授权（无行 = 未表态 = not_granted）
  - 撤回不倒流：只影响后续使用，已生成内容保留；撤回保留行以便审计

边界（重要）：
  基础功能（把用户自己的记忆展示给用户本人）不是授权项——那是产品存在的理由。
  本模块管的是「额外用途」：模型训练、家庭共享、客服调阅、个性化推荐、研究分析。
  B4 回响的 enabled 开关是独立的展示频次控制，不归本模块；但回响若要跨角色展示
  （B8 落地后），须先过 family_share scope。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConsentGrant
from app.services.audit import record_audit

ConsentState = Literal["ok", "not_granted", "revoked"]

# 用途白名单（单一真源；新增用途只改这里 + 前端清单）
CONSENT_SCOPES: dict[str, str] = {
    "model_training": "用我的内容训练或微调模型",
    "family_share": "允许家庭成员查看我共享的内容",
    "support_access": "允许客服在工单中调阅我的记录",
    "personalization": "用我的内容做个性化推荐与画像",
    "research_analysis": "用于匿名统计分析或产品研究",
}
VALID_SCOPES = frozenset(CONSENT_SCOPES)


def _require_scope(scope: str) -> None:
    if scope not in VALID_SCOPES:
        raise ValueError(f"未知用途 scope：{scope!r}；合法值：{sorted(VALID_SCOPES)}")


def list_consents(db: Session, user_id: str) -> list[dict]:
    """用户视角的完整清单：5 项全列，未表态的也返回（granted=False）。"""
    rows = {
        r.scope: r
        for r in db.execute(
            select(ConsentGrant).where(ConsentGrant.user_id == user_id)
        ).scalars().all()
    }
    return [
        {
            "scope": scope,
            "label": label,
            "state": (rows[scope].state if scope in rows else "not_granted"),
            "granted": scope in rows and rows[scope].state == "granted",
            "updated_at": rows[scope].updated_at if scope in rows else None,
        }
        for scope, label in CONSENT_SCOPES.items()
    ]


def get_consent(db: Session, user_id: str, scope: str) -> ConsentState:
    """ConsentPort.get：查单个用途的授权状态。无行 = not_granted（默认不勾）。"""
    _require_scope(scope)
    row = db.execute(
        select(ConsentGrant).where(
            ConsentGrant.user_id == user_id, ConsentGrant.scope == scope
        )
    ).scalar_one_or_none()
    if row is None:
        return "not_granted"
    return "ok" if row.state == "granted" else "revoked"


def is_granted(db: Session, user_id: str, scope: str) -> bool:
    """消费方最常用的布尔判定——只认 ok，其余一律不放行。"""
    return get_consent(db, user_id, scope) == "ok"


def set_consent(
    db: Session, user_id: str, scope: str, granted: bool, *, actor_id: str | None = None
) -> dict:
    """ConsentPort.set：授予或撤回，写审计。撤回保留行（不倒流、可追溯）。"""
    _require_scope(scope)
    row = db.execute(
        select(ConsentGrant).where(
            ConsentGrant.user_id == user_id, ConsentGrant.scope == scope
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    before = row.state if row is not None else None
    target_state = "granted" if granted else "revoked"
    # 状态未变（含「从未表态且要撤回」）：不写行、不写审计，避免重复点击刷屏
    if before == target_state:
        return {
            "scope": scope,
            "label": CONSENT_SCOPES[scope],
            "state": before,
            "granted": before == "granted",
            "updated_at": row.updated_at,
        }
    if row is None:
        row = ConsentGrant(user_id=user_id, scope=scope)
        db.add(row)
    if granted:
        row.state = "granted"
        row.granted_at = now
        row.revoked_at = None
    else:
        # 撤回不倒流：只改状态，已生成内容不动
        row.state = "revoked"
        row.revoked_at = now
    try:
        db.flush()
        record_audit(
            db, user_id,
            action="consent_grant" if granted else "consent_revoke",
            target=scope,
            detail={"label": CONSENT_SCOPES[scope]},
            actor_id=actor_id or user_id,
        )
        db.commit()
    except Exception:  # noqa: BLE001 —— 审计失败必须整体回滚，不留无审计的授权变更
        db.rollback()
        raise
    db.refresh(row)
    return {
        "scope": scope,
        "label": CONSENT_SCOPES[scope],
        "state": row.state,
        "granted": row.state == "granted",
        "updated_at": row.updated_at,
    }
