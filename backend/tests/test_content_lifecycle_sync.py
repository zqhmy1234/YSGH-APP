"""内容生命周期 · 同步写入**单一路径**测试（功能修复波 簇② · D08-1/2/3/5 · 2026-09-25）

缺陷原文：`.trae/specs/refactor-extensibility-maintainability-wave/audit/domain-08-sync-offline.md`

覆盖（每条都按"关掉修复即失败"设计，见 spec 的反向探针记录）：
  - **D08-1**：离线删（sync `delete`）→ 投影 `contents.deleted_at` ⇒ REST 读立即消失
    （原实现只写 SFV 墓碑 ⇒ 列表/详情仍视为存活，"先删了还在"）
  - **D08-1 深层**：**从未写 SFV 行**的内容（照片经 REST/分片链路上云）离线删
    不再被误判 "entity 不存在" 而静默丢弃
  - **D08-1 可恢复**：离线删后出现在回收站（按 `deleted_at` 过滤）⇒ 可 restore
  - **D08-2**：REST 删 → 写 `deleted_logs` ⇒ 30 天后 `run_cleanup` **真的**彻底清除
    （原实现不写日志 ⇒ 清理任务永不选中）
  - **D08-3**：离线改 `remark` → 投影回 `contents.remark`；白名单外字段 → `rejected`
  - **D08-5**：LWW 冲突 → **不写**变更日志（原实现把败方旧值下发他端 replay）
  - **双向写穿**：REST `PATCH` → 落 SFV（LWW 基准）+ 变更日志
  - **恢复反向动作**：restore → 清墓碑 + 审计日志 `restored` ⇒ 30 天后**不**被误清除

前置：PG yishu 库（fake 存储由 conftest autouse 强制）；不依赖 Redis。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.api.deps import load_alive_content
from app.core.errors import ApiError
from app.db.models import Content, DeletedLog, OfflineQueue, SyncFieldVersion
from app.services.sync import push_ops
from app.workers.cleanup_job import run_cleanup
from sqlalchemy import select, text

pytestmark = pytest.mark.integration

DEVICE = "lifecycle-device"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_content(
    db, user, *, text_value: str = "内容", content_type: str = "text", **kw
) -> Content:
    """直建内容权威行（不走 POST /contents —— 那条链会入队 RQ，依赖 Redis）"""
    c = Content(
        user_id=user.id,
        content_type=content_type,
        text=text_value,
        source="app",
        status="done",
        **kw,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _fetch(db, cid: str) -> Content | None:
    """绕过身份图直查权威行（清理任务在**另一 Session** 里删行，需真读库）"""
    return db.execute(select(Content).where(Content.id == cid)).scalar_one_or_none()


def _op(
    op_type: str,
    entity_id: str,
    *,
    field: str | None = None,
    value=None,
    updated_at: datetime | None = None,
    entity_type: str = "content",
) -> dict:
    return {
        "op_id": f"t-{uuid.uuid4().hex[:12]}",
        "op_type": op_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "field": field,
        "value": value,
        "updated_at": (updated_at or _now()).isoformat(),
    }


@pytest.fixture()
def as_user():
    """把 `get_current_user` 覆写为给定用户（REST 端点测试用；用后必清 override）"""
    from app.api import deps
    from app.main import app

    def _factory(user):
        app.dependency_overrides[deps.get_current_user] = lambda: user
        return app

    yield _factory
    app.dependency_overrides.clear()


# ═══════════════════════ D08-1：离线删必须投影权威表 ═══════════════════════


def test_offline_delete_projects_deleted_at(db_user):
    """D08-1：sync delete → contents.deleted_at 置位 ⇒ load_alive_content 404"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    r = push_ops(db, user.id, DEVICE, [_op("delete", cid)])

    assert r["rejected"] == [], r
    assert r["applied"] and r["applied"][0]["deleted"] is True, r
    row = db.get(Content, cid)
    db.refresh(row)
    assert row.deleted_at is not None, "离线删必须置权威表 deleted_at（否则 REST 读仍存活）"
    assert str(row.deleted_by) == str(user.id)
    # 墓碑仍在（同步各端）
    tomb = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == cid, SyncFieldVersion.field == "*"
        )
    ).scalar_one()
    assert tomb.deleted is True
    # REST 读立即消失（IDOR 统一 404）
    with pytest.raises(ApiError):
        load_alive_content(db, user.id, cid)


def test_offline_delete_of_never_synced_content_not_rejected(db_user):
    """D08-1 深层：内容无任何 SFV 行（从未字段同步）→ 离线删仍应成功

    照片经 REST/分片链路上云时不写 SFV 行；原实现只认 SFV ⇒ 误判 "entity 不存在"
    而静默丢弃用户删除。
    """
    db, user = db_user
    c = _make_content(db, user, content_type="photo")
    cid = str(c.id)
    assert db.execute(
        select(SyncFieldVersion).where(SyncFieldVersion.entity_id == cid)
    ).scalars().all() == []

    r = push_ops(db, user.id, DEVICE, [_op("delete", cid)])

    assert r["rejected"] == [], r
    assert r["applied"] and r["applied"][0]["deleted"] is True, r
    db.refresh(db.get(Content, cid))
    assert db.get(Content, cid).deleted_at is not None


def test_offline_delete_appears_in_trash_and_can_restore(db_user, as_user):
    """D08-1 可恢复：离线删后进入回收站（原实现回收站看不到 ⇒ 无法恢复）"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)
    push_ops(db, user.id, DEVICE, [_op("delete", cid)])

    from fastapi.testclient import TestClient

    cli = TestClient(as_user(user))
    r = cli.get("/api/v1/trash")
    assert r.status_code == 200, r.text
    ids = [it["id"] for it in r.json()["data"]]
    assert cid in ids, f"离线删除的内容必须出现在回收站（可恢复）: {ids}"

    rr = cli.post(f"/api/v1/trash/{cid}/restore")
    assert rr.status_code == 200, rr.text
    db.expire_all()
    assert db.get(Content, cid).deleted_at is None


# ═══════════════════════ D08-2：REST 删必须被 30 天清理选中 ═══════════════════════


def test_rest_delete_writes_deleted_log(db_user, as_user):
    """D08-2：REST DELETE → 写 deleted_logs（原实现只置 deleted_at ⇒ 永不清理）"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    from fastapi.testclient import TestClient

    cli = TestClient(as_user(user))
    r = cli.delete(f"/api/v1/contents/{cid}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["deleted"] is True

    db.expire_all()
    logs = db.execute(
        select(DeletedLog).where(DeletedLog.content_id == cid)
    ).scalars().all()
    assert len(logs) == 1, f"REST 删必须写审计日志（否则清理任务永不选中）: {logs}"
    assert logs[0].cleanup_status == "pending"
    # 墓碑 + 变更日志同样就位（单一软删写路径的四件事）
    assert db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == cid, SyncFieldVersion.field == "*"
        )
    ).scalar_one().deleted is True
    assert db.execute(
        select(OfflineQueue).where(OfflineQueue.user_id == user.id)
    ).scalars().all(), "REST 删应写变更日志供他端 pull"


def test_rest_delete_is_purged_after_retention(db_user, as_user):
    """D08-2 端到端：REST 删 + 审计日志超 30 天 → run_cleanup 真的物理清除"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    from fastapi.testclient import TestClient

    cli = TestClient(as_user(user))
    assert cli.delete(f"/api/v1/contents/{cid}").status_code == 200

    # 把审计日志做老到超出保留期
    db.expire_all()
    db.execute(
        text(
            "UPDATE deleted_logs SET deleted_at = now() - make_interval(days => 40) "
            "WHERE content_id = :c"
        ),
        {"c": cid},
    )
    db.commit()

    report = run_cleanup(older_than_days=30)
    assert report["failed"] == 0, report
    db.expire_all()
    assert _fetch(db, cid) is None, "超保留期后必须被彻底清除（原实现永不清理）"


# ═══════════════════════ D08-3：离线字段写入必须投影权威表 ═══════════════════════


def test_offline_remark_projects_to_authoritative(db_user):
    """D08-3：离线改备注 → 投影回 contents.remark（原实现只写 SFV，用户看不到）"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)
    assert db.get(Content, cid).remark is None

    r = push_ops(db, user.id, DEVICE, [
        _op("upsert_field", cid, field="remark", value="离线写的备注")
    ])

    assert r["rejected"] == [], r
    assert len(r["applied"]) == 1, r
    db.refresh(db.get(Content, cid))
    assert db.get(Content, cid).remark == "离线写的备注", "离线备注必须投影到权威表"
    # 账本同步（LWW 基准）
    sfv = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == cid, SyncFieldVersion.field == "remark"
        )
    ).scalar_one()
    assert sfv.value == "离线写的备注"


def test_non_whitelisted_field_on_authoritative_row_rejected(db_user):
    """D08-3 白名单：content 有权威行时，白名单外字段显式 rejected（不静默记账）"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    r = push_ops(db, user.id, DEVICE, [
        _op("upsert_field", cid, field="place", value="不该被接收")
    ])

    assert r["applied"] == [], r
    assert len(r["rejected"]) == 1, r
    assert "字段不可同步" in r["rejected"][0]["reason"], r
    # 不伪造成功：SFV 未写、权威表未改
    assert db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == cid, SyncFieldVersion.field == "place"
        )
    ).scalars().all() == []
    db.refresh(db.get(Content, cid))
    assert db.get(Content, cid).place is None


def test_whitelist_not_applied_to_entities_without_authoritative_row(db_user):
    """白名单只对**存在权威行**的 content 收窄；无权威行沿用通用账本（防误伤扩展面）"""
    db, user = db_user
    synthetic = str(uuid.uuid4())
    r = push_ops(db, user.id, DEVICE, [
        _op("upsert_field", synthetic, field="title", value="通用账本")
    ])
    assert len(r["applied"]) == 1, r
    assert r["rejected"] == [], r


def test_whitelist_value_type_guard(db_user):
    """白名单字段值类型校验：把 list 绑进 Text 列前显式 rejected（防 DB 层 500）"""
    db, user = db_user
    c = _make_content(db, user)
    r = push_ops(db, user.id, DEVICE, [
        _op("upsert_field", str(c.id), field="remark", value=["不是字符串"])
    ])
    assert r["applied"] == [], r
    assert len(r["rejected"]) == 1 and "类型不符" in r["rejected"][0]["reason"], r


# ═══════════════════════ D08-5：冲突不污染变更日志 ═══════════════════════


def test_lww_conflict_does_not_write_change_log(db_user):
    """D08-5：LWW 冲突（败方）**不写**变更日志；日志只含云端权威值"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    r1 = push_ops(db, user.id, DEVICE, [
        _op("upsert_field", cid, field="remark", value="云端新值")
    ])
    assert len(r1["applied"]) == 1, r1
    r2 = push_ops(db, user.id, DEVICE, [
        _op(
            "upsert_field", cid, field="remark", value="离线旧值",
            updated_at=_now() - timedelta(hours=2),
        )
    ])
    assert len(r2["conflicts"]) == 1, r2
    assert r2["applied"] == [], r2

    logs = db.execute(
        select(OfflineQueue).where(OfflineQueue.user_id == user.id)
    ).scalars().all()
    assert len(logs) == 1, f"冲突不得写变更日志（否则败方旧值下发他端）: {len(logs)}"
    assert logs[0].payload["value"] == "云端新值"
    # 权威表保持云端值（未被旧值覆盖）
    db.refresh(db.get(Content, cid))
    assert db.get(Content, cid).remark == "云端新值"


# ═══════════════════════ 双向写穿：REST 写入也落账本 ═══════════════════════


def test_rest_patch_writes_field_version_and_change_log(db_user, as_user):
    """双向写穿：REST PATCH remark → 落 SFV（LWW 基准）+ 变更日志"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    from fastapi.testclient import TestClient

    cli = TestClient(as_user(user))
    r = cli.patch(f"/api/v1/contents/{cid}", json={"remark": "在线写的备注"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["remark"] == "在线写的备注"

    db.expire_all()
    sfv = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == cid, SyncFieldVersion.field == "remark"
        )
    ).scalar_one()
    assert sfv.value == "在线写的备注", "REST 权威写入必须落 SFV 作为离线编辑的 LWW 基准"
    logs = db.execute(
        select(OfflineQueue).where(
            OfflineQueue.user_id == user.id, OfflineQueue.op_type == "upsert_field"
        )
    ).scalars().all()
    assert len(logs) == 1, f"REST 改备注应写一条变更日志: {logs}"
    assert logs[0].payload["field"] == "remark"

    # LWW 基准生效：稍旧的离线编辑不得覆盖这次在线值
    r2 = push_ops(db, user.id, DEVICE, [
        _op(
            "upsert_field", cid, field="remark", value="更晚到达的旧编辑",
            updated_at=_now() - timedelta(hours=1),
        )
    ])
    assert len(r2["conflicts"]) == 1, r2
    db.refresh(db.get(Content, cid))
    assert db.get(Content, cid).remark == "在线写的备注"


# ═══════════════════════ 恢复：反向动作缺一不可 ═══════════════════════


def test_restore_clears_tombstone_and_prevents_purge(db_user, as_user):
    """恢复：清墓碑 + 审计日志 restored ⇒ 30 天后**不**被误彻底清除"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    from fastapi.testclient import TestClient

    cli = TestClient(as_user(user))
    assert cli.delete(f"/api/v1/contents/{cid}").status_code == 200
    assert cli.post(f"/api/v1/trash/{cid}/restore").status_code == 200

    db.expire_all()
    assert db.get(Content, cid).deleted_at is None
    # ② 墓碑已清（否则同步域仍判已删）
    assert db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == cid, SyncFieldVersion.field == "*"
        )
    ).scalars().all() == []
    # ③ 审计日志中和（否则 30 天后误删已恢复的内容）
    logs = db.execute(select(DeletedLog).where(DeletedLog.content_id == cid)).scalars().all()
    assert [lg.cleanup_status for lg in logs] == ["restored"], logs

    # 即便把它做老到超期，也不得被清理
    db.execute(
        text(
            "UPDATE deleted_logs SET deleted_at = now() - make_interval(days => 40) "
            "WHERE content_id = :c"
        ),
        {"c": cid},
    )
    db.commit()
    run_cleanup(older_than_days=30)
    db.expire_all()
    assert _fetch(db, cid) is not None, "已恢复的内容不得被 30 天清理误删"


def test_trash_clear_cleans_ledger(db_user, as_user):
    """硬删（清空回收站）后清尾：内容行/墓碑/pending 审计日志都不留悬空"""
    db, user = db_user
    c = _make_content(db, user)
    cid = str(c.id)

    from fastapi.testclient import TestClient

    cli = TestClient(as_user(user))
    assert cli.delete(f"/api/v1/contents/{cid}").status_code == 200
    r = cli.request("DELETE", "/api/v1/trash")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert _fetch(db, cid) is None
    assert db.execute(
        select(SyncFieldVersion).where(SyncFieldVersion.entity_id == cid)
    ).scalars().all() == []
    logs = db.execute(select(DeletedLog).where(DeletedLog.content_id == cid)).scalars().all()
    assert all(lg.cleanup_status != "pending" for lg in logs), logs
