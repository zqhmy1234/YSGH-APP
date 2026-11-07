"""客服 openid → unionid 对齐（2026-10-15 · 台账 §7.29，绑定链的另一半）。

链路：App 微信授权（写 `users.unionid`）⇄ 企微客服回调只给**客服 openid** ⇄ 靠解析出的
unionid 对齐 ⇒ 消息归属到人。覆盖：未配凭证安全降级、解析成功即归属并落绑定、解析不到仍按未绑定。
"""
import pytest
from app.core.config import settings
from app.db.models import UserWechatBinding
from app.services.wechat import kf_unionid
from app.services.wechat.binding import resolve_user_id

pytestmark = pytest.mark.integration


class _StubGateway:
    """替身：openid → unionid 映射（None 表示企微也解析不到）"""

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def resolve_unionid(self, openid: str) -> str | None:
        self.calls.append(openid)
        return self.mapping.get(openid)


@pytest.fixture()
def kf_gateway(monkeypatch):
    """注入替身 + 打开"已配凭证"开关（否则 `resolve_unionid` 会按未配置提前返回）"""
    monkeypatch.setattr(settings, "wechat_kf_secret", "kf-secret-test")
    monkeypatch.setattr(settings, "wechat_corp_id", "corp-test")
    stub = _StubGateway({})
    kf_unionid.set_gateway(stub)
    yield stub
    kf_unionid.set_gateway(kf_unionid._HttpKfGateway())


def test_unconfigured_is_safe_noop(monkeypatch):
    """未配客服 Secret ⇒ 不解析、不联网、返回 None（回调仍 200 的既有语义不变）"""
    monkeypatch.setattr(settings, "wechat_kf_secret", "")
    assert kf_unionid.resolve_unionid("kf-openid-x") is None


def test_unionid_alignment_binds_and_owns_message(db_user, kf_gateway):
    """核心：客服 openid 经 unionid 对齐到用户 ⇒ 归属成功 **且落一条 wechat_kf 绑定**"""
    db, user = db_user
    # 模拟"该用户已在 App 里微信授权过"：它的 unionid 就是微信侧的
    user.unionid = "u-align-1"
    db.commit()
    kf_gateway.mapping["kf-openid-1"] = "u-align-1"

    assert resolve_user_id(db, "kf-openid-1") == str(user.id)

    row = (
        db.query(UserWechatBinding)
        .filter(UserWechatBinding.openid == "kf-openid-1")
        .one()
    )
    assert row.channel == "wechat_kf", "对齐后应落 wechat_kf 渠道绑定"
    assert str(row.user_id) == str(user.id)

    # 已落绑定 ⇒ 第二次直查命中（不再走网络解析）
    kf_gateway.calls.clear()
    assert resolve_user_id(db, "kf-openid-1") == str(user.id)
    assert kf_gateway.calls == [], "第二次不应再解析（直查已命中）"


def test_unresolved_stays_unbound(db_user, kf_gateway):
    """企微也解析不到（未绑开放平台）⇒ 返回 None 且**不建绑定**（保持"只记录"语义）"""
    db, _user = db_user
    kf_gateway.mapping["kf-openid-2"] = None
    assert resolve_user_id(db, "kf-openid-2") is None
    assert (
        db.query(UserWechatBinding).filter(UserWechatBinding.openid == "kf-openid-2").count() == 0
    )


def test_unionid_without_local_user_stays_unbound(db_user, kf_gateway):
    """解析出 unionid 但库内没有对应用户 ⇒ 仍按未绑定（不凭空造账号）"""
    db, _user = db_user
    kf_gateway.mapping["kf-openid-3"] = "u-nobody-in-db"
    assert resolve_user_id(db, "kf-openid-3") is None
    assert (
        db.query(UserWechatBinding).filter(UserWechatBinding.openid == "kf-openid-3").count() == 0
    )
