"""BA3 AI 链批测试：chips 自由标签 + 照片 AI 描述（远期总账 A1/A7）

覆盖：
  - generate_tags：成功解析 / LLM 调用失败 / 解析失败 / 未配置 / 空文本（全部显式抛
    AiTaggingError，绝不静默返回空数组）+ 标签清洗（去重/≤8 字/上限 6）
  - generate_photo_description：成功 + 40 字截断 / 未配置
  - 管线挂钩：text/voice → tags 投 low 队列（enqueue_unique 捕获，不入真实 Redis，
    防并行 worker 消费）；tag_content 成功写 tags_json 可回读
  - 失败语义：LLM 异常 → 主内容 status 仍 done、tags_json/ai_description 维持 None、
    log 有错误原因（不静默）
前置：PG yishu 库；LLM 全程 mock（零真实调用，Key 纪律）
"""
import logging
import uuid
from contextlib import contextmanager

import pytest
from app.db.models import Content


@contextmanager
def capture_pipeline_logs():
    """直挂 handler 到 yishu.pipeline 收集日志。

    不走 caplog/root 传播链——其他测试文件可能污染 root logger 级别/状态
    （实证：与 test_ba1_fields 合跑时 caplog 捕不到 WARNING），此写法免疫。
    """
    logger = logging.getLogger("yishu.pipeline")
    msgs: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda rec: msgs.append(rec.getMessage())
    old_level = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield msgs
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

pytestmark = pytest.mark.integration


def _mock_llm_available(monkeypatch):
    """conftest 强制 mock_external_ai=True → llm_available()=False；打标测试显式打开"""
    monkeypatch.setattr("app.services.ai_tagging.llm_available", lambda: True)


def _content(db, user_id: str, ctype: str = "text", text: str | None = None, **kw) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=ctype,
        text=text,
        status="processing",
        source="app",
        extra=kw.get("extra"),
        cos_key=kw.get("cos_key"),
        taken_at=kw.get("taken_at"),
    )
    db.add(c)
    db.commit()
    return c


# ---------------------------------------------------------------------------
# 单元：generate_tags
# ---------------------------------------------------------------------------


class TestGenerateTags:
    def test_success_parses_json(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.ai_tagging import generate_tags

        _mock_llm_available(monkeypatch)
        monkeypatch.setattr(
            "app.services.ai_tagging._chat",
            lambda system, user: '{"tags": ["产品想法", "用户访谈", "灵感"]}',
        )
        tags = generate_tags(SimpleNamespace(text="今天聊了一个新产品想法，想做用户访谈"))
        assert tags == ["产品想法", "用户访谈", "灵感"]

    def test_clean_dedupe_and_len_limit(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.ai_tagging import generate_tags

        _mock_llm_available(monkeypatch)
        monkeypatch.setattr(
            "app.services.ai_tagging._chat",
            lambda system, user: (
                '{"tags": ["灵感", "灵感", "这个标签实在太长了超过八个字", "", "待办", '
                '"工作", "学习", "家庭", "旅行", "阅读"]}'
            ),
        )
        tags = generate_tags(SimpleNamespace(text="内容"))
        # 去重保序 + 超长剔除 + 空串剔除 + 上限 6
        assert tags == ["灵感", "待办", "工作", "学习", "家庭", "旅行"]

    def test_llm_call_failure_raises(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.ai_tagging import AiTaggingError, generate_tags

        _mock_llm_available(monkeypatch)

        def boom(system, user):
            raise TimeoutError("调用超时 30s")

        monkeypatch.setattr("app.services.ai_tagging._chat", boom)
        with pytest.raises(AiTaggingError) as raised:
            generate_tags(SimpleNamespace(text="内容"))
        assert raised.value.code == "LLM_CALL_FAILED"

    def test_parse_failure_raises_not_empty_array(self, monkeypatch):
        """解析失败必须显式抛错，绝不静默返回空数组假装成功"""
        from types import SimpleNamespace

        from app.services.ai_tagging import AiTaggingError, generate_tags

        _mock_llm_available(monkeypatch)
        monkeypatch.setattr(
            "app.services.ai_tagging._chat", lambda system, user: "抱歉，我无法输出标签"
        )
        with pytest.raises(AiTaggingError) as raised:
            generate_tags(SimpleNamespace(text="内容"))
        assert raised.value.code == "LLM_PARSE_FAILED"

    def test_not_configured_explicit_path(self, monkeypatch):
        """LLM 未配置（无 key / mock 模式）→ 配置缺失显式路径，不 crash"""
        from types import SimpleNamespace

        from app.services.ai_tagging import AiTaggingError, generate_tags

        monkeypatch.setattr("app.services.ai_tagging.llm_available", lambda: False)
        with pytest.raises(AiTaggingError) as raised:
            generate_tags(SimpleNamespace(text="内容"))
        assert raised.value.code == "LLM_NOT_CONFIGURED"

    def test_empty_text_raises(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.ai_tagging import AiTaggingError, generate_tags

        _mock_llm_available(monkeypatch)
        with pytest.raises(AiTaggingError) as raised:
            generate_tags(SimpleNamespace(text="  "))
        assert raised.value.code == "EMPTY_TEXT"


# ---------------------------------------------------------------------------
# 单元：generate_photo_description
# ---------------------------------------------------------------------------


class TestGeneratePhotoDescription:
    def test_success_and_truncate_40(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.ai_tagging import MAX_DESC_LEN, generate_photo_description

        _mock_llm_available(monkeypatch)
        monkeypatch.setattr(
            "app.services.ai_tagging._vision",
            lambda path, prompt: "西湖边荷花盛开，远山如黛，游客众多，天气晴朗，一派夏日风光景象，湖面波光粼粼",
        )
        desc = generate_photo_description(SimpleNamespace(extra={}), image_path="data/tmp/ai_tag_probe.jpg")
        assert 0 < len(desc) <= MAX_DESC_LEN

    def test_not_configured_explicit_path(self, monkeypatch):
        from types import SimpleNamespace

        from app.services.ai_tagging import AiTaggingError, generate_photo_description

        monkeypatch.setattr("app.services.ai_tagging.llm_available", lambda: False)
        with pytest.raises(AiTaggingError) as raised:
            generate_photo_description(SimpleNamespace(extra={}), image_path="data/tmp/ai_tag_probe.jpg")
        assert raised.value.code == "LLM_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# 管线挂钩：text/voice → tag_content（low 队列）
# ---------------------------------------------------------------------------


class TestPipelineTagHook:
    def test_text_content_queues_tag_and_writes_tags_json(self, db_user, monkeypatch):
        db, user = db_user
        c = _content(db, user.id, "text", "今天想去吃火锅，顺便聊聊新项目")
        monkeypatch.setattr(
            "app.services.pipeline._get_classifier",
            lambda: lambda t: {"label": "todo", "label_cn": "待办", "confidence": 0.95},
        )
        queued = []
        monkeypatch.setattr(
            "app.core.queue.enqueue_unique",
            lambda func, key, *a, **kw: queued.append((func, key)),
        )

        from app.services.pipeline import process_content, tag_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        assert r["tags_job"] == "queued"
        assert (tag_content, str(c.id)) in queued  # low 队列去重键 = content_id

        # 直接执行打标任务（模拟 worker 消费），LLM mock
        _mock_llm_available(monkeypatch)
        monkeypatch.setattr(
            "app.services.ai_tagging._chat",
            lambda system, user: '{"tags": ["火锅", "新项目", "生活"]}',
        )
        tr = tag_content(str(c.id))
        assert tr["status"] == "succeeded"
        db.expire(c)
        db.refresh(c)
        assert c.tags_json == ["火锅", "新项目", "生活"]
        assert c.status == "done"

    def test_voice_content_queues_tag(self, db_user, monkeypatch, tmp_path):
        from app.services.external.asr import AsrResult

        db, user = db_user
        wav = tmp_path / "tag.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        monkeypatch.setattr(
            "app.services.external.asr.transcribe",
            lambda path, **kwargs: AsrResult(
                text="今天心情很好",
                channel="funasr",
                emotion_source="none",
                audio_format="wav",
                source_audio_sha256="abc",
            ),
        )
        monkeypatch.setattr(
            "app.services.pipeline._get_classifier",
            lambda: lambda t: {"label": "emotion", "label_cn": "情绪", "confidence": 0.8},
        )
        queued = []
        monkeypatch.setattr(
            "app.core.queue.enqueue_unique",
            lambda func, key, *a, **kw: queued.append((func, key)),
        )
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        assert r["tags_job"] == "queued"

    def test_no_speech_voice_skips_tag(self, db_user, monkeypatch, tmp_path):
        from app.services.external.asr import AsrResult

        db, user = db_user
        wav = tmp_path / "silence.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        c = _content(db, user.id, "voice", extra={"audio_path": str(wav)})
        monkeypatch.setattr(
            "app.services.external.asr.transcribe",
            lambda *args, **kwargs: AsrResult(
                text="",
                channel="local_vad",
                outcome="no_speech",
                audio_format="wav",
                source_audio_sha256="abc",
            ),
        )
        queued = []
        monkeypatch.setattr(
            "app.core.queue.enqueue_unique",
            lambda func, key, *a, **kw: queued.append((func, key)),
        )
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        assert r["tags_job"] is None  # 空白语音无文本不打标

    def test_tag_failure_keeps_done_and_logs(self, db_user, monkeypatch):
        """LLM 异常路径：tags_json 维持 None、主内容 status 仍 done、log 有错误原因"""
        db, user = db_user
        c = _content(db, user.id, "text", "测试内容")
        c.status = "done"
        db.commit()

        _mock_llm_available(monkeypatch)

        def boom(system, call_user):
            raise RuntimeError("百炼不可用")

        monkeypatch.setattr("app.services.ai_tagging._chat", boom)
        from app.services.pipeline import tag_content

        with capture_pipeline_logs() as msgs:
            tr = tag_content(str(c.id))
        assert tr["status"] == "failed"
        assert tr["error"] == "LLM_CALL_FAILED"
        db.expire(c)
        db.refresh(c)
        assert c.tags_json is None  # 失败未写入
        assert c.status == "done"  # 打标失败不影响主内容
        assert any("AI 打标失败" in m for m in msgs)


# ---------------------------------------------------------------------------
# 管线挂钩：照片 → ai_description（内联，失败静默但 log）
# ---------------------------------------------------------------------------


class TestPhotoDescriptionHook:
    @staticmethod
    def _seed_photo(cos_key: str) -> None:
        from app.services.external.storage import get_storage_backend

        get_storage_backend().put_object(cos_key, b"fake-image-bytes")

    def test_photo_writes_ai_description(self, db_user, monkeypatch):
        db, user = db_user
        self._seed_photo("photos/ba3/1.jpg")
        c = _content(db, user.id, "photo", cos_key="photos/ba3/1.jpg")
        from app.services.external import dashscope as ds_mod

        monkeypatch.setattr(ds_mod, "image_caption", lambda k, **kw: "西湖边的荷花")
        _mock_llm_available(monkeypatch)
        monkeypatch.setattr(
            "app.services.ai_tagging._vision", lambda path, prompt: "荷塘边盛开的粉色荷花"
        )
        from app.services.pipeline import process_content

        r = process_content(str(c.id))
        assert r["status"] == "done"
        db.expire(c)
        db.refresh(c)
        assert c.ai_description == "荷塘边盛开的粉色荷花"
        assert c.status == "done"

    def test_photo_description_failure_keeps_done_and_logs(
        self, db_user, monkeypatch
    ):
        db, user = db_user
        self._seed_photo("photos/ba3/2.jpg")
        c = _content(db, user.id, "photo", cos_key="photos/ba3/2.jpg")
        from app.services.external import dashscope as ds_mod

        monkeypatch.setattr(ds_mod, "image_caption", lambda k, **kw: "西湖边的荷花")
        _mock_llm_available(monkeypatch)

        def boom(path, prompt):
            raise TimeoutError("调用超时 30s")

        monkeypatch.setattr("app.services.ai_tagging._vision", boom)
        from app.services.pipeline import process_content

        with capture_pipeline_logs() as msgs:
            r = process_content(str(c.id))
        assert r["status"] == "done"  # 描述失败不影响照片主流程
        db.expire(c)
        db.refresh(c)
        assert c.ai_description is None
        assert c.status == "done"
        assert any("照片 AI 描述失败" in m for m in msgs)

    def test_photo_not_configured_skips_without_crash(
        self, db_user, monkeypatch
    ):
        """开发环境无 Key：照片描述走「配置缺失」显式路径，log 可查、主流程不受影响"""
        db, user = db_user
        self._seed_photo("photos/ba3/3.jpg")
        c = _content(db, user.id, "photo", cos_key="photos/ba3/3.jpg")
        from app.services.external import dashscope as ds_mod

        monkeypatch.setattr(ds_mod, "image_caption", lambda k, **kw: "西湖边的荷花")
        # llm_available 保持 False（conftest 强制 mock_external_ai=true）
        from app.services.pipeline import process_content

        with capture_pipeline_logs() as msgs:
            r = process_content(str(c.id))
        assert r["status"] == "done"
        db.expire(c)
        db.refresh(c)
        assert c.ai_description is None
        assert c.status == "done"
        assert any("照片 AI 描述失败" in m for m in msgs)
        assert any("百炼未配置" in m for m in msgs)
