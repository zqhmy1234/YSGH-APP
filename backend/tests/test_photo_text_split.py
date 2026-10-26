"""照片文本分层（caption / OCR 拆分）契约测试 · 2026-10-26

背景：详情页/搜索页把照片 `text`（caption + OCR 拼接串）整串当正文渲染，
截图类照片 97% 正文是界面文字。修复=服务端并列下发 `caption` / `ocr_text`。

本文件锁死三条不能回退的性质：
  ① **两代拼接顺序都认**（2026-10-25 翻转过 `OCR\\ncaption` → `caption\\nOCR`，
     库里同时存在两代数据；只认新顺序 ⇒ 旧行整条露出 OCR）；
  ② **无 caption 时正文为空**（不能把 OCR 顶上来当人话）；
  ③ **非照片类型不受影响**（caption/ocr_text 恒 None，text 原样）。
"""
import pytest
from app.services.photo_text import split_caption_ocr

CAPTION = "一个人在湖边散步"
OCR = "此电脑\n本地磁盘\n(D:)\nGuangH-App\ndeploy\ndownload\n在apk中搜索"


class TestSplitCaptionOcr:
    def test_new_order_caption_first(self):
        """2026-10-25 之后的写法：caption 在前、OCR 在后"""
        cap, ocr = split_caption_ocr(f"{CAPTION}\n{OCR}", OCR)
        assert cap == CAPTION
        assert ocr == OCR

    def test_legacy_order_ocr_first(self):
        """旧写法（OCR 在前）—— 库里存量行，必须也能拆对"""
        cap, ocr = split_caption_ocr(f"{OCR}\n{CAPTION}", OCR)
        assert cap == CAPTION
        assert ocr == OCR

    def test_no_ocr_returns_text_as_caption(self):
        """无 OCR（extra 里没有 ocr_text）→ 整串就是人话，ocr_text 为 None"""
        cap, ocr = split_caption_ocr(CAPTION, None)
        assert cap == CAPTION
        assert ocr is None

    def test_empty_ocr_treated_as_absent(self):
        """空串 OCR 同「无 OCR」（pipeline 只在 `if ocr_text:` 时写入，空串不应出现，
        但历史/手工数据可能存在 → 按无 OCR 处理，不做空串剥离）"""
        cap, ocr = split_caption_ocr(CAPTION, "")
        assert cap == CAPTION
        assert ocr is None

    def test_caption_failed_text_is_only_ocr(self):
        """**核心断言**：caption 生成失败时 text 只有 OCR ⇒ caption 必须为 None。

        否则客户端会把 779 字符界面文字当正文打印 —— 这正是要修的病本身。
        """
        cap, ocr = split_caption_ocr(OCR, OCR)
        assert cap is None
        assert ocr == OCR

    def test_text_none_with_ocr(self):
        """text 为 None 但 extra 有 ocr_text（caption 与 text 双空的历史形态）"""
        cap, ocr = split_caption_ocr(None, OCR)
        assert cap is None
        assert ocr == OCR

    def test_both_none(self):
        assert split_caption_ocr(None, None) == (None, None)

    def test_corrupt_concat_falls_back_to_text(self):
        """拼接串损坏（OCR 既不在头也不在尾，如被截断）→ 整串当人话。

        宁可多显示一段，也不吞掉用户可能真写进去的内容（不静默丢数据）。
        """
        broken = "前面半句介绍……\n此电脑\n本地磁盘"
        cap, ocr = split_caption_ocr(broken, OCR)
        assert cap == broken
        assert ocr == OCR

    def test_caption_with_trailing_whitespace_stripped(self):
        """剥离后首尾空白要 trim（拼接时 caption 可能带尾随空格/换行）"""
        cap, _ = split_caption_ocr(f"{CAPTION}  \n{OCR}", OCR)
        assert cap == CAPTION


class TestContentOutFields:
    """契约面：`text` 形状不变，只并列新增两个可选字段。"""

    def test_fields_optional(self):
        from app.schemas.content import ContentOut

        props = ContentOut.model_fields
        assert "caption" in props and "ocr_text" in props
        assert props["caption"].default is None
        assert props["ocr_text"].default is None
        # text 仍在且未被改成必填以外的形态（历史行为不变）
        assert "text" in props


class TestDetailHttpLayer:
    """**端到端**：照片详情接口真的把 caption/ocr_text 分层下发，且 `text` 形状不变。

    与上面的纯函数测试互补：纯函数证明"拆得对"，本条证明"接线接上了"
    （service 写了但 serializer 没传 = 修复对用户不可见，是这类改动最常见的漏法）。
    需要本地 PG；Redis 缺失只会让管线入队告警，不影响详情读。
    """

    @pytest.fixture()
    def client(self):
        from app.main import app
        from fastapi.testclient import TestClient

        return TestClient(app)

    @pytest.fixture()
    def auth_headers(self, client):
        import uuid as _uuid

        r = client.post(
            "/api/v1/auth/wechat",
            json={"code": f"oc-{_uuid.uuid4().hex[:8]}", "device_id": "oc-dev"},
        )
        assert r.status_code == 200
        token = r.json()["data"]["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_photo_detail_splits_caption_and_ocr(self, client, auth_headers):
        """照片：caption 人话 + ocr_text 机器产物，两者并列下发。"""

        from app.core.security import decode_token
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)["sub"]
        cid = None
        try:
            # 直接落库（绕过上传/管线：本测试只关心**读**路径的分层，不关心写入）
            with SessionLocal() as db:
                c = Content(
                    user_id=user_id,
                    content_type="photo",
                    text=f"{CAPTION}\n{OCR}",
                    status="done",
                    extra={"ocr_text": OCR},
                )
                db.add(c)
                db.commit()
                cid = str(c.id)

            r = client.get(f"/api/v1/contents/{cid}", headers=auth_headers)
            assert r.status_code == 200, r.text
            d = r.json()["data"]
            assert d["caption"] == CAPTION, f"caption 应为纯人话: {d.get('caption')!r}"
            assert d["ocr_text"] == OCR, "ocr_text 应为 OCR 原文"
            # **关键**：text 形状不变（搜索/向量/老客户端依赖它）
            assert d["text"] == f"{CAPTION}\n{OCR}", "text 必须原样保留拼接串"
        finally:
            if cid:
                with SessionLocal() as db:
                    db.execute(sa_delete(Content).where(Content.id == cid))
                    db.commit()

    def test_non_photo_detail_has_null_layers(self, client, auth_headers):
        """文字记忆：caption/ocr_text 必须为 null（避免客户端误按照片渲染）。"""

        from app.core.security import decode_token
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        token = auth_headers["Authorization"].split(" ", 1)[1]
        user_id = decode_token(token)["sub"]
        cid = None
        try:
            with SessionLocal() as db:
                c = Content(
                    user_id=user_id,
                    content_type="text",
                    text="今天去了湖边散步",
                    status="done",
                )
                db.add(c)
                db.commit()
                cid = str(c.id)

            r = client.get(f"/api/v1/contents/{cid}", headers=auth_headers)
            assert r.status_code == 200, r.text
            d = r.json()["data"]
            assert d["caption"] is None
            assert d["ocr_text"] is None
            assert d["text"] == "今天去了湖边散步"
        finally:
            if cid:
                with SessionLocal() as db:
                    db.execute(sa_delete(Content).where(Content.id == cid))
                    db.commit()

    def test_serializer_splits_photo_only(self):
        """非照片类型即使 text 里恰好含 ocr extra，也不得拆（避免误伤文字/语音）。"""
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from app.api.contents.serializers import _to_out

        def fake(**kw):
            base = dict(
                id="00000000-0000-0000-0000-000000000001",
                content_type="text",
                content_class=None,
                text=CAPTION,
                taken_at=None,
                place=None,
                emotion=None,
                tags_json=None,
                status="done",
                extra=None,
                created_at=datetime(2026, 10, 26, 9, 0, 0, tzinfo=UTC),
                duration=None,
                remark=None,
                size_bytes=None,
                ai_description=None,
            )
            base.update(kw)
            return SimpleNamespace(**base)

        # photo：拆开
        out_photo = _to_out(fake(content_type="photo", text=f"{CAPTION}\n{OCR}", extra={"ocr_text": OCR}))
        assert out_photo.caption == CAPTION
        assert out_photo.ocr_text == OCR
        assert out_photo.text == f"{CAPTION}\n{OCR}"  # text 形状不变

        # text：即使 extra 里塞了 ocr_text 也不拆（caption/ocr_text 恒 None）
        out_text = _to_out(fake(content_type="text", text=CAPTION, extra={"ocr_text": OCR}))
        assert out_text.caption is None
        assert out_text.ocr_text is None
        assert out_text.text == CAPTION
