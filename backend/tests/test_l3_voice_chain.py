"""L3 录音写链 + L5 音频播读链 · 环节级断言测试（R9-B1 · 2026-09-06 峰宝拍板⑤）

背景（R9 实锤）：短录音链 saveVoiceContent(cosKey='') 从不传音频文件——28 条 voice
failed=AUDIO_DOWNLOAD_FAILED、cos_key=NULL 恒不可播。R9-B1 修复：客户端短录音改走
分片上传（init→chunk→complete），后端 _register_voice_content 直接建 voice 内容
（cos_key 搬 voice/ 前缀 + 本地转写 text 预填 + 入队管线）。

峰宝拍板⑤：pytest 真实测试库、每链一组、每环节（产生/处理/输出）各一个测试。

- L3 组（录音写链）：E1 产生(init) / E2 上传(chunk) / E3 处理(complete 建库+text) / E4 输出(媒体可取)
- L5 组（播读链）：成功下发逐字节一致 / 非本人 404 / cos_key 缺失 404

队列隔离：_register_voice_content 的 safe_enqueue_unique 打真 Redis——统一 mock
（对齐 test_photo_content._fake_queue_tooling 先例）；worker 管线本身不在本测试范围
（test_pipeline.py 已覆盖）。
"""
import io
import uuid
import wave

import pytest
from app.db.session import SessionLocal


@pytest.fixture()
def db():
    """本文件自带 db fixture（先例对齐 test_auth_db.py：各文件自管会话）"""
    session = SessionLocal()
    yield session
    session.close()

# ---------------------------------------------------------------------------
# 链上下文 fixture：整条 L3 链跑一次，各环节测试各自断言
# ---------------------------------------------------------------------------


def _make_wav_bytes(seconds: float = 0.3) -> bytes:
    """生成最小合法 wav（44100/16bit/mono 静音）"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * int(44100 * seconds))
    return buf.getvalue()


@pytest.fixture()
def voice_chain(client, auth_headers, monkeypatch):
    """执行完整 L3 链：登录→init→chunk→complete(voice meta + text 预填)。

    返回 dict 携带各环节响应与产物（user_id/headers/upload_id/content_id/wav）。
    队列入队 mock 掉（不打真 Redis）；teardown 清理该用户全部数据。
    """
    # mock 队列：register.py 的 safe_enqueue_unique 引用自 photo_content
    import app.services.upload.register as register_mod

    monkeypatch.setattr(
        register_mod, "safe_enqueue_unique", lambda func, *a, **kw: None
    )

    user_id, headers = auth_headers("l3")
    wav = _make_wav_bytes()

    # E1 产生：init
    r_init = client.post(
        "/api/v1/upload/init",
        data={
            "client_upload_id": "l3-voice-" + uuid.uuid4().hex[:12],
            "file_name": "l3_test.wav",
            "file_size": len(wav),
            "chunk_size": len(wav),
            "upload_mode": "original",
        },
        headers=headers,
    )
    upload_id = r_init.json()["data"]["upload_id"] if r_init.status_code == 200 else ""

    # E2 上传：chunk（multipart 单块）
    r_chunk = (
        client.post(
            "/api/v1/upload/chunk",
            files={"file": ("l3_test.wav", wav)},
            data={"upload_id": upload_id, "chunk_index": "0"},
            headers=headers,
        )
        if upload_id
        else None
    )

    # E3 处理：complete（voice meta + 本地转写 text 预填 = R9-B1 新契约）
    meta = {
        "content_type": "voice",
        "duration_ms": 300,
        "source": "app",
        "text": "L3链路测试语音",
        "emotion": "calm",
        "extra": {"file_name": "l3_test.wav"},
    }
    r_complete = (
        client.post(
            "/api/v1/upload/complete",
            data={"upload_id": upload_id, "meta": json_dumps(meta)},
            headers=headers,
        )
        if upload_id
        else None
    )
    content_id = ""
    if r_complete is not None and r_complete.status_code == 200:
        content_id = r_complete.json()["data"].get("content_id", "")

    yield {
        "user_id": user_id,
        "headers": headers,
        "wav": wav,
        "r_init": r_init,
        "r_chunk": r_chunk,
        "r_complete": r_complete,
        "upload_id": upload_id,
        "content_id": content_id,
    }

    # teardown：清该用户数据（contents/events/messages 等 30+ 子表）+ 尽力清存储对象
    from tests.conftest import cleanup_user_data

    db = SessionLocal()
    try:
        keys = []
        try:
            from app.db.models import Content

            for (cos_key,) in db.query(Content.cos_key).filter(
                Content.user_id == uuid.UUID(user_id),
                Content.cos_key.isnot(None),
            ):
                keys.append(cos_key)
        except Exception:  # noqa: BLE001, S110 —— 存键失败不阻断 DB 清理
            pass
        cleanup_user_data(db, user_id)
        # DB 清理后再删存储对象（fs 后端；对象删失败仅告警）
        if keys:
            try:
                from app.services.external.storage import get_storage_backend

                backend = get_storage_backend()
                for k in keys:
                    try:
                        backend.delete_object(k)
                    except Exception:  # noqa: BLE001, S110
                        pass
            except Exception:  # noqa: BLE001, S110
                pass
    finally:
        db.close()


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj)


# ---------------------------------------------------------------------------
# L3 组 · 每环节一个断言
# ---------------------------------------------------------------------------


class TestL3VoiceWriteChain:
    def test_e1_init_accepts_sanitized_id(self, voice_chain):
        """环节①产生：合法 client_upload_id → init 200 且发 upload_id。

        R7 前该链恒 422（id 含 / . | 撞白名单），本断言锁死回归。
        """
        r = voice_chain["r_init"]
        assert r.status_code == 200, r.text
        assert r.json()["data"]["upload_id"], "init 必须返回 upload_id"

    def test_e2_chunk_single_block_200(self, voice_chain):
        """环节②上传：单块 multipart → 200"""
        r = voice_chain["r_chunk"]
        assert r is not None, "upload_id 缺失导致 chunk 未执行"
        assert r.status_code == 200, r.text

    def test_e3_complete_creates_voice_with_text(self, voice_chain, db):
        """环节③处理：complete(voice meta) → 建 voice 内容。

        R9-B1 断言：cos_key 以 voice/ 前缀落库（对象已从 photos/ 搬移）+
        本地转写 text 预填落库 + client_emotion 进 extra（不进正式情绪字段）。
        """
        r = voice_chain["r_complete"]
        assert r is not None, "chunk 失败导致 complete 未执行"
        assert r.status_code == 200, r.text
        content_id = voice_chain["content_id"]
        assert content_id, "complete 必须返回 content_id"

        from app.db.models import Content

        row = db.get(Content, uuid.UUID(content_id))
        assert row is not None
        assert row.content_type == "voice"
        assert row.cos_key and row.cos_key.startswith("voice/"), (
            f"cos_key 必须落 voice/ 前缀，实际 {row.cos_key!r}"
        )
        assert row.text == "L3链路测试语音", "本地转写 text 必须预填落库"
        assert row.extra.get("client_emotion") == "calm"
        assert row.extra.get("duration_ms") == 300
        assert row.status == "processing", "建库后状态必须 processing（等待管线）"

    def test_e4_audio_retrievable_via_media_endpoint(self, voice_chain, client):
        """环节④输出：GET /media/audio/{content_id} → 200 + audio/* + 字节一致。

        这是「有音频数据必须能播」的最小闭环断言（L5 播读链成功路径）。
        """
        cid = voice_chain["content_id"]
        r = client.get(
            f"/api/v1/media/audio/{cid}", headers=voice_chain["headers"]
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("audio/")
        assert r.content == voice_chain["wav"], "下发字节必须与上传 wav 逐字节一致"


# ---------------------------------------------------------------------------
# L5 组 · 播读链失败路径（诚实错误态：不可播必须显式 404，不静默）
# ---------------------------------------------------------------------------


class TestL5AudioReadChain:
    def test_audio_of_other_user_404(self, client, auth_headers, voice_chain):
        """非本人 content → 404（防 IDOR，不泄漏归属）"""
        _, other_headers = auth_headers("l5other")
        r = client.get(
            f"/api/v1/media/audio/{voice_chain['content_id']}", headers=other_headers
        )
        assert r.status_code == 404

    def test_audio_missing_cos_key_404(self, client, auth_headers, db):
        """cos_key 缺失（历史短录音）→ 404 诚实报「音频不可用」，绝不返回假音频"""
        from app.db.models import Content

        user_id, headers = auth_headers("l5nocos")
        row = Content(
            user_id=uuid.UUID(user_id),
            content_type="voice",
            text="无音频存量",
            source="app",
            status="confirmed",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        try:
            r = client.get(f"/api/v1/media/audio/{row.id}", headers=headers)
            assert r.status_code == 404
        finally:
            from tests.conftest import cleanup_user_data

            cleanup_user_data(db, user_id)

    def test_audio_soft_deleted_404(self, client, auth_headers, db):
        """P2-4（2026-09-10 深扫）：本人软删（回收站）期间的语音 → 404 不下发。

        与 waveform/favorite/PATCH 同族 _load_alive_content 口径对齐——全项目
        「deleted_at 内容对外不可达」一致性契约；恢复（deleted_at 清空）后可播。
        本用例先断软删态 404，再模拟恢复断 200，钉死两侧语义。
        """
        from datetime import datetime, timezone

        from app.db.models import Content
        from app.services.external.storage import get_storage_backend

        user_id, headers = auth_headers("l5softdel")
        cos_key = f"voice/{user_id}/202609/softdel_{uuid.uuid4().hex[:8]}.wav"
        get_storage_backend().put_object(cos_key, b"RIFFfake-wav-bytes")
        row = Content(
            user_id=uuid.UUID(user_id),
            content_type="voice",
            text="回收站语音",
            source="app",
            status="confirmed",
            cos_key=cos_key,
            deleted_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        try:
            # ① 软删态：即使对象在存储、是本人内容，也必须 404
            r = client.get(f"/api/v1/media/audio/{row.id}", headers=headers)
            assert r.status_code == 404, f"软删语音应 404，实际 {r.status_code}"
            # ② 模拟恢复：清 deleted_at 后即可播（证明 404 归因软删而非其他）
            row.deleted_at = None
            db.commit()
            r2 = client.get(f"/api/v1/media/audio/{row.id}", headers=headers)
            assert r2.status_code == 200, f"恢复后应 200，实际 {r2.status_code}"
        finally:
            from tests.conftest import cleanup_user_data

            cleanup_user_data(db, user_id)
