"""图片塔适配层测试（R-4 · 2026-10-14）

覆盖：
  - 后端分支选择（bge_caption 现状 / dashscope_vision 真图片塔）与空间标识
  - bge 分支与改动前**逐字等价**（同样的 caption 得同样的向量）
  - vision 分支：有图走图片通道、无图有 caption 走**同空间**文本通道
  - 输入不足 / 未配 key → 显式抛错（不静默）
  - 检索端**向量空间隔离条件**的构造（`vector_store._with_image_space`）—— 这是
    「切换后不会静默返回垃圾」的关键，漏了 `IsEmptyCondition` 会丢掉全部旧点

⚠️ 真实 `tongyi-embedding-vision-plus` 调用**未在真账号上验证过**（未开通），
   本文件全部用 monkeypatch 验证**调用路径与契约**，不冒充真实调用验证。
"""
import pytest
from app.core.config import settings
from app.services import image_tower
from app.services.image_tower import (
    IMAGE_SPACE_BGE,
    IMAGE_SPACE_KEY,
    IMAGE_SPACE_VISION,
    ImageTowerError,
    current_image_space,
    encode_image_vector,
)
from app.services.vector_store import VectorStore

_STUB = [0.01] * 1024


@pytest.fixture
def backend(monkeypatch):
    """切换图片塔后端（照 test_content_safety 的 Literal 分支范式）"""

    def _set(name: str) -> None:
        monkeypatch.setattr(settings, "image_tower_backend", name)

    return _set


# ---------- 空间标识 ----------


def test_space_follows_backend(backend):
    """空间标识必须随后端实时变化（不能模块级缓存，否则切换后过滤条件不跟着走）"""
    backend("bge_caption")
    assert current_image_space() == IMAGE_SPACE_BGE
    backend("dashscope_vision")
    assert current_image_space() == IMAGE_SPACE_VISION


def test_default_backend_is_bge(monkeypatch):
    """默认必须是不改行为的 bge_caption（用独立 Settings 验默认值，不靠 monkeypatch）"""
    monkeypatch.delenv("IMAGE_TOWER_BACKEND", raising=False)
    from app.core.config import Settings

    assert Settings(_env_file=None).image_tower_backend == "bge_caption"
    assert Settings(_env_file=None).image_tower_dimension == 1024


# ---------- bge 分支 ----------


def test_bge_matches_legacy_behavior(backend, monkeypatch):
    """bge 分支 == 改动前的 `encode_dense([caption])` + 空间标记"""
    backend("bge_caption")
    monkeypatch.setattr("app.services.embedding.encode_dense", lambda texts, **kw: [_STUB])

    vec, space = encode_image_vector(caption="周末去西湖拍的照片")

    assert vec == _STUB
    assert space == IMAGE_SPACE_BGE


def test_bge_requires_caption(backend):
    """bge 分支没有 caption 就无法出向量 → 显式抛错"""
    backend("bge_caption")
    with pytest.raises(ImageTowerError, match="caption"):
        encode_image_vector()


# ---------- vision 分支 ----------


def test_vision_uses_image_channel(backend, monkeypatch):
    """有图片 → 走多模态图片通道，空间标记为 vision"""
    backend("dashscope_vision")
    calls: list[tuple] = []
    monkeypatch.setattr(
        "app.services.llm_ops.embed_image",
        lambda path, *, dimension: (calls.append((path, dimension)), _STUB)[1],
    )

    vec, space = encode_image_vector(image_path="photo.jpg", caption="无关的 caption")

    assert vec == _STUB
    assert space == IMAGE_SPACE_VISION
    assert calls == [("photo.jpg", settings.image_tower_dimension)]


def test_vision_falls_back_to_text_channel(backend, monkeypatch):
    """无图有 caption → 走**同一模型**的文本通道（同空间降级，不是跨空间混入）"""
    backend("dashscope_vision")
    monkeypatch.setattr(
        "app.services.llm_ops.embed_text_in_vision",
        lambda text, *, dimension: _STUB,
    )
    monkeypatch.setattr(
        "app.services.llm_ops.embed_image",
        lambda *a, **kw: pytest.fail("有 caption 时不该走图片通道"),
    )

    vec, space = encode_image_vector(caption="被删掉图片后只剩 caption")

    assert vec == _STUB
    assert space == IMAGE_SPACE_VISION  # 关键：与图片通道同空间


def test_vision_requires_any_input(backend):
    """既无图又无 caption → 显式抛错（由调用方降级跳过，而非写垃圾向量）"""
    backend("dashscope_vision")
    with pytest.raises(ImageTowerError):
        encode_image_vector()


def test_vision_without_key_raises(backend, monkeypatch):
    """未配 key / mock 关 → 底层显式抛错（不静默返回零向量）"""
    backend("dashscope_vision")
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "")

    with pytest.raises(RuntimeError, match="百炼未配置"):
        encode_image_vector(image_path="photo.jpg")


# ---------- 检索端空间隔离（切换才安全的根本）----------


def _space_cond(flt) -> dict:
    """取出空间隔离条件的 dict 形态（它是唯一一个 Filter 型 must 项）"""
    from qdrant_client.http import models

    conds = [c for c in (flt.must or []) if isinstance(c, models.Filter)]
    assert len(conds) == 1, f"应有且仅有一个空间条件，实际 {len(conds)}"
    return conds[0].model_dump()


def test_space_filter_includes_current_and_missing(backend):
    """隔离条件必须同时命中「当前空间」与「字段缺失」—— 后者保证旧点不丢"""
    backend("bge_caption")
    out = VectorStore._with_image_space(None)

    should = _space_cond(out)["should"]
    assert should[0]["key"] == IMAGE_SPACE_KEY
    assert should[0]["match"]["value"] == IMAGE_SPACE_BGE
    assert should[1]["is_empty"]["key"] == IMAGE_SPACE_KEY


def test_space_filter_excludes_other_space_only(backend):
    """切到 vision 后，条件里只应匹配 vision + 缺失，**不含** bge_caption"""
    backend("dashscope_vision")
    should = _space_cond(VectorStore._with_image_space(None))["should"]

    matched = [c["match"]["value"] for c in should if c.get("match")]
    assert matched == [IMAGE_SPACE_VISION], "只应匹配当前空间，不得掺入其它空间"


def test_space_filter_preserves_user_filter(backend):
    """用户过滤器必须被完整保留（本条件只做追加，不改用户语义）"""
    backend("bge_caption")
    user = VectorStore._to_filter({"content_types": ["photo"]})

    out = VectorStore._with_image_space(user)

    assert len(out.must) == 2, "应为 [用户过滤器, 空间条件]"
    assert out.must[0] == user, "用户过滤器应原样嵌入（含其 should/must_not 语义）"


def test_space_filter_without_user_filter(backend):
    """无用户过滤器时也要带上空间条件（不能退化成 None = 不过滤）"""
    backend("bge_caption")
    assert VectorStore._with_image_space(None) is not None


def test_image_tower_module_does_not_import_vector_store():
    """依赖方向护栏：图片塔模块绝不能 import vector_store（否则与它形成循环）"""
    import ast
    from pathlib import Path

    src = Path(image_tower.__file__).read_text(encoding="utf-8")
    names = {n.module or "" for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom)}
    names |= {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in names if "vector_store" in m], "image_tower 不得依赖 vector_store"
    assert not [m for m in names if m.startswith("app.services.rag")], "image_tower 不得依赖 rag.*"
