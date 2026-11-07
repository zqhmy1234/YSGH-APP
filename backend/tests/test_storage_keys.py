"""存储键命名空间单一来源（D02-2 / D09-2 · 功能修复波 簇③）回归测试。

覆盖缺陷原貌（P0）与修复后的口径：
  - **D09-2**：微信原件落 `wechat/{uid}/` 命名空间，但 `/media/{key}` 前缀白名单
    不含该前缀 ⇒ 票据签发成功、归属校验通过，**端点恒 401**（原件永远下发不出来）。
  - **D02-2**：白名单在 5 处各写一份字面量（media / schemas / contents / storage /
    wechat）⇒ 任何一处漏改都静默分叉。

本文件既测**行为**（端点真能下发 wechat 键、仍拒非法前缀），也测**同源**
（五处派生于 `services/storage_keys.py`，不残留本地字面量）。

前置：PG yishu 库（conftest 强制 fake 存储）。
"""
import re
import uuid

import pytest
from app.services import storage_keys as sk
from app.services.external.media_url import build_media_path, content_type_for
from app.services.external.storage import _build_sts_policy, get_storage_backend

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. 行为：票据端点下发 wechat/ 命名空间（D09-2 的回归断言 —— 修复前恒 401）
# ---------------------------------------------------------------------------


def test_media_endpoint_serves_wechat_namespace(db_user, client):
    """微信原件（`wechat/{uid}/…`）凭票据应 200 下发（修复前：401 MEDIA_001）。"""
    db, user = db_user
    uid = str(user.id)
    key = f"wechat/{uid}/wxmsg-{uuid.uuid4().hex[:8]}.jpg"
    payload = b"wechat-original-bytes"
    backend = get_storage_backend()
    backend.put_object(key, payload)
    try:
        r = client.get(build_media_path(key, uid, ttl=600))
        assert r.status_code == 200, f"微信原件应可下发，实际 {r.status_code} {r.text[:200]}"
        assert r.content == payload
        assert r.headers["content-type"] == "image/jpeg"
    finally:
        backend.delete_object(key)


def test_media_endpoint_serves_wechat_voice_as_audio(db_user, client):
    """微信语音原件（`.amr`）Content-Type 必须是 audio/*（D09-14 —— 原回落 image/jpeg）。"""
    db, user = db_user
    uid = str(user.id)
    key = f"wechat/{uid}/wxvoice-{uuid.uuid4().hex[:8]}.amr"
    backend = get_storage_backend()
    backend.put_object(key, b"amr-bytes")
    try:
        r = client.get(build_media_path(key, uid, ttl=600))
        assert r.status_code == 200, r.text[:200]
        assert r.headers["content-type"].startswith("audio/"), (
            f"微信语音 MIME 失真为 {r.headers['content-type']}"
        )
    finally:
        backend.delete_object(key)


def test_media_endpoint_still_rejects_unknown_namespace(db_user, client):
    """白名单外命名空间（即使票据合法）仍 401 —— 补 wechat/ 不等于放开任意前缀。"""
    db, user = db_user
    uid = str(user.id)
    key = f"docs/{uid}/secret.txt"
    backend = get_storage_backend()
    backend.put_object(key, b"should-not-be-served")
    try:
        r = client.get(build_media_path(key, uid, ttl=600))
        assert r.status_code == 401, f"白名单外键竟可下发: {r.status_code}"
    finally:
        backend.delete_object(key)


def test_media_endpoint_whitelist_reads_single_source(db_user, client, monkeypatch):
    """反向探针：把唯一来源的 `wechat/` 拿掉 ⇒ 同一请求立刻 401。

    证明端点**真的**在消费 `storage_keys.MEDIA_PREFIXES`（不是碰巧 200），
    即"单一来源"有鉴别力；monkeypatch 自动还原，无文件改动。
    """
    db, user = db_user
    uid = str(user.id)
    key = f"wechat/{uid}/probe-{uuid.uuid4().hex[:8]}.jpg"
    backend = get_storage_backend()
    backend.put_object(key, b"probe")
    try:
        assert client.get(build_media_path(key, uid, ttl=600)).status_code == 200
        monkeypatch.setattr(
            sk, "MEDIA_PREFIXES", tuple(f"{ns}/" for ns in sk.UPLOAD_NAMESPACES)
        )
        assert client.get(build_media_path(key, uid, ttl=600)).status_code == 401
    finally:
        backend.delete_object(key)


def test_media_key_allowed_matrix():
    """准入判定逐例（空键 / 前缀伪装 / 大小写敏感 / 未知命名空间）。"""
    assert sk.is_media_key_allowed("photos/u/202601/a.jpg") is True
    assert sk.is_media_key_allowed("voice/u/202601/a.wav") is True
    assert sk.is_media_key_allowed("thumbnails/u/202601/a.jpg") is True
    assert sk.is_media_key_allowed("wechat/u/msg.jpg") is True
    # 伪装前缀 / 空 / None / 前缀仅作子串
    assert sk.is_media_key_allowed("photos_evil/u/a.jpg") is False
    assert sk.is_media_key_allowed("docs/u/a.jpg") is False
    assert sk.is_media_key_allowed("") is False
    assert sk.is_media_key_allowed(None) is False
    assert sk.is_media_key_allowed("xphotos/u/a.jpg") is False


def test_amr_has_audio_content_type():
    """D09-14：`.amr` 必须有映射（原无映射 ⇒ 回落 `image/jpeg`，语音按图片下发）。"""
    assert content_type_for("wechat/U1/M1.amr") == "audio/amr"


def test_heif_has_own_mime_and_upload_exts_subset_of_mime_table():
    """D02-9（2026-09-25 部分收口）：受理白名单里的每个照片扩展名都必须有 MIME 映射。

    原状：`.heif` ∈ `ALLOWED_PHOTO_EXTS` 但本表无映射 ⇒ `content_type_for` 回落
    `image/jpeg`（下发 MIME 与字节不符）。本断言把"两表不一致"变成**机器可查**：
    以后往受理面加扩展名而忘了补 MIME，测试即红。
    （`.gif` 方向的不一致**刻意不在本断言内**：GIF 是否受理属产品决策，见 media_url 注释。）
    """
    from app.services.external.media_url import _CONTENT_TYPES
    from app.services.upload_meta import ALLOWED_PHOTO_EXTS

    assert content_type_for("photos/U1/202609/a.heif") == "image/heif"
    assert content_type_for("photos/U1/202609/a.HEIF") == "image/heif"  # 大小写不敏感
    missing = sorted(e for e in ALLOWED_PHOTO_EXTS if e not in _CONTENT_TYPES)
    assert missing == [], f"受理白名单里这些扩展名缺 MIME 映射（会回落 image/jpeg）: {missing}"
    # D02-9 收口（2026-09-25 拍板「按建议来」+ 库内实测分布）：GIF 不支持 ⇒
    # MIME 表不保留**不可达条目**（受理层两条链路都不产出 `.gif` 键）。
    # 若将来要放开 GIF：先补 `file_magic` 魔数 + 缩略图取首帧，再连同本行一起开。
    assert ".gif" not in _CONTENT_TYPES, "GIF 未支持受理，MIME 表不应留不可达条目"
    # 反向：实测有 194 个 `.heic` 在用（iPhone）⇒ 受理面与 MIME 面都必须保留 HEIF 族
    assert {".heic", ".heif"} <= set(_CONTENT_TYPES)
    assert {".heic", ".heif"} <= ALLOWED_PHOTO_EXTS


# ---------------------------------------------------------------------------
# 2. 同源：五处均派生于 storage_keys（防再漂移）
# ---------------------------------------------------------------------------


def test_wechat_object_key_layout_unchanged():
    """键布局与既有链路逐字一致（`wechat/{uid}/{msg_id}{ext}`），扩展名带点与否均归一。"""
    assert sk.wechat_object_key("U1", "M1", ".jpg") == "wechat/U1/M1.jpg"
    assert sk.wechat_object_key("U1", "M1", "amr") == "wechat/U1/M1.amr"


def test_sts_policy_only_signs_upload_namespaces():
    """STS（客户端直传）**只签上传命名空间**——`wechat/` 属服务端写入，不进 policy。"""
    policy = _build_sts_policy("U1")
    resources = policy["statement"][0]["resource"]
    assert len(resources) == len(sk.UPLOAD_NAMESPACES)
    for ns in sk.UPLOAD_NAMESPACES:
        assert any(f"/{ns}/U1/*" in r for r in resources), f"缺少 {ns} 授权"
    assert not any("/wechat/" in r for r in resources), "wechat/ 不应可被客户端直传"


def test_schema_pattern_and_contents_prefixes_share_source():
    """pydantic pattern 与内容归属前缀都从同一集合派生（且都含 wechat/）。"""
    from app.schemas.content import _STORAGE_KEY_PREFIX

    assert _STORAGE_KEY_PREFIX == sk.CONTENT_KEY_PATTERN
    for ns in sk.CONTENT_NAMESPACES:
        assert re.match(_STORAGE_KEY_PREFIX, f"{ns}/u/202601/a.jpg"), f"{ns} 未被契约接受"
    assert not re.match(_STORAGE_KEY_PREFIX, "docs/u/a.jpg")

    prefixes = sk.user_scoped_prefixes("U1")
    assert prefixes == tuple(f"{ns}/U1/" for ns in sk.CONTENT_NAMESPACES)
    assert "wechat/U1/" in prefixes


def test_validate_cos_key_accepts_wechat_prefix_but_not_other_user(db_user):
    """`_validate_cos_key`：接受本人 `wechat/{uid}/`（消除潜伏 422），仍拒他人前缀。"""
    from app.api.contents import _validate_cos_key
    from app.core.errors import ApiError

    db, user = db_user
    uid = str(user.id)
    backend = get_storage_backend()
    own = f"wechat/{uid}/wx-{uuid.uuid4().hex[:8]}.jpg"
    backend.put_object(own, b"x")
    try:
        _validate_cos_key(db, uid, own)  # 不抛即通过

        with pytest.raises(ApiError) as exc:
            _validate_cos_key(db, uid, f"wechat/{uuid.uuid4().hex}/other.jpg")
        assert exc.value.code == "CONTENT_009"

        with pytest.raises(ApiError):
            _validate_cos_key(db, uid, f"photos/{uuid.uuid4().hex}/other.jpg")
    finally:
        backend.delete_object(own)


def test_no_hardcoded_namespace_literals_remain():
    """源码级防漂移：五处不得再出现本地白名单字面量（全部改从 storage_keys 派生）。

    这是"单一来源"的**反向保护断言**——若有人把字面量写回去，本用例即红。
    """
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[1] / "app"
    watched = {
        "api/media.py": ['("photos/", "voice/", "thumbnails/")',
                         "'photos/', 'voice/', 'thumbnails/'"],
        "schemas/content.py": [r'^(photos|voice|thumbnails)/'],
        "services/external/storage.py": ['("photos", "voice", "thumbnails")'],
        "services/wechat/service.py": ['f"wechat/{user_id}/'],
    }
    for rel, literals in watched.items():
        text = (app_root / rel).read_text(encoding="utf-8")
        for lit in literals:
            assert lit not in text, f"{rel} 仍残留本地白名单字面量: {lit}"
    # 唯一来源自身必须同时含上传与服务端命名空间
    assert "wechat" in sk.SERVER_NAMESPACES
    assert set(sk.MEDIA_NAMESPACES) == set(sk.UPLOAD_NAMESPACES) | set(sk.SERVER_NAMESPACES)
