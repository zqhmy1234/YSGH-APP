"""微信**授权登录**→ 建渠道绑定（2026-10-15 · 台账 §7.29）。

背景：F6 微信入口要成立，必须让「App 侧拿到的微信身份」与「企微客服侧收到的 openid」
对到同一个人身上。设计决策 #8 定为 **unionid 对齐**：

    App 点「微信绑定」→ `uni.login` → `POST /auth/wechat {code}` → code2session
    → **unionid（用户主键）+ openid（绑定表键）** → 授权即完成绑定 ✓
    企微侧：客服 openid →（batchget 解析）→ unionid → 匹配同一 user ✓

原实现只建 `users` 行、**不写 `user_wechat_bindings`** ⇒ 企微侧永远匹配不到人（入口不可达）。

覆盖（dev 未配 appid/secret ⇒ 走 mock 身份，但**管道与真实路径完全一致**）：
  ① 授权登录后绑定表出现该 openid（channel 取自 `WECHAT_LOGIN_CHANNEL`）
  ② 同一 openid 已绑**他人**时：登录**仍成功**（绑定失败不得影响登录），且**不换绑**
  ③ 同用户重复登录 ⇒ 幂等（不产生重复行、不报错）
"""
import pytest
from app.db.models import UserWechatBinding
from app.services.wechat.binding import bind

pytestmark = pytest.mark.integration


def _login(client, code: str, device_id: str = "dev-bind"):
    return client.post("/api/v1/auth/wechat", json={"code": code, "device_id": device_id})


def test_wechat_login_creates_channel_binding(client, db_user):
    """① 授权登录 ⇒ 绑定表落一条（openid=`mock-openid-<code>`，channel=wechat_app）"""
    db, _seed_user = db_user
    r = _login(client, "bind-flow-1")
    assert r.status_code == 200, r.text
    uid = r.json()["data"]["user"]["id"]

    row = (
        db.query(UserWechatBinding)
        .filter(UserWechatBinding.openid == "mock-openid-bind-flow-1")
        .one()
    )
    assert str(row.user_id) == str(uid), "绑定必须落在本次登录建出的那个用户上"
    assert row.channel == "wechat_app", f"channel 应取 WECHAT_LOGIN_CHANNEL，实际 {row.channel}"


def test_wechat_login_is_idempotent_on_binding(client, db_user):
    """③ 同用户重复授权登录 ⇒ 幂等：仍是同一行、同一 openid，不报错"""
    db, _seed_user = db_user
    first = _login(client, "bind-flow-2")
    assert first.status_code == 200, first.text
    again = _login(client, "bind-flow-2", "dev-bind-2b")
    assert again.status_code == 200, again.text

    rows = (
        db.query(UserWechatBinding)
        .filter(UserWechatBinding.openid == "mock-openid-bind-flow-2")
        .all()
    )
    assert len(rows) == 1, f"重复登录不得产生重复绑定行：{rows}"
    assert str(rows[0].user_id) == str(first.json()["data"]["user"]["id"])


def test_conflicting_openid_does_not_break_login_and_never_rebinds(client, db_user):
    """② openid 已绑**他人** ⇒ 登录照常成功，且**不换绑**（既定纪律：静默换绑＝把别人微信
    收到的记忆改投到我账号）。这条同时守住两件事：可用性（登录不受影响）+ 安全（不换绑）。"""
    db, other_user = db_user
    bind(db, str(other_user.id), "mock-openid-bind-flow-3", "wechat_app")
    db.commit()

    r = _login(client, "bind-flow-3", "dev-bind-3")
    assert r.status_code == 200, f"绑定冲突不得让登录失败：{r.text}"

    rows = (
        db.query(UserWechatBinding)
        .filter(UserWechatBinding.openid == "mock-openid-bind-flow-3")
        .all()
    )
    assert len(rows) == 1, f"不得新增绑定行：{rows}"
    assert str(rows[0].user_id) == str(other_user.id), "原绑定不得被改到新登录用户上"
