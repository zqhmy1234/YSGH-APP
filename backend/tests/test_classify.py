"""classify 分类 job 归属/脱敏专项测试（H3 · 由 test_security_p3.py 按域拆分而来）

覆盖（原 M3）：
  - enqueue 时 job.meta 写入 user_id（归属校验的数据来源）
  - 用户 B 查询用户 A 的 classify job → 403 CLASSIFY_003
  - owner 查询自己 job 不误伤
  - 失败仅回传脱敏错误（不直出 exc_info，防内部路径/堆栈泄漏）
  - corrections 三层裁决 job 越权 → 403 CORR_004
"""


class _FakeJob:
    """轻量 RQ Job 桩（避免依赖 Redis 真实 job）"""

    def __init__(self, meta, status="queued", result=None, exc_info=None):
        self.meta = meta
        self._status = status
        self._result = result
        self.exc_info = exc_info

    def get_status(self):
        return self._status

    def return_value(self):
        return self._result


def test_classify_enqueue_writes_user_id_meta(client, auth_headers, monkeypatch):
    """enqueue 时 job.meta 写入 user_id（归属校验的数据来源）"""
    user_id, headers = auth_headers("p3-job-meta")
    captured: dict = {}

    class _FakeOut:
        id = "job-meta-1"

    def fake_enqueue(func, *args, **kwargs):
        captured["meta"] = kwargs.get("meta")
        return _FakeOut()

    monkeypatch.setattr("app.api.classify.enqueue_high", fake_enqueue)
    r = client.post("/api/v1/classify", json={"text": "明天记得买牛奶"}, headers=headers)
    assert r.status_code == 200, r.text
    assert captured["meta"] == {"user_id": user_id}


def test_classify_job_cross_user_403(client, auth_headers, monkeypatch):
    """用户 B 查询用户 A 的 job → 403 CLASSIFY_003（越权轮询他人分类结果）"""
    user_a, _ = auth_headers("p3-job-a")
    _, headers_b = auth_headers("p3-job-b")

    monkeypatch.setattr(
        "app.api.classify.get_job", lambda job_id: _FakeJob({"user_id": user_a})
    )
    r = client.get("/api/v1/classify/jobs/secret-job-id", headers=headers_b)
    assert r.status_code == 403
    assert r.json()["code"] == "CLASSIFY_003"


def test_classify_job_owner_can_query(client, auth_headers, monkeypatch):
    """归属一致（owner 查询自己 job）不误伤"""
    user_id, headers = auth_headers("p3-job-own")
    monkeypatch.setattr(
        "app.api.classify.get_job",
        lambda job_id: _FakeJob(
            {"user_id": user_id},
            status="finished",
            result={"label": "todo", "label_cn": "待办", "confidence": 0.9, "scores": []},
        ),
    )
    r = client.get("/api/v1/classify/jobs/own-job-id", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "finished"


def test_classify_job_failure_error_sanitized(client, auth_headers, monkeypatch):
    """失败仅回传脱敏错误（不再直出 exc_info 末行，防内部路径/堆栈泄漏）"""
    user_id, headers = auth_headers("p3-job-fail")
    raw_exc = (
        "Traceback (most recent call last):\n"
        '  File "/srv/app/backend/app/services/secret_path.py", line 42, in foo\n'
        "ValueError: boom"
    )
    monkeypatch.setattr(
        "app.api.classify.get_job",
        lambda job_id: _FakeJob({"user_id": user_id}, status="failed", exc_info=raw_exc),
    )
    r = client.get("/api/v1/classify/jobs/fail-job-id", headers=headers)
    assert r.status_code == 200
    err = r.json()["data"]["error"]
    assert err == "任务执行失败，请稍后重试"
    assert "secret_path" not in err and "Traceback" not in err


def test_arbitrate_job_cross_user_403(client, auth_headers, monkeypatch):
    """corrections 三层裁决 job 越权同样 403 CORR_004"""
    user_a, _ = auth_headers("p3-arb-a")
    _, headers_b = auth_headers("p3-arb-b")

    # corrections.arbitrate_job_status 在函数内 `from app.core.queue import get_job`，
    # 需 monkeypatch app.core.queue.get_job
    monkeypatch.setattr(
        "app.core.queue.get_job", lambda job_id: _FakeJob({"user_id": user_a})
    )
    r = client.get("/api/v1/classify/arbitrate/jobs/arb-secret", headers=headers_b)
    assert r.status_code == 403
    assert r.json()["code"] == "CORR_004"
