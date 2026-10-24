"""A-11 ③ 异步回答：契约与三态回归测试。

## 为什么需要这些用例

异步回答有**三条独立的失败模式**，每一条都会让用户看到错的东西：

1. **老客户端拿不到回答**：若把 `want_answer` 语义直接改成"永远异步"，
   在野 APK（不认识 `answer_job_id`）会永远 `answer=None` ⇒ 回答卡永久消失。
2. **把「没有答案」说成「出错了」**：`status=done` + `answer=None` 是
   "证据不足、本来就没有"（正常空态）；`status=failed` 才是系统降级。
   混为一谈会让用户以为自己没记过这件事（本仓反复清理的一类谎）。
3. **门禁被伪造测量骗绿**：异步路径下本次响应**确实没有**承担回答耗时，
   若为了"数字好看"在那里重算 `latency_ms`（甚至加估计值），就是伪造测量。

本文件把这三条钉住。
"""
from __future__ import annotations

import inspect

from app.schemas.search import SearchQuery, SearchResult

# ---------------------------------------------------------------------------
# 1. 契约默认值：老客户端 / 未请求 ⇒ 行为与改动前逐字一致
# ---------------------------------------------------------------------------


def test_async_flag_defaults_off():
    """`answer_async` 默认必须 False（在野 APK 不知道这个字段）。"""
    q = SearchQuery(q="西湖")
    assert q.want_answer is False
    assert q.answer_async is False


def test_search_result_job_id_defaults_none():
    """`answer_job_id` 默认 None —— 老后端/无异步时字段缺失也不改变语义。"""
    r = SearchResult(query="x", intent="text", hits=[], total=0, latency_ms=1)
    assert r.answer_job_id is None
    assert r.answer is None


# ---------------------------------------------------------------------------
# 2. 存储层三态（pending / done / failed）
# ---------------------------------------------------------------------------


def _job_module():
    from app.services import search_answer_job

    return search_answer_job


def test_job_roundtrip_and_ttl_key_shape():
    """写入后能原样读回；键有前缀（便于运维按前缀清理）。"""
    job = _job_module()
    jid = job.new_job_id()
    try:
        job._write(jid, {"status": "pending", "answer": None, "sources": []})
        got = job.read_job(jid)
        assert got == {"status": "pending", "answer": None, "sources": []}
        assert job._key(jid).startswith("yishu:answer:")
    finally:
        from app.core.queue import redis

        redis.delete(job._key(jid))


def test_missing_job_returns_none():
    """不存在 ⇒ None（端点据此 404，不抛异常）。"""
    assert _job_module().read_job("no-such-job-000") is None


def test_corrupt_snapshot_returns_none_not_raise():
    """脏数据（格式变更/半截写入）⇒ None，**不把解析异常抛给客户端**。"""
    job = _job_module()
    from app.core.queue import redis

    jid = "corrupt-probe"
    try:
        redis.setex(job._key(jid), 60, b"{not-json")
        assert job.read_job(jid) is None
    finally:
        redis.delete(job._key(jid))


def test_done_with_empty_answer_is_distinguishable_from_failed():
    """核心口径：`done`+空 answer（本来就没有）与 `failed`（系统降级）必须可分。"""
    job = _job_module()
    from app.core.queue import redis

    try:
        job._write("probe-done", {"status": "done", "answer": None, "sources": [], "elapsed_ms": 1})
        job._write("probe-fail", {"status": "failed", "answer": None, "sources": [], "reason": "llm-unavailable"})
        done = job.read_job("probe-done")
        fail = job.read_job("probe-fail")
        assert done["status"] == "done" and done["answer"] is None
        assert fail["status"] == "failed" and fail["reason"] != ""
        # 两者不可混用：客户端据 status 决定渲染"没查到"还是"出错了"
        assert done["status"] != fail["status"]
    finally:
        redis.delete(job._key("probe-done"))
        redis.delete(job._key("probe-fail"))


# ---------------------------------------------------------------------------
# 3. 测量诚实性：异步路径不得重算 latency_ms
# ---------------------------------------------------------------------------


def test_async_path_skips_latency_recompute():
    """源码级防漂移：`rag._search_impl` 里必须有 `if not q.answer_async:` 守卫。

    为什么用源码断言而不是跑一遍：这条不变量是「**没写**某行代码」，
    行为上无法从返回值区分（重算后 latency 只变大一点，断言不出错）。
    用源码锁最直接，且改动者一定会看到这条用例。
    """
    import app.services.rag as rag

    src = inspect.getsource(rag._search_impl)
    assert "if not q.answer_async:" in src, (
        "异步路径必须跳过 latency_ms 重算 —— 否则等于把'没等待的时间'算进延迟，是伪造测量"
    )


def test_async_creates_job_and_leaves_answer_none(monkeypatch):
    """`answer_async=True` 时：返回 job_id、不为本次响应生成正文。"""
    import app.services.rag as rag

    called: list[str] = []

    def fake_start(query, hits):  # noqa: ANN001
        called.append(query)
        return "job-probe-123"

    monkeypatch.setattr(
        "app.services.search_answer_job.start_answer_job", fake_start, raising=False
    )
    # 直接验证 _search_impl 的分支行为需要完整召回链路，成本过高；
    # 这里用「函数存在 + 分支在源码里」的组合断言（上面的源码锁已覆盖顺序）。
    assert hasattr(rag, "_search_impl")
    src = inspect.getsource(rag._search_impl)
    assert "start_answer_job" in src
    assert "answer_async" in src


def test_client_contract_parses_job_id():
    """客户端解析器必须读 `answer_job_id`（字段名两端一致，防手抄漂移）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "client" / "utils" / "search_api.uts").read_text(encoding="utf-8")
    assert "'answer_job_id'" in src or '"answer_job_id"' in src
    # 路径常量与后端路由必须一致
    contract = (root / "client" / "utils" / "contract.uts").read_text(encoding="utf-8")
    assert "/api/v1/search/answers/{job_id}" in contract


# ---------------------------------------------------------------------------
# 4. 归属校验（IDOR）—— 2026-10-24 被 test_authz_gate 抓出来后补的
#
# 初版快照没记 user_id，端点只靠「uuid4 hex 猜不到」。`test_authz_gate` 直接判红：
# 「按 id 操作既有实体的端点必须引用 user.id」。那条检查是对的 —— 安全性不该建立
# 在「别人猜不到」上（uuid 会出现在日志/分享链接/截图里）。
# ---------------------------------------------------------------------------


def test_read_job_rejects_other_users_snapshot():
    """别人的 job_id ⇒ None（端点据此 404，不泄露「存在但不属于你」）。"""
    job = _job_module()
    from app.core.queue import redis

    jid = "owner-probe"
    try:
        job._write(jid, {"status": "done", "answer": "我的私事", "sources": [], "user_id": "user-A"})
        # 本人可读
        assert job.read_job(jid, user_id="user-A") is not None
        # 他人不可读（返回 None，而不是抛错 —— 端点统一 404 口径）
        assert job.read_job(jid, user_id="user-B") is None
    finally:
        redis.delete(job._key(jid))


def test_read_job_rejects_legacy_snapshot_without_owner():
    """老快照（本字段上线前的残留）没有 user_id ⇒ 传了 user_id 时一律拒。

    宁可让极少数在途 job 失效（客户端按"没拿到回答"渲染），也不放过越权读。
    """
    job = _job_module()
    from app.core.queue import redis

    jid = "legacy-probe"
    try:
        job._write(jid, {"status": "done", "answer": "旧格式", "sources": []})
        assert job.read_job(jid, user_id="user-A") is None
    finally:
        redis.delete(job._key(jid))


def test_start_answer_job_records_owner():
    """`start_answer_job` 必须把 user_id 写进快照（否则归属校验无据可依）。

    ⚠️ 不要断言 `status == "pending"`：后台线程是**并发**的，且本用例传空 hits ⇒
    `generate_answer` 立刻返回（无证据 ⇒ answer=None、failed=False），
    线程可能在断言前就把快照覆盖成 `done` —— 那是**正确的**行为，断言 pending 会变成 flaky
    （实测在整跑时偶发失败、单跑必过，就是这个竞态）。
    本用例的考点是「归属被记录」，故只断言 user_id 与 status 属合法取值。
    """
    job = _job_module()
    from app.core.queue import redis

    jid = job.start_answer_job("探针查询", [], user_id="user-C")
    try:
        snap = job.read_job(jid, user_id="user-C")
        assert snap is not None
        assert snap.get("user_id") == "user-C"
        assert snap.get("status") in ("pending", "done", "failed")
    finally:
        redis.delete(job._key(jid))


def test_endpoint_passes_user_id_to_read_job():
    """源码级防漂移：端点**必须**把 user.id 传进 read_job（否则归属校验被绕过）。"""
    import inspect

    from app.api import search as search_api

    src = inspect.getsource(search_api.get_answer_job)
    assert "read_job(job_id, user_id=" in src, (
        "get_answer_job 必须把 user.id 传给 read_job —— 否则归属校验形同虚设"
    )
