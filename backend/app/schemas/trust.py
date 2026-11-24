"""B6 信任域契约（用途授权 / 保险箱 / 审计）"""
from pydantic import BaseModel, ConfigDict, Field

from app.services.consent import CONSENT_SCOPES


class ConsentItemOut(BaseModel):
    scope: str
    label: str
    state: str
    granted: bool
    updated_at: str | None = None


class ConsentListOut(BaseModel):
    items: list[ConsentItemOut]
    note: str = "关闭授权不影响已生成内容，仅阻止后续使用"


class ConsentSetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(description=f"合法值：{', '.join(sorted(CONSENT_SCOPES))}")
    granted: bool


class VaultItemOut(BaseModel):
    content_id: str
    note: str | None = None
    created_at: str
    granted_roles: list = Field(default_factory=list)


class VaultLockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_id: str
    note: str | None = Field(default=None, max_length=200)


class VaultUnlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_id: str
    key: str


class VaultTokenOut(BaseModel):
    content_id: str
    key: str


class AuditItemOut(BaseModel):
    id: int
    action: str
    target: str | None = None
    detail: dict
    created_at: str


class AuditListOut(BaseModel):
    items: list[AuditItemOut]
