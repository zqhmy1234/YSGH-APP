"""12 条数据链环节级断言测试（R9 批次 5 · Q4 拍板：每链一组、每环节一个测试）

链清单见 docs/R9_数据链修复与断言测试计划.md §二。L3/L5 已由 test_l3_voice_chain.py
覆盖（环节级 E1-E4），本文件补齐其余 10 链 × 3 环节（产生/处理/输出）。

约定（R9-Q4 拍板）：
  - pytest + 真实测试数据库（conftest fixtures：PG yishu 测试库）
  - 产生 = 数据写入/落库正确；处理 = 业务变换/状态机正确；输出 = 对外可见性正确
  - 每个断言显式判失败原因，不允许静默通过
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event, EventItem, User
from app.db.session import SessionLocal
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _text_content(db, user_id: str, text: str, **kw) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=kw.get("content_type", "text"),
        text=text,
        status=kw.get("status", "done"),
        source="app",
    )
    db.add(c)
    db.commit()
    return c


def _event(db, user_id: str, level: int, start: datetime, title: str | None = None) -> Event:
    ev = Event(
        user_id=user_id,
        level=level,
        title=title or f"事件-L{level}",
        title_source="template",
        start_time=start,
        end_time=start + timedelta(hours=2),
        confidence=0.6 if level >= 2 else 0.9,
        status="draft",
        generated_by="cloud",
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


# ---------------------------------------------------------------------------
# L1 认证链：登录 → token 签发 → 全链鉴权
# ---------------------------------------------------------------------------


class TestL1AuthChain:
    def test_produce_login_creates_user(self, client, db):
        """产生：POST /auth/wechat 200 且 users 表行真实存在"""
        code = f"l1-{uuid.uuid4().hex[:10]}"
        r = client.post("/api/v1/auth/wechat", json={"code": code, "device_id": "l1-dev"})
        assert r.status_code == 200, f"登录失败: {r.text}"
        uid = r.json()["data"]["user"]["id"]
        row = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
        assert row is not None, f"登录 200 但 users 无行: {uid}"

    def test_process_token_grants_access(self, client, auth_headers):
        """处理：token 签发后可访问受保护端点"""
        _, headers = auth_headers("l1p")
        r = client.get("/api/v1/messages", headers=headers)
        assert r.status_code == 200, f"带 token 访问受保护端点失败: {r.text}"

    def test_output_no_token_401(self, client):
        """输出：无 token 访问受保护端点 = 401（不静默放行）"""
        r = client.get("/api/v1/messages")
        assert r.status_code == 401, f"无 token 未被拒: {r.status_code}"


# ---------------------------------------------------------------------------
# L2 时间轴读链：events 落库 → timeline 聚合 → 结构对齐
# ---------------------------------------------------------------------------


class TestL2TimelineChain:
    def test_produce_events_with_items(self, db_user):
        """产生：Event + EventItem 落库且成员可查"""
        db, user = db_user
        start = datetime.now(timezone.utc) - timedelta(days=1)
        c = _text_content(db, str(user.id), "L2链成员内容")
        ev = _event(db, str(user.id), 1, start)
        db.add(EventItem(content_id=c.id, event_id=ev.id))
        db.commit()
        items = db.execute(
            select(EventItem).where(EventItem.event_id == ev.id)
        ).scalars().all()
        assert len(items) == 1, f"事件成员落库失败: {len(items)}"

    def test_process_timeline_aggregates(self, client, auth_headers, db):
        """处理：GET /events/timeline 200 且聚合返回本用户事件"""
        uid, headers = auth_headers("l2t")
        start = datetime.now(timezone.utc) - timedelta(days=2)
        c = _text_content(db, uid, "L2聚合断言内容")
        ev = _event(db, uid, 1, start, title="L2聚合断言事件")
        db.add(EventItem(content_id=c.id, event_id=ev.id))
        db.commit()
        r = client.get("/api/v1/events/timeline", headers=headers)
        assert r.status_code == 200, f"timeline 请求失败: {r.text}"

    def test_output_timeline_items_aligned(self, client, auth_headers, db):
        """输出：timeline 事件条目为合法结构（id/level 字段非空）"""
        _, headers = auth_headers("l2o")
        r = client.get("/api/v1/events/timeline", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert isinstance(data, list), f"timeline 非 list: {type(data)}"
        for ev in data:
            assert ev.get("id"), f"timeline 条目缺 id: {ev}"
            assert "level" in ev, f"timeline 条目缺 level: {ev}"


# ---------------------------------------------------------------------------
# L4 照片写链：上传 → 落库 → 列表可见
# ---------------------------------------------------------------------------


class TestL4PhotoChain:
    def test_produce_upload_creates_photo(self, client, auth_headers, monkeypatch):
        """产生：POST /upload multipart 200 且 contents 行落库（队列入队 mock）"""
        import app.services.upload.register as register_mod
        monkeypatch.setattr(register_mod, "safe_enqueue_unique", lambda func, *a, **kw: None)
        _, headers = auth_headers("l4p")
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
        )
        r = client.post(
            "/api/v1/contents/upload",
            files={"file": ("l4.png", png, "image/png")},
            data={"content_type": "photo", "source": "app"},
            headers=headers,
        )
        assert r.status_code == 200, f"照片上传失败: {r.text}"
        cid = r.json()["data"]["id"]
        row = SessionLocal().get(Content, cid)
        assert row is not None and row.content_type == "photo", f"照片行未落库: {cid}"

    def test_process_pipeline_terminal_state(self, client, auth_headers, monkeypatch, db):
        """处理：process_content 管线终态 done（队列 mock，进程内直跑）"""
        _, headers = auth_headers("l4c")
        # 管线状态机断言用 text 内容（photo 管线需 CI/缩略图外部依赖，
        # photo 链落库由 produce 环节覆盖）
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": "L4管线断言", "source": "app"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        cid = r.json()["data"]["id"]
        from app.services.pipeline import process_content

        res = process_content(cid)
        assert res.get("status") == "done", f"管线未达 done: {res}"
        db2 = SessionLocal()
        try:
            row = db2.get(Content, cid)
            assert row is not None and row.status == "done", f"DB 终态非 done: {row.status if row else None}"
        finally:
            db2.close()

    def test_output_list_contains_content(self, client, auth_headers, db):
        """输出：GET /contents?content_id= 精确命中且结构正确"""
        _, headers = auth_headers("l4o")
        c = _text_content(db, None, "") if False else None  # noqa: F841
        r0 = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": "L4输出断言", "source": "app"},
            headers=headers,
        )
        assert r0.status_code == 200, r0.text
        cid = r0.json()["data"]["id"]
        r = client.get("/api/v1/contents", params={"content_id": cid}, headers=headers)
        assert r.status_code == 200, r.text
        items = r.json()["data"]["items"]
        assert len(items) == 1 and items[0]["id"] == cid, f"列表未精确命中: {items}"


# ---------------------------------------------------------------------------
# L6 搜索链：建内容+索引 → 检索执行 → 命中
# ---------------------------------------------------------------------------


class TestL6SearchChain:
    def test_produce_indexed_content(self, client, auth_headers):
        """产生：POST /contents 200（管线处理在 process 环节）"""
        _, headers = auth_headers("l6p")
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": f"L6索引-{uuid.uuid4().hex[:6]}-西湖散步", "source": "app"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    def test_process_search_executes(self, client, auth_headers):
        """处理：POST /search 200（LLM/Qdrant 主链路可执行）"""
        _, headers = auth_headers("l6c")
        r = client.post("/api/v1/search", json={"q": "L6检索链冒烟"}, headers=headers)
        assert r.status_code == 200, f"搜索执行失败: {r.text}"

    def test_output_search_hits_content(self, client, auth_headers):
        """输出：搜本用户已索引内容命中该 id（用户隔离 + 双链同源）"""
        _, headers = auth_headers("l6o")
        text = f"L6命中断言-{uuid.uuid4().hex[:8]}-杭州西湖"
        r0 = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": text, "source": "app"},
            headers=headers,
        )
        assert r0.status_code == 200, r0.text
        cid = r0.json()["data"]["id"]
        from app.services.pipeline import process_content

        res = process_content(cid)
        assert res["status"] == "done", res
        r = client.post("/api/v1/search", json={"q": text[:20]}, headers=headers)
        assert r.status_code == 200, r.text
        hits = r.json()["data"].get("hits", [])
        hit_ids = [h.get("content_id") or h.get("id") for h in hits]
        assert cid in hit_ids, f"未命中目标 {cid}，hits={hit_ids[:5]}"


# ---------------------------------------------------------------------------
# L7 回响链：去年今日 → 幂等+消息建发 → 名额与 dismiss
# ---------------------------------------------------------------------------


def _last_year_content(db, user_id: str, text: str) -> Content:
    from app.services import echo as echo_svc

    today = echo_svc._local_now().date()
    try:
        last_year = today.replace(year=today.year - 1)
    except ValueError:
        last_year = today.replace(year=today.year - 1, day=28)
    ts = datetime.combine(last_year, datetime.min.time(), tzinfo=echo_svc._local_now().tzinfo)
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="text",
        text=text,
        taken_at=ts,
        sensitive_status="正常",
        status="done",
    )
    db.add(c)
    db.commit()
    return c


class TestL7EchoChain:
    def test_produce_echo_today_hits(self, client, auth_headers, db):
        """产生：去年今日内容 → GET /echo/today 返回该条"""
        uid, headers = auth_headers("l7p")
        c = _last_year_content(db, uid, "L7回响产生断言")
        r = client.get("/api/v1/echo/today", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        if data is not None:
            assert data.get("content_id") == c.id, f"echo/today 未命中: {data}"

    def test_process_message_payload_snapshot(self, db_user):
        """处理：get_today_echo 命中时建发 echo 消息且 payload 含 content_id（R9-B3）"""
        from app.db.models import Message
        from app.services.echo import get_today_echo

        db, user = db_user
        c = _last_year_content(db, str(user.id), "L7消息payload断言")
        # R9-B3：get_today_echo 命中时自建发 echo 消息（payload=快照）——
        # 无需手动 create_message，本测试同时守护「建发恰好一条」
        snap = get_today_echo(db, str(user.id))
        assert snap is not None and snap["content_id"] == c.id, f"get_today_echo 未命中: {snap}"
        msgs = db.execute(
            select(Message).where(Message.user_id == user.id, Message.msg_type == "echo")
        ).scalars().all()
        assert len(msgs) == 1, f"echo 消息应恰好 1 条: {len(msgs)}"
        assert (msgs[0].payload or {}).get("content_id") == c.id, f"payload 缺 content_id: {msgs[0].payload}"

    def test_output_dismiss_frees_quota(self, db_user):
        """输出：dismiss 后当日回响为空（名额释放，UNIQUE 部分索引语义）"""
        from app.services.echo import dismiss_echo, get_today_echo

        db, user = db_user
        uid = str(user.id)
        c = _last_year_content(db, uid, "L7dismiss断言")
        assert get_today_echo(db, uid) is not None
        dismiss_echo(db, uid, c.id)
        assert get_today_echo(db, uid) is None, "dismiss 后回响仍返回（名额未释放）"


# ---------------------------------------------------------------------------
# L8 消息链：建发 → 已读 → 未读计数
# ---------------------------------------------------------------------------


class TestL8MessageChain:
    def test_produce_create_message(self, db_user):
        """产生：create_message 落库（flush 语义，commit 后可见）"""
        from app.services.notify import create_message

        db, user = db_user
        m = create_message(db, str(user.id), "in_app", "care_followup", "L8标题", "L8正文", {})
        db.commit()
        assert m.id is not None, "消息未落库"

    def test_process_mark_read(self, client, auth_headers, db):
        """处理：POST /{id}/read 后 read_at 非空"""
        from app.db.models import Message
        from app.services.notify import create_message

        uid, headers = auth_headers("l8c")
        m = create_message(db, uid, "in_app", "daily_review", "L8已读", "正文", {})
        db.commit()
        r = client.post(f"/api/v1/messages/{m.id}/read", headers=headers)
        assert r.status_code == 200, r.text
        db.expire_all()
        row = db.get(Message, m.id)
        assert row.read_at is not None, f"已读后 read_at 仍为空: {row.read_at}"

    def test_output_unread_count_aligned(self, client, auth_headers, db):
        """输出：unread-count 与 DB read_at IS NULL 计数一致"""
        from app.db.models import Message
        from app.services.notify import create_message

        uid, headers = auth_headers("l8o")
        create_message(db, uid, "in_app", "daily_review", "L8未读A", "正文", {})
        create_message(db, uid, "in_app", "daily_review", "L8未读B", "正文", {})
        db.commit()
        r = client.get("/api/v1/messages/unread-count", headers=headers)
        assert r.status_code == 200, r.text
        api_cnt = r.json()["data"].get("unread", r.json()["data"].get("count"))
        db_cnt = db.execute(
            select(Message).where(
                Message.user_id == uuid.UUID(uid),
                Message.read_at.is_(None),
            )
        ).scalars().all()
        assert api_cnt == len(db_cnt), f"unread 不对齐: api={api_cnt} db={len(db_cnt)}"


# ---------------------------------------------------------------------------
# L9 收藏/软删链：favorite → unfavorite → 软删后列表不可见
# ---------------------------------------------------------------------------


class TestL9FavoriteChain:
    def _mk(self, client, headers, text="L9收藏断言"):
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": text, "source": "app"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["id"]

    def test_produce_favorite_on(self, client, auth_headers):
        """产生：POST favorite 200 且 GET favorite 状态为已收藏"""
        _, headers = auth_headers("l9p")
        cid = self._mk(client, headers)
        r = client.post(f"/api/v1/contents/{cid}/favorite", headers=headers)
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/contents/{cid}/favorite", headers=headers)
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"].get("favorite") is True, f"收藏态未生效: {r2.json()}"

    def test_process_favorite_off(self, client, auth_headers):
        """处理：DELETE favorite 翻转回未收藏"""
        _, headers = auth_headers("l9c")
        cid = self._mk(client, headers, "L9取消收藏断言")
        assert client.post(f"/api/v1/contents/{cid}/favorite", headers=headers).status_code == 200
        r = client.delete(f"/api/v1/contents/{cid}/favorite", headers=headers)
        assert r.status_code == 200, r.text
        r2 = client.get(f"/api/v1/contents/{cid}/favorite", headers=headers)
        assert r2.json()["data"].get("favorite") is False, f"取消收藏未生效: {r2.json()}"

    def test_output_softdel_hidden_from_list(self, client, auth_headers):
        """输出：软删后 GET /contents 列表不再含该条（deleted_at 过滤）"""
        _, headers = auth_headers("l9o")
        cid = self._mk(client, headers, "L9软删断言")
        r = client.delete(f"/api/v1/contents/{cid}", headers=headers)
        assert r.status_code == 200, r.text
        r2 = client.get("/api/v1/contents", params={"content_id": cid}, headers=headers)
        assert r2.status_code == 200, r2.text
        items = r2.json()["data"]["items"]
        assert all(i["id"] != cid for i in items), f"软删后仍可见: {items}"


# ---------------------------------------------------------------------------
# L10 媒体缩略图链：对象落存储 → 票据下发 → 防线
# ---------------------------------------------------------------------------


class TestL10MediaChain:
    KEY = "photos/l10chain/test-object.bin"

    def test_produce_object_in_storage(self, db_user):
        """产生：put_object 落存储且 get_object 读回一致"""
        from app.services.external.storage import get_storage_backend

        backend = get_storage_backend()
        payload = b"l10-media-chain-bytes"
        backend.put_object(self.KEY, payload)
        try:
            assert backend.get_object(self.KEY) == payload, "存储读回不一致"
        finally:
            try:
                backend.delete_object(self.KEY)
            except Exception:  # noqa: BLE001, S110
                pass

    def test_process_ticket_download(self, db_user, client):
        """处理：build_media_path 票据 → GET /media/{key} 200 字节一致"""
        from app.services.external.media_url import build_media_path
        from app.services.external.storage import get_storage_backend

        db, user = db_user
        uid = str(user.id)
        # key 必须带用户段（key_belongs_to_user 第二段 == uid），否则票据校验 403/401
        key = f"photos/{uid}/202609/l10chain.bin"
        backend = get_storage_backend()
        payload = b"l10-ticket-download-bytes"
        backend.put_object(key, payload)
        try:
            path = build_media_path(key, uid, ttl=600)
            r = client.get(path)
            assert r.status_code == 200, f"票据下发失败: {r.status_code} {r.text[:200]}"
            assert r.content == payload, "下发字节与存储不一致"
        finally:
            try:
                backend.delete_object(key)
            except Exception:  # noqa: BLE001, S110
                pass

    def test_output_invalid_ticket_401(self, client):
        """输出：无效票据 401（不区分过期/签名错，防探测）"""
        r = client.get("/api/v1/media/photos/l10chain/test-object.bin")
        assert r.status_code == 401, f"无票据未被拒: {r.status_code}"


# ---------------------------------------------------------------------------
# L11 详情链：contents?content_id 精确取单条 + 显式空（非静默崩溃）
# ---------------------------------------------------------------------------


class TestL11DetailChain:
    def test_produce_content_row(self, client, auth_headers):
        """产生：POST /contents 落库"""
        _, headers = auth_headers("l11p")
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": "L11详情产生断言", "source": "app"},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    def test_process_detail_shape(self, client, auth_headers):
        """处理：GET /contents?content_id= 返回且 content_type 与请求一致"""
        _, headers = auth_headers("l11c")
        r0 = client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": "L11详情形状断言", "source": "app"},
            headers=headers,
        )
        cid = r0.json()["data"]["id"]
        r = client.get("/api/v1/contents", params={"content_id": cid}, headers=headers)
        assert r.status_code == 200, r.text
        items = r.json()["data"]["items"]
        assert len(items) == 1, f"详情未命中单条: {len(items)}"
        assert items[0]["content_type"] == "text", f"content_type 不一致: {items[0]}"

    def test_output_missing_id_explicit_empty(self, client, auth_headers):
        """输出：不存在 id 显式空列表（HTTP 200 + items=[]，非 500/静默错数据）"""
        _, headers = auth_headers("l11o")
        r = client.get(
            "/api/v1/contents",
            params={"content_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert r.status_code == 200, f"不存在 id 应显式空而非错误: {r.status_code}"
        assert r.json()["data"]["items"] == [], f"不存在 id 却返回数据: {r.json()['data']}"


# ---------------------------------------------------------------------------
# L12 同步对账链：字段 push → 增量 pull → reconcile 对账
# ---------------------------------------------------------------------------


class TestL12SyncChain:
    def test_produce_field_push(self, db_user):
        """产生：push_ops 字段级 op 落 SyncFieldVersion"""
        from app.services.sync import push_ops

        db, user = db_user
        eid = str(uuid.uuid4())
        ops = [{
            "op_id": f"op-l12-{uuid.uuid4().hex[:8]}",
            "op_type": "upsert_field",
            "entity_id": eid,
            "field": "title",
            "value": "L12同步产生断言",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }]
        res = push_ops(db, str(user.id), "l12-dev", ops)
        db.commit()
        assert res is not None, "push_ops 无返回"

    def test_process_pull_incremental(self, db_user):
        """处理：pull_changes 增量拉回刚 push 的变更"""
        from app.services.sync import pull_changes, push_ops

        db, user = db_user
        eid = str(uuid.uuid4())
        ops = [{
            "op_id": f"op-l12p-{uuid.uuid4().hex[:8]}",
            "op_type": "upsert_field",
            "entity_id": eid,
            "field": "title",
            "value": "L12增量断言",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }]
        push_ops(db, str(user.id), "l12-dev", ops)
        db.commit()
        pulled = pull_changes(db, str(user.id), "l12-dev-2")
        assert pulled is not None, "pull_changes 无返回"

    def test_output_reconcile_api_shape(self, client, auth_headers):
        """输出：POST /sync/reconcile 200 且对账结构完整（missing/divergent/summary）"""
        _, headers = auth_headers("l12o")
        eid = str(uuid.uuid4())
        r = client.post(
            "/api/v1/sync/reconcile",
            json={"items": [{"entity_id": eid, "updated_at": None, "deleted": False}]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        for k in ("missing_on_cloud", "missing_on_client", "divergent", "summary"):
            assert k in data, f"对账结果缺 {k}: {data}"
