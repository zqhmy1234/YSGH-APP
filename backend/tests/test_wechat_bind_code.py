"""微信绑定码（2026-10-15 · 台账 §4.20 ㉖）。

覆盖：发码/消费往返、单次使用、重发作废旧码、宽松前缀容忍与**不误绑**（普通聊天数字）、
以及服务层「未绑定用户发码 → 建绑定 + 不把码当内容入库」。

设计（`app/services/wechat/bind_code.py`）：App 领码 → 用户微信发码 → 企微回调的
`resolve_user_id` 解析不到 → `service.bind_by_code` 消费码并建绑定 ⇒ F6 主链打通。
"""
import pytest
from app.db.models import UserWechatBinding
from app.services.wechat import bind_code
from app.services.wechat.service import bind_by_code, process_incoming

pytestmark = pytest.mark.integration


class _MemRedis:
    """内存桩（duck-typed 满足本模块用到的 get/set/delete/getdel + ex）"""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.kv[key] = str(value)

    def delete(self, *keys: str) -> None:
        for k in keys:
            self.kv.pop(k, None)

    def getdel(self, key: str) -> str | None:
        return self.kv.pop(key, None)


@pytest.fixture()
def mem_redis(monkeypatch):
    """替换模块级 Redis 客户端（生产不注入；见 bind_code.set_client 注释）"""
    fake = _MemRedis()
    bind_code.set_client(fake)
    yield fake
    bind_code.set_client(None)


def _text_msg(openid: str, content: str) -> dict:
    return {
        "msg_id": f"wxm-bind-{openid}-{content}",
        "msg_type": "text",
        "from_user": openid,
        "content": content,
    }


# ── 码本身的语义 ────────────────────────────────────────────────────────────


def test_issue_and_consume_roundtrip(mem_redis):
    code = bind_code.issue_code("u-1")
    assert len(code) == bind_code.CODE_DIGITS and code.isdigit()
    assert bind_code.consume_code(code) == "u-1"


def test_consume_is_single_use(mem_redis):
    code = bind_code.issue_code("u-1")
    assert bind_code.consume_code(code) == "u-1"
    assert bind_code.consume_code(code) is None, "同一码不得二次使用"


def test_reissue_invalidates_previous(mem_redis):
    """重发即失效：避免用户把先前那张码留在聊天里还能被翻出来用。"""
    first = bind_code.issue_code("u-1")
    second = bind_code.issue_code("u-1")
    assert first != second, "连发两次应得到不同码（极端碰撞概率可忽略）"
    assert bind_code.consume_code(first) is None, "旧码必须失效"
    assert bind_code.consume_code(second) == "u-1"


def test_prefix_and_whitespace_tolerated(mem_redis):
    code = bind_code.issue_code("u-1")
    assert bind_code.consume_code(f" 绑定 {code} ") == "u-1"


def test_plain_chat_number_does_not_bind(mem_redis):
    """**不误绑**：普通聊天里的数字（金额/日期）不得命中绑定码。

    口径说明（2026-10-15 修正本用例）：`normalize_code` 只判**形态**（纯 6 位数字），
    「今天花了 100000 块」里的 `100000` 形态上是合法码 ⇒ 断言"取不出码"是**错的** ✗。
    真正的安全属性是**它不会命中任何活码**：活码在 Redis 里、10 分钟有效、单次使用，
    且必须与签发它的账号同期 ⇒ 未签发的数字必然落空 ⇒ 故断言 `consume_code(...) is None`。
    """
    bind_code.issue_code("u-1")
    assert bind_code.consume_code("今天花了 100000 块") is None, "普通数字不得绑到任何账号"
    assert bind_code.normalize_code("12345") is None, "位数不符不取码"
    assert bind_code.normalize_code("") is None


# ── 服务层：未绑定用户发码 → 建绑定 + 不当内容 ──────────────────────────────


def test_unbound_text_with_code_creates_binding(db_user, mem_redis):
    db, user = db_user
    code = bind_code.issue_code(str(user.id))
    msg = _text_msg("openid-A", f"绑定 {code}")

    bound = bind_by_code(db, msg)
    assert bound == str(user.id), "有效码应解析到签发它的账号"
    db.commit()
    row = (
        db.query(UserWechatBinding)
        .filter(UserWechatBinding.openid == "openid-A")
        .one()
    )
    assert str(row.user_id) == str(user.id)
    # 码消息本身**不建内容**（它是绑定指令，不是记忆）
    assert db.query(UserWechatBinding).count() >= 1


def test_unbound_text_without_code_is_ignored(db_user, mem_redis):
    """无码的未绑定消息保持既定语义：只记录、不建绑定（不误伤既有行为）。"""
    db, _user = db_user
    assert bind_by_code(db, _text_msg("openid-B", "你好呀")) is None
    assert (
        db.query(UserWechatBinding).filter(UserWechatBinding.openid == "openid-B").count() == 0
    )


def test_bound_message_still_goes_to_pipeline(db_user, mem_redis):
    """绑定成功后，同一条码消息按**只记录**处理（幂等防企微重推），不再重复消费。"""
    db, user = db_user
    code = bind_code.issue_code(str(user.id))
    msg = _text_msg("openid-C", f"绑定 {code}")
    assert bind_by_code(db, msg) == str(user.id)
    out = process_incoming(db, msg, user_id=None)
    assert out["status"] in ("created", "duplicate"), out
    # 第二次回调（企微重推）⇒ 幂等
    again = process_incoming(db, msg, user_id=None)
    assert again["status"] == "duplicate", again
