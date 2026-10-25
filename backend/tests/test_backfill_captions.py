"""caption 回填脚本的可解码性判据测试（D-01 · 2026-10-26）

## 为什么需要这组测试

`backfill_captions.py` 原先用 `storage.object_exists()` 判断"这张照片能不能回填"。
但真库里有 **71 个 18 字节的 `b'smoke-object-bytes'` 占位对象**（测试/烟测遗留），
`object_exists()` 对它们**返回 True** ⇒ 它们混进回填队列，每条白烧一次 Qwen3-VL
往返（真金成本），最后必然失败。

实测漏斗（2026-10-26，真库）：
    done 照片 2348 → text 空 1949 → 有 cos_key 1055 → 对象存在 49 → **PIL 可解码 17**
即 49 条里有 **32 条是占位对象**（65% 白烧）。

## 覆盖

1. `_is_decodable_image()`：真图片 True / 18 字节占位 False / 空字节 False / 非图片二进制 False
2. `_select()`：占位对象**不进**待办列表（核心断言 —— 只测 helper 会漏掉接线错误）
3. 负向探针：把判据退回 `object_exists` 语义时，占位对象**会**进列表 —— 证明测试有鉴别力
"""
from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image

# 脚本在 backend/scripts/ 下，无包结构 ⇒ 显式加路径后按模块名导入
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

pytestmark = pytest.mark.integration

# 真库里实际存在的占位对象内容（实测：71 个文件都是这个 18 字节字面量）
SMOKE_PLACEHOLDER = b"smoke-object-bytes"


def _png_bytes() -> bytes:
    """造一张**真的** PNG（1×1），PIL 可解码"""
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 120, 60)).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. 判据本身
# ---------------------------------------------------------------------------


class TestIsDecodableImage:
    def test_real_png_is_decodable(self):
        from backfill_captions import _is_decodable_image

        assert _is_decodable_image(_png_bytes()) is True

    def test_smoke_placeholder_is_not_decodable(self):
        """18 字节 `smoke-object-bytes` —— 真库 71 个，必须判 False

        这条是本缺陷的本体：`object_exists()` 对它返回 True。
        """
        from backfill_captions import _is_decodable_image

        assert len(SMOKE_PLACEHOLDER) == 18, "占位对象实测 18 字节（判据前提）"
        assert _is_decodable_image(SMOKE_PLACEHOLDER) is False

    def test_empty_bytes_is_not_decodable(self):
        from backfill_captions import _is_decodable_image

        assert _is_decodable_image(b"") is False

    def test_truncated_image_is_not_decodable(self):
        """截断的真图（前 30 字节）—— 文件头在但结构不全，verify 应拒绝"""
        from backfill_captions import _is_decodable_image

        assert _is_decodable_image(_png_bytes()[:30]) is False

    def test_non_image_binary_is_not_decodable(self):
        from backfill_captions import _is_decodable_image

        assert _is_decodable_image(b"RIFF....WAVEfmt " * 10) is False  # 假 wav


# ---------------------------------------------------------------------------
# 2. 接线：_select 必须真的跳过占位对象
# ---------------------------------------------------------------------------


def _seed_photo(db, user_id: str, cos_key: str, *, text: str | None = None) -> str:
    """建一条 done 照片行（text 为空 ⇒ 落入 empty_done 候选池）"""
    from datetime import UTC, datetime, timedelta

    from app.db.models import Content

    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo",
        text=text,
        cos_key=cos_key,
        status="done",
        source="app",
        # 必须"超龄"才进 _stale_candidates（默认 3600s）
        updated_at=datetime.now(UTC) - timedelta(hours=48),
    )
    db.add(c)
    db.commit()
    return str(c.id)


class TestSelectSkipsPlaceholders:
    def test_placeholder_object_excluded_from_todo(self, db_user, monkeypatch):
        """核心接线断言：占位对象**不进**待办，真图片**进**

        只测 `_is_decodable_image()` 是不够的——判据写对了但没接到 `_select` 里，
        缺陷照样存在（这正是本仓反复踩的"判据与接线脱节"）。
        """
        from app.services.external.storage import get_storage_backend

        db, user = db_user
        storage = get_storage_backend()

        good_key = f"photos/bench-d01/{uuid.uuid4().hex}.png"
        bad_key = f"photos/bench-d01/{uuid.uuid4().hex}.png"
        storage.put_object(good_key, _png_bytes())
        storage.put_object(bad_key, SMOKE_PLACEHOLDER)

        good_id = _seed_photo(db, user.id, good_key)
        bad_id = _seed_photo(db, user.id, bad_key)

        # D-23（2026-10-25）：超龄判定优先看 extra.processing_started_at、回退 updated_at。
        # 上面已把 updated_at 设成 48h 前 ⇒ 满足超龄。
        import backfill_captions

        ids, _scanned, funnel = backfill_captions._select(5000)

        assert good_id in ids, "真图片应进待办"
        assert bad_id not in ids, (
            "18 字节占位对象**不得**进待办（否则每条白烧一次 VL 往返）"
        )
        assert funnel["undecodable"] >= 1, "漏斗里应能看到被判掉的占位对象"

    def test_negative_probe_object_exists_would_include_placeholder(
        self, db_user, monkeypatch
    ):
        """负向探针：把判据退回 `object_exists`（即原实现）时，占位对象**会**进待办

        证明上面那条断言不是"恰好因为别的原因"通过 —— 若判据被改回去，本测试会红。
        """
        from app.services.external.storage import get_storage_backend

        db, user = db_user
        storage = get_storage_backend()
        bad_key = f"photos/bench-d01/{uuid.uuid4().hex}.png"
        storage.put_object(bad_key, SMOKE_PLACEHOLDER)
        bad_id = _seed_photo(db, user.id, bad_key)

        import backfill_captions

        # 摘掉加严判据（等价于回到 `storage.object_exists` 的原实现）
        monkeypatch.setattr(
            backfill_captions, "_is_decodable_image", lambda data: True
        )
        ids, _scanned, _funnel = backfill_captions._select(5000)
        assert bad_id in ids, (
            "负向探针失效：即便退回 object_exists 语义，占位对象也没进待办 —— "
            "说明上面那条断言不是靠加严判据变绿的"
        )


# ---------------------------------------------------------------------------
# 3. 头部注释里的数字必须与实测口径一致（防再次写错）
# ---------------------------------------------------------------------------


def test_header_documents_measured_funnel_not_114():
    """头部注释必须写**实测**漏斗，且点名占位对象 + PIL 加严

    这条是"文档与实现脱钩"的机械防线：D-01 的原始问题之一就是注释写 114
    而实测只有 17，导致后来的人按 114 估成本。

    ⚠️ 断言**不是**「文件里不能出现 114」——那条旧数字被**刻意保留在更正说明里**
    （"原头部写 114，这是错的"），删掉它等于抹掉历史、下次又会被重新写回来。
    真正要钉的是：**当前生效的结论**（可回填 17 / 容器 49 / 占位 32）在文件里，
    且标注了它是实测值。
    """
    src = (BACKEND_DIR / "scripts" / "backfill_captions.py").read_text(encoding="utf-8")
    # 当前生效的结论必须是实测值（而不是旧值）
    assert "17" in src, "头部必须写明实测可回填数 17"
    assert "49" in src, "头部必须写明对象存在的 49 条（漏斗中间量）"
    assert "PIL" in src, "头部必须说明 PIL verify 加严"
    assert "smoke-object-bytes" in src, "头部必须点名那个占位对象（否则下次又踩）"
    # 114 只允许作为「已作废」出现，不得作为结论
    assert "原头部写" in src and "这是错的" in src, (
        "114 若保留，必须明确标注为『已作废/原头部写错的』，不能作为现行结论"
    )
