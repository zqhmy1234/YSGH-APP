"""B6 安全审计（关键动作留痕，用户可查自己的记录）。

设计（用户拍板 2026-11-24）：
  - 只记关键动作：授权变更 / 保险箱锁定解锁 / 导出 / 注销与恢复
  - 不记每次检索、每次生成（那是全量行为日志，量大成本高）
  - 用户可查自己的记录（"我的数据活动"）
  - 只增不改不删；账号注销时随用户清除（purge 链已纳入 audit_log）

不存敏感原文：target 是 scope 名或对象 id，detail 只放非敏感元数据。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog

# 审计动作白名单（单一真源）
AUDIT_ACTIONS = frozenset({
    "consent_grant", "consent_revoke",
    "vault_lock", "vault_unlock",
    "export_requested", "export_zip",
    "account_purge_requested", "account_restored",
    "memory_written",  # B5 合并：Agent 对话记忆写入 canonical 账本
})


def record_audit(
    db: Session,
    user_id: str,
    *,
    action: str,
    target: str | None = None,
    detail: dict | None = None,
    actor_id: str | None = None,
) -> AuditLog:
    """写一条审计。action 必须在白名单内——防拼错、防随意扩张记录面。"""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"未知审计动作：{action!r}")
    row = AuditLog(
        user_id=user_id,
        action=action,
        target=target,
        detail={**(detail or {}), **({"actor_id": actor_id} if actor_id and actor_id != user_id else {})},
    )
    db.add(row)
    db.flush()
    return row


def list_audit(
    db: Session, user_id: str, *, limit: int = 50, action: str | None = None
) -> list[dict]:
    """用户查自己的数据活动（倒序、有上限；不暴露他人记录）。"""
    q = select(AuditLog).where(AuditLog.user_id == user_id)
    if action:
        if action not in AUDIT_ACTIONS:
            raise ValueError(f"未知审计动作：{action!r}")
        q = q.where(AuditLog.action == action)
    q = q.order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 200))
    return [
        {
            "id": r.id,
            "action": r.action,
            "target": r.target,
            "detail": r.detail or {},
            "created_at": r.created_at,
        }
        for r in db.execute(q).scalars().all()
    ]
