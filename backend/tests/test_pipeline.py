"""内容 AI 管线测试（2026-08-20 · process_content 全类型）

覆盖：
  - text：SetFit 分类回写 content_class/class_source + status=done
  - voice：ASR 转写 → text 回写；空白语音/失败状态显式保存
  - photo：caption 索引 + CI 打标（失败静默不阻断）
  - 分类/caption 可静默降级；ASR 主步骤失败必须 status=failed
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
    # 清理（顺序：先子表；2026-08-26：补 UserProfile——管线 B1 标注会写 user_profile，
    # 完整 FK schema 下删 user 被 user_profile_user_id_fkey 拦，本地旧库无 FK 掩盖）
    from app.db.models import Message, ProfileAnnotationPool, ProfileDimensionHistory, UserProfile

    db.execute(sa_delete(ProfileDimensionHistory).where(ProfileDimensionHistory.user_id == user.id))
    db.execute(sa_delete(ProfileAnnotationPool).where(ProfileAnnotationPool.user_id == user.id))
    db.execute(sa_delete(UserProfile).where(UserProfile.user_id == user.id))
    # 2026-08-26（Wave4 集成）：B5a 情绪消费会写 messages（voice_done/关怀）→ 删 user 前必须先清
    db.execute(sa_delete(Message).where(Message.user_id == user.id))
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
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert c.text  # 转写文本已回写
        assert c.content_class == "emotion"

    def test_voice_persists_acoustic_emotion_confidence(self, db_user, monkeypatch, tmp_path):
        from app.services.external.asr import AsrResult

        db, user = db_user
        wav = tmp_path / "emotion.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        monkeypatch.setattr(
            "app.services.external.asr.transcribe",
            lambda path, **kwargs: AsrResult(
                text="今天心情很好",
                channel="funasr",
                emotion="开心",
                emotion_confidence=0.88,
                emotion_source="sensevoice_local",
                emotion_model="iic/SenseVoiceSmall-onnx",
                audio_format="wav",
                source_audio_sha256="abc",
            ),
        )
        monkeypatch.setattr(
            "app.services.pipeline._get_classifier",
            lambda: lambda text: {
                "label": "emotion",
                "label_cn": "情绪",
                "confidence": 0.8,
            },
        )
        from app.services.pipeline import process_content

        result = process_content(str(c.id))
        assert result["status"] == "done"
        db.refresh(c)
        assert c.emotion == {
            "emotion": "开心",
            "confidence": 0.88,
            "source": "sensevoice_local",
            "model": "iic/SenseVoiceSmall-onnx",
            "actionable": True,
        }

    def test_voice_queues_local_emotion_after_transcript(
        self, db_user, monkeypatch, tmp_path
    ):
        """主转写先 done，本地情绪只进入低优先级队列。"""
        from app.services.external.asr import AsrResult

        db, user = db_user
        wav = tmp_path / "async-emotion.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        calls = []

        def fake_transcribe(path, **kwargs):
            calls.append(kwargs)
            return AsrResult(
                text="云端转写先完成",
                channel="funasr",
                emotion_source="none",
                audio_format="wav",
                source_audio_sha256="abc",
            )

        queued = []
        monkeypatch.setattr("app.services.external.asr.transcribe", fake_transcribe)
        monkeypatch.setattr("app.services.pipeline._index_content", lambda *args: None)
        monkeypatch.setattr("app.services.pipeline._classify_content", lambda *args: None)
        monkeypatch.setattr(
            "app.core.queue.enqueue_low",
            lambda func, content_id: queued.append((func, content_id)),
        )
        from app.services.pipeline import enrich_content_emotion, process_content

        response = process_content(str(c.id))
        assert response["status"] == "done"
        assert response["emotion_job"] == "queued"
        assert calls == [{"enhance_emotion": False}]
        assert queued == [(enrich_content_emotion, str(c.id))]
        db.refresh(c)
        assert c.status == "done"
        assert c.text == "云端转写先完成"
        assert c.extra["audio_processing"]["emotion_enrichment"] == "pending"

    def test_async_emotion_job_persists_result(self, db_user, monkeypatch, tmp_path):
        """独立情绪任务只补情绪字段，不改写已经完成的转写。"""
        from app.services.external.asr import SenseVoiceResult

        db, user = db_user
        wav = tmp_path / "emotion-job.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(
            db,
            user.id,
            "voice",
            text="已经完成的云端转写",
            extra={
                "audio_path": str(wav),
                "audio_processing": {"emotion_enrichment": "pending"},
            },
        )
        c.status = "done"
        c.emotion = {"emotion": "平静", "confidence": 0.0, "source": "none"}
        db.commit()
        monkeypatch.setattr(
            "app.services.external.asr.infer_local_emotion",
            lambda path: SenseVoiceResult(
                text="",
                emotion="开心",
                emotion_confidence=0.91,
                raw_emotion="<|HAPPY|>",
            ),
        )
        from app.services.pipeline import enrich_content_emotion

        response = enrich_content_emotion(str(c.id))
        assert response["status"] == "succeeded"
        db.refresh(c)
        assert c.status == "done"
        assert c.text == "已经完成的云端转写"
        assert c.emotion["source"] == "sensevoice_local"
        assert c.emotion["emotion"] == "开心"
        assert c.emotion["actionable"] is True
        assert c.extra["audio_processing"]["emotion_enrichment"] == "succeeded"

    def test_primary_emotion_skips_local_job(self, db_user, monkeypatch, tmp_path):
        """主通道已有情绪时，auto 策略不再调用 SenseVoice。"""
        db, user = db_user
        wav = tmp_path / "provider-emotion.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(
            db,
            user.id,
            "voice",
            text="主通道已有情绪",
            extra={
                "audio_path": str(wav),
                "audio_processing": {"emotion_enrichment": "pending"},
            },
        )
        c.status = "done"
        c.emotion = {"emotion": "开心", "confidence": 0.82, "source": "funasr"}
        db.commit()

        def should_not_run(path):
            raise AssertionError("不应调用本地模型")

        monkeypatch.setattr(
            "app.services.external.asr.infer_local_emotion", should_not_run
        )
        from app.services.pipeline import enrich_content_emotion

        response = enrich_content_emotion(str(c.id))
        assert response == {
            "content_id": str(c.id),
            "status": "skipped",
            "reason": "primary-emotion-present",
        }
        db.refresh(c)
        assert c.emotion["source"] == "funasr"
        assert c.extra["audio_processing"]["emotion_enrichment"] == "skipped"

    def test_emotion_enqueue_failure_keeps_transcript_done(
        self, db_user, monkeypatch, tmp_path
    ):
        """情绪任务入队失败也不能把真实转写标成 failed。"""
        from app.services.external.asr import AsrResult

        db, user = db_user
        wav = tmp_path / "enqueue-failure.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        monkeypatch.setattr(
            "app.services.external.asr.transcribe",
            lambda path, **kwargs: AsrResult(
                text="转写仍然成功",
                channel="funasr",
                emotion_source="none",
                audio_format="wav",
                source_audio_sha256="abc",
            ),
        )
        monkeypatch.setattr("app.services.pipeline._index_content", lambda *args: None)
        monkeypatch.setattr("app.services.pipeline._classify_content", lambda *args: None)

        def fail_enqueue(*args, **kwargs):
            raise RuntimeError("redis unavailable")

        monkeypatch.setattr("app.core.queue.enqueue_low", fail_enqueue)
        from app.services.pipeline import process_content

        response = process_content(str(c.id))
        assert response["status"] == "done"
        assert response["emotion_job"] == "enqueue_failed"
        db.refresh(c)
        assert c.status == "done"
        assert c.text == "转写仍然成功"
        detail = c.extra["audio_processing"]
        assert detail["emotion_enrichment"] == "enqueue_failed"
        assert detail["emotion_error"]["code"] == "EMOTION_ENQUEUE_FAILED"

    def test_voice_missing_audio_fails(self, db_user, monkeypatch):
        """无音频路径 → failed_final，不能伪装 done。"""
        db, user = db_user
        c = _content(db, user.id, "voice")
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "failed"
        assert r["outcome"] == "failed_final"
        db.refresh(c)
        assert c.status == "failed"
        assert c.extra["audio_processing"]["code"] == "AUDIO_NOT_FOUND"

    def test_voice_no_speech_is_explicit_done(self, db_user, monkeypatch, tmp_path):
        """确认静音是正常空结果，但必须携带 no_speech 原因。"""
        from app.services.external.asr import AsrResult

        db, user = db_user
        wav = tmp_path / "silence.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        result = AsrResult(
            text="",
            channel="local_vad",
            outcome="no_speech",
            model="digital-silence-v1",
            provider="local",
            audio_format="wav",
            source_audio_sha256="abc",
        )
        monkeypatch.setattr("app.services.external.asr.transcribe", lambda *args, **kwargs: result)
        from app.services.pipeline import process_content

        response = process_content(str(c.id))
        assert response["status"] == "done"
        assert response["outcome"] == "no_speech"
        db.refresh(c)
        assert c.status == "done"
        assert c.text is None
        assert c.extra["audio_processing"]["outcome"] == "no_speech"

    def test_voice_retryable_failure_reraises_for_rq_retry(
        self, db_user, monkeypatch, tmp_path
    ):
        """A2（P0-2）：retryable 失败先落审计再 re-raise——RQ Retry(3) 真正重投。

        （此前吞掉正常返回 → RQ 视为成功不重试，网络抖动后内容永久 failed，
        用户语音静默丢失；现在异常冒泡，审计已回写供 requeue_job 兜底识别）
        """
        from app.services.external.asr import AsrError

        db, user = db_user
        wav = tmp_path / "voice.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})

        def unavailable(*args, **kwargs):
            raise AsrError("NETWORK_ERROR", "timeout", retryable=True)

        monkeypatch.setattr("app.services.external.asr.transcribe", unavailable)
        from app.services.pipeline import process_content

        with pytest.raises(AsrError) as raised:
            process_content(str(c.id))
        assert raised.value.code == "NETWORK_ERROR"
        assert raised.value.retryable is True
        db.refresh(c)
        assert c.status == "failed"
        assert c.extra["audio_processing"]["outcome"] == "failed_retryable"

    def test_voice_unclassified_failure_reraises_after_audit(
        self, db_user, monkeypatch, tmp_path
    ):
        """未分类异常先落审计（failed + retryable）再 re-raise，交 RQ 重投。"""
        db, user = db_user
        wav = tmp_path / "voice.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr("app.services.external.asr.transcribe", boom)
        from app.services.pipeline import process_content

        with pytest.raises(RuntimeError):
            process_content(str(c.id))
        db.refresh(c)
        assert c.status == "failed"
        assert c.extra["audio_processing"]["code"] == "ASR_PIPELINE_ERROR"
        assert c.extra["error"]["retryable"] is True


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

class TestP0FailureWriteback:
    """P0-4（审查 H-4）：非 voice 失败同样回写 failed + extra.error（对齐 voice 先例）

    A2（P0-2）：回写后 re-raise → RQ Retry(3) 真正重投（此前吞掉返回，RQ 视为
    成功不重试，text/photo 意外异常静默坏死）。
    """

    def test_text_unclassified_failure_reraises_after_audit(self, db_user, monkeypatch):
        """text 未分类异常：先落审计（failed + retryable）再 re-raise。"""
        db, user = db_user
        c = _content(db, user.id, "text", "测试内容")

        def boom(db, content):
            raise RuntimeError("unexpected text pipeline error")

        monkeypatch.setattr("app.services.pipeline._process_text", boom)
        from app.services.pipeline import process_content

        with pytest.raises(RuntimeError):
            process_content(str(c.id))
        db.refresh(c)
        assert c.status == "failed"
        assert c.extra["error"]["code"] == "PIPELINE_ERROR"
        assert c.extra["error"]["retryable"] is True

    def test_photo_unclassified_failure_reraises_after_audit(self, db_user, monkeypatch):
        """photo 未分类异常：先落审计（failed + retryable）再 re-raise。"""
        db, user = db_user
        c = _content(db, user.id, "photo", cos_key="photos/p0/x.jpg")

        def boom(db, content):
            raise RuntimeError("unexpected photo pipeline error")

        monkeypatch.setattr("app.services.pipeline._process_photo", boom)
        from app.services.pipeline import process_content

        with pytest.raises(RuntimeError):
            process_content(str(c.id))
        db.refresh(c)
        assert c.status == "failed"
        assert c.extra["error"]["code"] == "PIPELINE_ERROR"
        assert c.extra["error"]["retryable"] is True

    def test_retry_success_clears_stale_error(self, db_user, monkeypatch):
        """A2：text 重投成功后 status→done 且清掉上一轮失败残留的 extra.error。"""
        db, user = db_user
        c = _content(db, user.id, "text", "测试内容")
        calls = {"n": 0}

        def flaky_process(db, content):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            content.text = content.text or "测试内容"
            content.content_class = "todo"

        monkeypatch.setattr("app.services.pipeline._process_text", flaky_process)
        from app.services.pipeline import process_content

        with pytest.raises(RuntimeError):
            process_content(str(c.id))  # 第 1 次：未分类异常 → 审计 + re-raise
        db.refresh(c)
        assert c.status == "failed"
        assert "error" in (c.extra or {})
        response = process_content(str(c.id))  # 第 2 次：重投成功
        assert response["status"] == "done"
        db.refresh(c)
        assert c.status == "done"
        assert "error" not in (c.extra or {})
