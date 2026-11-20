"""cron 入口的失败语义、隐私输出、预览与真实数据库幂等性。"""
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import Capsule, Content, Message
from app.workers import maintenance
from sqlalchemy import func, select


@pytest.mark.parametrize("job,stats", [
    ("requeue", {"requeued": 1, "failed": 1}),
    ("cleanup", {"cleanup": {"failed": 0}, "upload_reaper": {"failed": 1}}),
])
def test_partial_failure_returns_nonzero(monkeypatch, capsys, job, stats):
    monkeypatch.setattr(maintenance, "_execute", lambda args, dry_run: stats)
    assert maintenance.main([job]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed"
    assert result["stats"] == stats


def test_exception_does_not_leak_text(monkeypatch, capsys):
    def fail(args, dry_run):
        print("private-user-text")
        logging.critical("private-user-text")
        raise RuntimeError("private-user-text")

    old_disable = logging.root.manager.disable
    monkeypatch.setattr(maintenance, "_execute", fail)
    assert maintenance.main(["requeue"]) == 1
    output = capsys.readouterr()
    assert "private-user-text" not in output.out + output.err
    assert json.loads(output.out)["error_type"] == "RuntimeError"
    assert logging.root.manager.disable == old_disable


def test_rejects_non_counter_result(monkeypatch, capsys):
    monkeypatch.setattr(maintenance, "_execute", lambda args, dry_run: {"failed": "private-text"})
    assert maintenance.main(["requeue"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["stats"] == {}
    assert result["error_type"] == "ValueError"


def test_env_file_loaded_before_dispatch(monkeypatch, tmp_path, capsys):
    path = tmp_path / "cron.env"
    path.write_text("MAINTENANCE_TEST_VALUE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("MAINTENANCE_TEST_VALUE", "old-value")

    def execute(args, dry_run):
        import os

        assert os.environ["MAINTENANCE_TEST_VALUE"] == "from-file"
        return {"failed": 0}

    monkeypatch.setattr(maintenance, "_execute", execute)
    assert maintenance.main(["requeue", "--env-file", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_missing_env_file_does_not_dispatch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(maintenance, "_execute", lambda *a: pytest.fail("must not dispatch"))
    assert maintenance.main(["requeue", "--env-file", str(tmp_path / "missing")]) == 1
    assert json.loads(capsys.readouterr().out)["error_type"] == "FileNotFoundError"


@pytest.mark.parametrize("flags,expected", [([], True), (["--dry-run"], True), (["--execute-cleanup"], False)])
def test_cleanup_defaults_to_preview(monkeypatch, capsys, flags, expected):
    calls = []
    monkeypatch.setattr(maintenance, "_execute", lambda args, dry_run: calls.append(dry_run) or {"failed": 0})
    assert maintenance.main(["cleanup", *flags]) == 0
    assert calls == [expected]
    assert json.loads(capsys.readouterr().out)["dry_run"] is expected


@pytest.mark.integration
def test_review_preview_and_repeat(db_user, capsys):
    from app.services.notify import REVIEW_TZ

    db, user = db_user
    now = datetime.now(REVIEW_TZ)
    db.add(Content(user_id=user.id, content_type="text", source="app", status="done",
                   text="维护任务测试", taken_at=now))
    db.commit()
    args = ["daily-review", "--user", str(user.id), "--day", now.date().isoformat()]
    query = select(func.count()).select_from(Message).where(
        Message.user_id == user.id, Message.msg_type == "daily_review")
    assert maintenance.main([*args, "--dry-run"]) == 0
    assert db.scalar(query) == 0
    assert maintenance.main(args) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["stats"]["generated"] == 1
    assert maintenance.main(args) == 0
    assert json.loads(capsys.readouterr().out)["stats"]["skipped"] == 1
    assert db.scalar(query) == 1


@pytest.mark.integration
def test_capsule_preview_and_repeat(db_user, capsys, monkeypatch, request):
    from app.db.session import SessionLocal

    db, user = db_user
    content = Content(user_id=user.id, content_type="text", source="app", status="done")
    db.add(content)
    db.flush()
    capsule = Capsule(user_id=user.id, content_id=content.id, status="sealed",
                      open_at=datetime.now(UTC) - timedelta(days=1))
    db.add(capsule)
    db.commit()
    request.addfinalizer(lambda: (db.delete(capsule), db.commit()))
    # 隔离候选，不让测试入口扫描共享开发库的其它胶囊。
    from app.workers import capsule_scan
    from sqlalchemy import update

    uid = str(user.id)
    monkeypatch.setattr(capsule_scan, "update", lambda model: update(model).where(Capsule.user_id == uid))
    assert maintenance.main(["capsules", "--dry-run"]) == 0
    db.refresh(capsule)
    assert capsule.status == "sealed"
    assert maintenance.main(["capsules"]) == 0
    assert maintenance.main(["capsules"]) == 0
    db.refresh(capsule)
    assert capsule.status == "due"
    with SessionLocal() as other:
        assert other.scalar(select(func.count()).select_from(Message).where(
            Message.user_id == user.id, Message.msg_type == "capsule_due")) == 1
    capsys.readouterr()
