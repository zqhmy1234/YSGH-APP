"""B6 端口适配器：L2 Protocol → 真实实现（2026-11-24）。

把已存在的实现包成端口，供上层按契约调用——不是新建能力：
  ConsentPort  → services/consent.py（本次新建）
  VaultPort    → services/vault.py（本次新建）
  ExportPort   → services/export.py（已有实现）
  LifecyclePort→ domain/memory/purge.py（已有实现）

LifecyclePort 按审判结论补了 restore 方法（原签名只有 request_delete/purge_status，
与已有的 DELETE /users/me + POST /me/restore 能力错配）。purge_status 从 User 行
实算，不伪造"排队中"——账号注销是软删 + 30 天窗口，没有异步 worker 队列。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.db.models import User
from app.domain.memory.purge import account_undo_until, request_account_purge, restore_account_purge
from app.ports.ports_extra import ExportPage, PortUnavailable
from app.services import consent as consent_svc
from app.services import vault as vault_svc
from app.services.export import build_export_payload


class DbConsentPort:
    """用途授权端口实现：get/set 走 consent 服务，scope 白名单由服务层校验。"""

    def get(self, user_id: str, scope: str) -> Literal["ok", "not_granted", "revoked"]:
        # 注意：consent.get_consent 需要 db；本端口方法签名无 db，须由构造期绑定 session。
        return self._consent.get_consent(self._db, user_id, scope)

    def set(self, user_id: str, scope: str, granted: bool) -> None:
        self._consent.set_consent(self._db, user_id, scope, granted)

    def __init__(self, db: Session):
        self._db = db
        self._consent = consent_svc


class DbVaultPort:
    """保险箱端口实现：lock/unlock 内容级锁定。

    L2 原签名 lock(user_id) 是整账号锁，与拍板的「一条条锁」不符——这里按
    内容 id 实现；不传 content_id 时抛 PortUnavailable（不假装账号级锁可用）。
    """

    def __init__(self, db: Session):
        self._db = db

    def lock(self, user_id: str, content_id: str | None = None) -> None:
        if content_id is None:
            raise PortUnavailable("账号级锁已改为内容级：必须传 content_id")
        vault_svc.lock_content(self._db, user_id, content_id)

    def unlock(
        self, user_id: str, key: str, content_id: str | None = None
    ) -> Literal["ok", "wrong_key", "unavailable"]:
        if content_id is None:
            return "unavailable"
        result = vault_svc.unlock_content(self._db, user_id, content_id, key)
        if result["ok"]:
            return "ok"
        return "wrong_key" if result["reason"] == "wrong_key" else "unavailable"


class DbExportPort:
    """导出端口实现：把 build_export_payload 的分页结果包成 ExportPage。"""

    def __init__(self, db: Session):
        self._db = db

    def page(self, user_id: str, *, cursor: str = "") -> ExportPage:
        payload = build_export_payload(self._db, user_id, cursor or None)
        return ExportPage(
            status="truncated" if payload.get("truncated") else "ok",
            cursor=payload.get("next_cursor") or "",
            items=(payload.get("contents") or (),),  # 单页负载，供上层消费
        )


class DbLifecyclePort:
    """生命周期端口实现：request_delete / restore / purge_status。"""

    def __init__(self, db: Session):
        self._db = db

    def request_delete(self, user_id: str) -> str:
        from app.services.audit import record_audit
        try:
            receipt = request_account_purge(self._db, user_id, actor_id=user_id)
            # 端口是独立入口，必须自带审计（HTTP 路径另行记，二者不会重复走同一调用链）
            record_audit(self._db, user_id, action="account_purge_requested", target="account")
            self._db.commit()  # purge 服务内部已 commit，审计在后须单独落库
        except Exception:  # noqa: BLE001
            self._db.rollback()
            raise
        return str(receipt.undo_until or "")

    def restore(self, user_id: str) -> bool:
        from app.services.audit import record_audit
        try:
            receipt = restore_account_purge(self._db, user_id)
            record_audit(self._db, user_id, action="account_restored", target="account")
            self._db.commit()
        except Exception:  # noqa: BLE001
            self._db.rollback()
            raise
        return receipt.status != "failed"

    def purge_status(self, user_id: str) -> Literal["scheduled", "purging", "done", "failed"]:
        """从 User.deleted_at 实算：软删 + 30 天窗口，无异步 worker。"""
        user = self._db.get(User, user_id)
        if user is None:
            return "failed"
        if user.deleted_at is None:
            return "done"
        until = account_undo_until(user.deleted_at)
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return "scheduled" if until > datetime.now(UTC) else "purging"


__all__ = ["DbConsentPort", "DbVaultPort", "DbExportPort", "DbLifecyclePort"]
