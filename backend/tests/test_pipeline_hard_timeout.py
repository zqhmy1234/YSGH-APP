"""process_content 应用层硬超时守护（2026-11-01 · 走查 P0）

独立成文件的原因：tests/test_pipeline.py 全文件连跑存在**预存** access violation
（TestR2StagedCommit → TestP0FailureWriteback 组合，baseline 复现、与超时守护无关），
放在同文件会被前面的崩溃挡住跑不到。本文件自带崩溃链解耦的隔离执行环境。

Windows SimpleWorker 上 RQ job_timeout 不生效（signal.alarm 仅 Unix），单个卡死
job 会永久堵死单线程 worker。守护把实现体丢进线程池，到点置 failed+retryable
并 raise PipelineHardTimeout（RQ 照常重投），泄漏线程 daemon 化不阻塞消费循环。
⚠️ 泄漏线程持有 sleep 时不能与后续 DB 操作并发（libpq 对象跨线程访问会
access violation）——测试尾部的 sleep 是给泄漏线程的退出缓冲，勿删。
"""
import pytest


class TestHardTimeoutGuard:
    """process_content 应用层硬超时（2026-11-01 · 走查 P0）

    Windows SimpleWorker 上 RQ job_timeout 不生效（signal.alarm 仅 Unix），
    单个卡死 job 会永久堵死单线程 worker。守护把实现体丢进线程池，
    到点置 failed+retryable 并 raise PipelineHardTimeout（RQ 照常重投）。
    """

    def test_timeout_returns_failed_and_raises(self, monkeypatch):
        """实现体挂死 → 到点置 failed + PIPELINE_HARD_TIMEOUT + 抛硬超时"""
        import time as _time

        from app.core.config import settings
        from app.services.pipeline import PipelineHardTimeout, process_content

        def hang_forever(content_id):
            _time.sleep(3)

        monkeypatch.setattr(settings, "pipeline_hard_timeout_s", 1)
        with pytest.raises(PipelineHardTimeout):
            process_content("00000000-0000-0000-0000-000000000000", _inner=hang_forever)
        # 泄漏的守护线程还剩 ~2s sleep；等它退出再做后续 DB 操作（libpq 对象跨线程
        # 访问会 access violation，见 lessons 2026-11-01）
        _time.sleep(3)

    def test_timeout_marks_content_failed(self, db_user, monkeypatch):
        """真实 content 挂死 → contents.status=failed + extra.error.code=PIPELINE_HARD_TIMEOUT"""
        import time as _time

        from app.core.config import settings
        from app.services.pipeline import PipelineHardTimeout, process_content

        db, user = db_user
        from app.db.models import Content

        c = Content(user_id=user.id, content_type="text", text="超时实验", status="done", source="app")
        db.add(c)
        db.commit()

        def hang_forever(content_id):
            _time.sleep(3)

        monkeypatch.setattr(settings, "pipeline_hard_timeout_s", 1)
        with pytest.raises(PipelineHardTimeout):
            process_content(str(c.id), _inner=hang_forever)
        db.expire_all()
        db.refresh(c)
        assert c.status == "failed"
        assert (c.extra or {}).get("error", {}).get("code") == "PIPELINE_HARD_TIMEOUT"
        # 同上：等泄漏守护线程退出，避免 teardown 的级联删除跨线程打 libpq
        _time.sleep(3)

    def test_normal_path_unaffected(self, db_user, monkeypatch):
        """正常实现体经守护路径结果不变（守护不影响成功流程）"""
        from app.services.pipeline import process_content

        db, user = db_user
        from app.db.models import Content

        c = Content(user_id=user.id, content_type="text", text="守护不误伤", status="done", source="app")
        db.add(c)
        db.commit()

        monkeypatch.setattr("app.services.pipeline._get_classifier", lambda: (lambda t: "note"))
        monkeypatch.setattr("app.services.pipeline._index_content", lambda *a, **k: None)
        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.expire_all()
        db.refresh(c)
        assert c.status == "done"
