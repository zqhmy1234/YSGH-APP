"""B6 保险箱（VaultPort 实现）。

设计（用户拍板 2026-11-24）：
  - 内容级锁定：一条条锁，不改 contents 原行（覆盖层，符合"只加层不改原文"铁律）
  - 不加密原文本体：原文本体已在 PG/Qdrant 多处索引，单独加密会造成检索口径分裂；
    锁的是**访问入口**——检索/回响/导出/共享四处必须过滤 locked_content_ids()
  - 谁能解锁：本人持服务端钥匙；家庭成员解锁依赖 B8（granted_roles 字段预留，本期恒空）
  - 服务端双钥匙：沿用 profile_sensitive_crypto 的 validate_profile_field_keys()，
    不新建密钥体系（用户拍板"不碰 B5 加密底座"）
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, VaultEntry
from app.services.audit import record_audit
from app.services.profile_sensitive_crypto import validate_profile_field_keys

# 解锁令牌：HKDF 从服务端 HMAC key 派生**专用子密钥**（不与画像盲索引共用原始字节），
# 带内容 id + 签发时间，短时效（5 分钟）、单次语义（解锁后条目删除即失效）。
# 格式：v1.<issued_at>.<hex>，校验时恒定时间比较 + 时间窗判定。
_UNLOCK_INFO = b"b6.vault.unlock:v1"
_UNLOCK_TTL_S = 300


def _unlock_key() -> bytes:
    _, hmac_key = validate_profile_field_keys()
    return hmac.new(hmac_key, _UNLOCK_INFO, hashlib.sha256).digest()


def _sign(user_id: str, content_id: str, issued_at: int, generation: int) -> str:
    # generation = vault_entry.id：每次重新上锁都会生成新行 ⇒ 旧令牌天然失效
    msg = f"{user_id}:{content_id}:{issued_at}:{generation}".encode()
    return hmac.new(_unlock_key(), msg, hashlib.sha256).hexdigest()


def _unlock_token(user_id: str, content_id: str, generation: int) -> str:
    issued = int(time.time())
    return f"v1.{generation}.{issued}.{_sign(user_id, content_id, issued, generation)}"


def _verify_token(user_id: str, content_id: str, token: str, generation: int) -> bool:
    try:
        version, gen_s, issued_s, signature = token.split(".", 3)
        if version != "v1" or int(gen_s) != generation:
            return False
        issued = int(issued_s)
        if abs(int(time.time()) - issued) > _UNLOCK_TTL_S:
            return False
        return hmac.compare_digest(signature, _sign(user_id, content_id, issued, generation))
    except (ValueError, AttributeError):
        return False


def _current_entry(db: Session, user_id: str, content_id: str) -> VaultEntry | None:
    return db.execute(
        select(VaultEntry).where(
            VaultEntry.user_id == user_id,
            VaultEntry.content_id == content_id,
            VaultEntry.unlocked_at.is_(None),
        )
    ).scalar_one_or_none()


def _require_alive_content(db: Session, user_id: str, content_id: str) -> Content:
    from app.api.deps import load_alive_content

    return load_alive_content(db, user_id, content_id)


def lock_content(
    db: Session, user_id: str, content_id: str, *, note: str | None = None,
    actor_id: str | None = None,
) -> dict:
    """锁一条内容（幂等）：已锁则原样返回。"""
    if note is not None and len(note) > 200:
        raise ValueError("note 不得超过 200 字符（与 API 契约一致）")
    _require_alive_content(db, user_id, content_id)
    row = _current_entry(db, user_id, content_id)
    if row is None:
        from sqlalchemy.exc import IntegrityError
        row = VaultEntry(user_id=user_id, content_id=content_id, note=note)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # 并发双锁：回读既有行，不把 500 抛给调用方（幂等承诺）
            db.rollback()
            row = _current_entry(db, user_id, content_id)
            if row is None:  # 极端情况：对方已解锁，重试一次
                return lock_content(db, user_id, content_id, note=note, actor_id=actor_id)
            return _entry_out(row)
        try:
            record_audit(
                db, user_id, action="vault_lock", target=content_id,
                detail={"note": note} if note else {}, actor_id=actor_id or user_id,
            )
            db.commit()
        except Exception:  # noqa: BLE001 —— 审计失败必须整体回滚，不留无审计的锁
            db.rollback()
            raise
        db.refresh(row)
    return _entry_out(row)


def unlock_content(
    db: Session, user_id: str, content_id: str, key: str, *, actor_id: str | None = None
) -> dict[str, bool | str]:
    """解锁一条内容：先验服务端令牌，不通过 → wrong_key。"""
    row = _current_entry(db, user_id, content_id)
    if row is None:
        return {"ok": False, "reason": "not_locked"}
    if not _verify_token(user_id, content_id, key, int(row.id)):
        return {"ok": False, "reason": "wrong_key"}
    row.unlocked_at = datetime.now(UTC)
    try:
        record_audit(
            db, user_id, action="vault_unlock", target=content_id,
            actor_id=actor_id or user_id,
        )
        db.commit()
    except Exception:  # noqa: BLE001 —— 审计失败整体回滚，锁仍在
        db.rollback()
        raise
    return {"ok": True, "reason": "ok"}


def unlock_token_for(db: Session, user_id: str, content_id: str) -> str:
    """给客户端下发解锁令牌（只在本人在场时调用；令牌等同密码，不写日志）。"""
    _require_alive_content(db, user_id, content_id)
    entry = _current_entry(db, user_id, content_id)
    if entry is None:
        raise ValueError("内容未锁定，无解锁令牌")
    return _unlock_token(user_id, content_id, int(entry.id))


def locked_content_ids(db: Session, user_id: str) -> set[str]:
    """访问入口过滤的集中查询：返回当前仍锁定的 content_id 集合。

    已接入：B4 回响（services/echo.py）、数据导出（services/export.py）。
    未接入（待各域改造）：L3 检索、家庭共享（依赖 B8 落地）——不得宣称已全域过滤。
    解锁后条目删除，集合自动不再包含该内容。
    """
    rows = db.execute(
        select(VaultEntry.content_id).where(
            VaultEntry.user_id == user_id,
            VaultEntry.unlocked_at.is_(None),
        )
    ).scalars().all()
    return set(rows)


def list_vault(db: Session, user_id: str, *, limit: int = 100) -> list[dict]:
    """用户看自己锁了哪些内容（只给 id 和 note，不取原文）。"""
    q = (
        select(VaultEntry)
        .where(VaultEntry.user_id == user_id, VaultEntry.unlocked_at.is_(None))
        .order_by(VaultEntry.created_at.desc())
        .limit(min(max(limit, 1), 200))
    )
    return [_entry_out(r) for r in db.execute(q).scalars().all()]


def _entry_out(r: VaultEntry) -> dict:
    return {
        "content_id": r.content_id,
        "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "granted_roles": r.granted_roles or [],
    }
