"""内容 AI 管线测试（2026-08-20 · process_content 全类型）

覆盖：
  - text：SetFit 分类回写 content_class/class_source + status=done
  - voice：ASR 转写（mock 兜底）→ text 回写 + 分类
  - photo：caption 索引 + CI 打标（失败静默不阻断）
  - 静默失败：分类/转写/caption 抛错 → status 仍 done（用户无感知）
  - 事件聚合：done 内容 → events 表 L1 日卡片 + event_items
前置：PG yishu 库（同 test_queue）
"""
import uuid

import pytest
from app.db.models import Content, Event, EventItem, User
from app.db.session import SessionLocal
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"pipeline-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(EventItem).where(EventItem.event_id.in_(
        select(Event.id).where(Event.user_id == user.id)
    )))
    db.execute(sa_delete(Event).where(Event.user_id == user.id))
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _content(db, user_id: str, ctype: str = "text", text: str | None = None, **kw) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=ctype,
        text=text,
        taken_at=kw.get("taken_at"),
        status="processing",
        source="app",
        extra=kw.get("extra"),
        cos_key=kw.get("cos_key"),
    )
    db.add(c)
    db.commit()
    return c


class TestTextPipeline:
    def test_text_classified_and_done(self, db_user, monkeypatch):
        db, user = db_user
        c = _content(db, user.id, "text", "今天想去吃火锅")
        # mock 分类避免加载 SetFit 大模型
        monkeypatch.setattr(
            "app.services.pipeline._get_classifier",
            lambda: lambda t: {"label": "todo", "label_cn": "待办", "confidence": 0.95},
        )
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert c.content_class == "todo"
        assert c.class_source == "setfit"

    def test_classify_failure_is_silent(self, db_user, monkeypatch):
        """分类抛错 → status 仍 done（用户无感知）"""
        db, user = db_user
        c = _content(db, user.id, "text", "测试内容")

        def boom(t):
            raise RuntimeError("模型不可用")

        monkeypatch.setattr("app.services.pipeline._get_classifier", lambda: boom)
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert c.content_class is None  # 分类失败未写入


class TestVoicePipeline:
    def test_voice_transcribed_and_classified(self, db_user, monkeypatch, tmp_path):
        db, user = db_user
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")  # 假 wav（mock 通道不解析内容）
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        monkeypatch.setattr(
            "app.services.pipeline._get_classifier",
            lambda: lambda t: {"label": "emotion", "label_cn": "情绪", "confidence": 0.8},
        )
        from app.services.external import asr as asr_mod

        # 强制 mock 通道（确定性输出）
        monkeypatch.setattr(asr_mod, "_llm_available", lambda: False)
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert c.text  # 转写文本已回写
        assert c.content_class == "emotion"

    def test_voice_missing_audio_silent(self, db_user, monkeypatch):
        """无音频路径 → 不抛错，status done"""
        db, user = db_user
        c = _content(db, user.id, "voice")
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"


class TestPhotoPipeline:
    @staticmethod
    def _seed_photo(cos_key: str = "photos/u/1.jpg") -> None:
        """把假图片字节写入存储后端（审查修复后：caption 需先下载 cos_key 到临时文件）"""
        from app.services.external.storage import get_storage_backend

        get_storage_backend().put_object(cos_key, b"fake-image-bytes")

    def test_photo_caption_and_done(self, db_user, monkeypatch):
        db, user = db_user
        self._seed_photo()
        c = _content(db, user.id, "photo", cos_key="photos/u/1.jpg")
        from app.services.external import dashscope as ds_mod

        monkeypatch.setattr(ds_mod, "image_caption", lambda k: "西湖边的荷花")
        monkeypatch.setattr(
            "app.services.external.tencent_ci.image_detect_label", lambda k: ["风景", "荷花"]
        )
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert c.text == "西湖边的荷花"  # caption 回写
        assert "ci_tags" in (c.extra or {})

    def test_photo_writes_image_vec(self, db_user, monkeypatch):
        """P2-07 以图搜图接线：photo 处理后将 caption 向量写入 image_vec（生产检索可用）"""
        from app.services.external import dashscope as ds_mod
        from app.services.vector_store import get_store, point_id_for

        db, user = db_user
        self._seed_photo()
        c = _content(db, user.id, "photo", cos_key="photos/u/1.jpg")
        monkeypatch.setattr(ds_mod, "image_caption", lambda k: "西湖边的荷花")
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        # 验证 image_vec 已写入（生产 collection 同点）
        store = get_store()
        points = store.client.retrieve(
            collection_name="yishu_contents",
            ids=[point_id_for(str(c.id))],
            with_vectors=True,
        )
        assert points, "photo 点应存在于生产 collection"
        vectors = points[0].vector or {}
        assert "image_vec" in vectors, "image_vec 应已写入（以图搜图生产接线）"

    def test_photo_caption_failure_silent(self, db_user, monkeypatch):
        """图片塔失败 → 照片仍 done（浏览不受影响，仅不可搜）"""
        db, user = db_user
        self._seed_photo()
        c = _content(db, user.id, "photo", cos_key="photos/u/1.jpg")
        from app.services.external import dashscope as ds_mod

        def boom(k):
            raise RuntimeError("百炼不可用")

        monkeypatch.setattr(ds_mod, "image_caption", boom)
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert c.text is None  # caption 失败未回写


class TestEventAggregation:
    def test_process_content_cloud_only_l2l3(self, db_user, monkeypatch):
        """S-SY-2（B3-6 分置）：管线完成后云侧只跑 L2/L3，不再自动建 L1（L1 由端侧提交）"""
        from datetime import datetime, timedelta, timezone

        from app.services.external.storage import get_storage_backend

        db, user = db_user
        backend = get_storage_backend()
        backend.put_object("photos/u/a.jpg", b"fake")
        backend.put_object("photos/u/b.jpg", b"fake")
        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        c1 = _content(db, user.id, "photo", cos_key="photos/u/a.jpg", taken_at=ts)
        c2 = _content(db, user.id, "photo", cos_key="photos/u/b.jpg", taken_at=ts + timedelta(minutes=10))
        monkeypatch.setattr(
            "app.services.pipeline._get_classifier",
            lambda: lambda t: {"label": "mixed", "label_cn": "混合", "confidence": 0.7},
        )
        from app.services.external import dashscope as ds_mod

        monkeypatch.setattr(ds_mod, "image_caption", lambda k: "测试照片")
        from app.services.pipeline import process_content

        for c in (c1, c2):
            process_content(str(c.id))

        # 云侧不再自动创建 L1 日卡片（2 张不足以成 L2/L3 候选 → events 为空）
        events = db.execute(select(Event).where(Event.user_id == user.id)).scalars().all()
        assert all(e.level != 1 for e in events), "S-SY-2：云侧不应再自动建 L1"

    def test_aggregate_user_full_mode_creates_l1_baseline(self, db_user):
        """full 模式（基线迁移/遗留路径）仍产生 L1 日卡片（第一波行为不删）"""
        from datetime import datetime, timedelta, timezone

        from app.services.events import aggregate_user

        db, user = db_user
        ts = datetime.now(timezone.utc) - timedelta(hours=3)
        _content(db, user.id, "photo", taken_at=ts)
        _content(db, user.id, "photo", taken_at=ts + timedelta(minutes=10))
        r = aggregate_user(db, user.id, mode="full")
        assert r["l1"] >= 1
        ev = db.execute(
            select(Event).where(Event.user_id == user.id, Event.level == 1)
        ).scalars().first()
        assert ev is not None and ev.generated_by == "cloud"
        items = db.execute(select(EventItem).where(EventItem.event_id == ev.id)).scalars().all()
        assert len(items) >= 1
