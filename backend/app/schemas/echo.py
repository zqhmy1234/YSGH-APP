"""回响契约（P2-ECHO · 去年今日 + B4 合并八闸设置）"""
from pydantic import BaseModel, ConfigDict, Field


class EchoPreferenceOut(BaseModel):
    """回响用户设置出参（GET /api/v1/echo/preference）

    role_visibility 为 B8 家庭域预留占位：当前恒 'owner_only'，不参与闸判定。
    """
    enabled: bool
    quiet_start: str
    quiet_end: str
    max_per_day: int
    channels: str
    role_visibility: str = "owner_only"


class EchoPreferenceIn(BaseModel):
    """回响用户设置入参（PUT /api/v1/echo/preference）"""
    model_config = ConfigDict(extra="forbid")  # 未知字段（channels/role_visibility/拼写错）必须 422，不静默丢

    enabled: bool | None = None
    quiet_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    # DB 部分唯一索引 uq_echo_history_daily(user_id, shown_date) WHERE action<>'dismiss'
    # 限制每天至多一条 respond，故 max_per_day 只能 0（关）或 1；>1 会与索引冲突。
    max_per_day: int | None = Field(default=None, ge=0, le=1)


class EchoTodayOut(BaseModel):
    """去年今日回响出参（GET /api/v1/echo/today；无命中时 data=null）"""

    content_id: str
    content_type: str
    text: str | None = None
    taken_at: str | None = None    # ISO8601 字符串（服务层 isoformat 输出）
    place: str | None = None
    echo_date: str
    fingerprint: str


class EchoDismissOut(BaseModel):
    """回响划掉出参（POST /api/v1/echo/{content_id}/dismiss）"""

    dismissed: bool
    content_id: str
