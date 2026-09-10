"""C 类悬账收口：event_edit_log.id 序列漂移 guard 迁移测试（c8d9e0f1a2b3）

背景（诊断结论，详见迁移文件头注）：
- schema.sql L192 `id bigserial PRIMARY KEY` 建库源正确；漂移库（本测试库 2026-09-08
  实锤）id 列无 default 无序列 → merge/split/confirm 一律 IntegrityError。
- guard 迁移幂等自愈：CREATE SEQUENCE IF NOT EXISTS + OWNED BY + SET DEFAULT nextval
  + setval 对齐 max(id)——任何路径建库后 `alembic upgrade head` 恢复 serial 语义。

覆盖：
- alembic upgrade head 幂等执行（本库已在 b2a3c4d5e6f7 → 应用 c8d9e0f1a2b3）
- information_schema 断言 id 默认值含 nextval('event_edit_log_id_seq')
- 序列归属正确（pg_depend：serial 语义，OWNED BY 生效）
- 序列水位不落后 max(id)（防撞主键）
- 模拟漂移自愈：拆掉 default → 重跑 upgrade 前直调 guard 语义 SQL → default 恢复
  （只动 default，不动数据；本机库 2026-09-08 手工修复路径的自动化复现）

前置：本地 PostgreSQL yishu 库（照 test_ba1_fields 风格，integration marker）
"""
import pytest

pytestmark = pytest.mark.integration


def _upgrade_head():
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


def _id_column_default(db) -> str | None:
    from sqlalchemy import text

    return db.scalar(
        text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'event_edit_log' AND column_name = 'id'"
        )
    )


def test_guard_migration_applied_and_id_has_nextval_default(db_user):
    """guard 迁移落库后：id 默认值 = nextval('event_edit_log_id_seq')（验收#4）"""
    db, _ = db_user
    _upgrade_head()
    default = _id_column_default(db)
    assert default is not None, "id 列必须有默认值（漂移未自愈）"
    assert "nextval" in default and "event_edit_log_id_seq" in default, (
        f"id 默认值应为序列 nextval: {default!r}"
    )
    # 序列归属（serial 语义）：pg_depend deptype='a'（auto/OWNED BY）
    from sqlalchemy import text

    owned = db.scalar(
        text(
            "SELECT count(*) FROM pg_depend d "
            "JOIN pg_class cs ON cs.oid = d.objid AND cs.relname = 'event_edit_log_id_seq' "
            "JOIN pg_class t ON t.oid = d.refobjid AND t.relname = 'event_edit_log' "
            "WHERE d.deptype = 'a'"
        )
    )
    assert owned and owned >= 1, "序列应 OWNED BY event_edit_log.id（表删级联删）"
    # 水位对齐：last_value >= max(id)（防 nextval 撞存量手工 id）
    row = db.execute(
        text(
            "SELECT (SELECT last_value FROM event_edit_log_id_seq), "
            "COALESCE((SELECT max(id) FROM event_edit_log), 0)"
        )
    ).one()
    assert row[0] >= row[1], f"序列水位 {row[0]} 不得落后 max(id) {row[1]}"


def test_guard_upgrade_idempotent_twice(db_user):
    """幂等：连续两次 upgrade head 无异常（已在 head 时 no-op；重复执行语义稳定）"""
    db, _ = db_user
    _upgrade_head()
    _upgrade_head()
    default = _id_column_default(db)
    assert default is not None and "nextval" in default, f"二次执行后默认值应仍在: {default!r}"


def test_guard_self_heals_drifted_column(db_user):
    """漂移自愈：手工拆掉 id 默认值（复现漂移态）→ 执行 guard 语义 SQL → 恢复

    不走 alembic（本库已在 head，upgrade 不会再跑已应用迁移），直接执行与
    c8d9e0f1a2b3.upgrade 相同的四条幂等语句——验证「任何漂移库 upgrade 后自愈」
    的语句级正确性。仅动列 default，不删数据（测试库直写主库，改动与修复前状态等价）。
    """
    from sqlalchemy import text

    db, user = db_user
    _upgrade_head()
    before = _id_column_default(db)
    assert before is not None and "nextval" in before
    try:
        # ① 打坏：剥掉 default（模拟「无序列默认」漂移态）
        db.execute(text("ALTER TABLE event_edit_log ALTER COLUMN id DROP DEFAULT"))
        db.commit()
        assert _id_column_default(db) is None, "打坏后应无默认值"
        # ② 自愈：guard 同款语句（IF NOT EXISTS + OWNED BY + SET DEFAULT + 条件 setval）
        db.execute(text("CREATE SEQUENCE IF NOT EXISTS event_edit_log_id_seq"))
        db.execute(
            text("ALTER SEQUENCE event_edit_log_id_seq OWNED BY event_edit_log.id")
        )
        db.execute(
            text(
                "ALTER TABLE event_edit_log "
                "ALTER COLUMN id SET DEFAULT nextval('event_edit_log_id_seq'::regclass)"
            )
        )
        db.execute(
            text(
                "SELECT setval('event_edit_log_id_seq', "
                "greatest((SELECT COALESCE(max(id), 0) FROM event_edit_log), 1), true) "
                "FROM event_edit_log_id_seq "
                "WHERE last_value < "
                "greatest((SELECT COALESCE(max(id), 0) FROM event_edit_log), 1)"
            )
        )
        db.commit()
        # ③ 断言恢复
        after = _id_column_default(db)
        assert after is not None and "nextval" in after and "event_edit_log_id_seq" in after, (
            f"自愈后默认值应恢复: {after!r}"
        )
        # ④ 插入验证：不显式给 id 也能自增（原事故 IntegrityError 的反证）
        ev_id = db.scalar(
            text(
                "INSERT INTO event_edit_log (event_id, user_id, action) "
                "SELECT e.id, e.user_id, 'guard_test' FROM events e "
                "WHERE e.user_id = :uid LIMIT 1 RETURNING id",
            ),
            {"uid": str(user.id)},
        )
        db.commit()
        if ev_id is not None:
            db.execute(
                text("DELETE FROM event_edit_log WHERE id = :i"), {"i": ev_id}
            )
            db.commit()
    finally:
        # 兜底：无论断言走到哪一步，恢复默认值（漂移库本不该长期无 default）
        default_now = _id_column_default(db)
        if default_now is None:
            db.execute(
                text(
                    "ALTER TABLE event_edit_log "
                    "ALTER COLUMN id SET DEFAULT nextval('event_edit_log_id_seq'::regclass)"
                )
            )
            db.commit()
