"""内容契约（contents 表：照片/文字/语音/文章统一入库）"""
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.services.storage_keys import CONTENT_KEY_PATTERN

# R6#12（输入校验）：cos_key/thumbnail_key 仅允许本域前缀
# （与 api/contents._validate_cos_key 运行时校验同源；防任意键入库污染/存储遍历）
# D02-2/D09-2（2026-09-25）：前缀集合收敛到 `services/storage_keys.py` 单一来源
# （此前本处与 media/api/contents/storage/wechat 共 5 份字面量各自维护）。
_STORAGE_KEY_PREFIX = CONTENT_KEY_PATTERN


class ContentCreate(BaseModel):
    content_type: str = Field(..., pattern=r"^(photo|text|voice|article)$")
    client_generated_id: str | None = Field(
        None, min_length=1, max_length=64,
        description="客户端生成的幂等键（R4#4：同用户唯一，双击/重试不重复入库）",
    )
    text: str | None = Field(
        None, max_length=64 * 1024, description="OCR 结果/转写/原文（≤64KB）"
    )
    taken_at: datetime | None = None
    gps_lat: float | None = Field(None, ge=-90, le=90)
    gps_lng: float | None = Field(None, ge=-180, le=180)
    perceptual_hash: str | None = None    # 去重（Q16）
    cos_key: str | None = Field(
        None, pattern=_STORAGE_KEY_PREFIX,
        description=(
            "照片原件（STS 直传后回调；仅 photos/voice/thumbnails/wechat/ 前缀，"
            "且须为当前用户前缀）"
        ),
    )
    thumbnail_key: str | None = Field(
        None, pattern=_STORAGE_KEY_PREFIX,
        description="缩略图键（thumbnails/ 前缀）",
    )
    extra: dict[str, Any] | None = None   # EXIF/时长/尺寸
    # W1 记忆域打通（2026-10-06）：新增 `agent` —— AI 对话页经服务间通道镜像写入。
    # 与 app/windows/wechat/import 平级，仅作来源标注；已核实无任何消费方按
    # source 枚举值过滤（rag/pipeline/sync_writes 均不依赖）。
    source: str = Field("app", pattern=r"^(app|windows|wechat|import|agent)$")
    remark: str | None = Field(
        None, max_length=2000, description="用户备注（BA1：save 系接口可选上送）"
    )
    size_bytes: int | None = Field(
        None, ge=0,
        description="声明原件字节数（A9：可选；缺省时带 cos_key 的记录由后端实测存储对象回填）",
    )
    # W3（2026-10-08）：agent 镜像的预置 chips 标签（L3 十三主题）——写 contents.tags_json，
    # App 详情页 AI 注释卡直接展示（此前 topics 只进 extra，App 完全看不见）。
    # **仅 source=agent 生效**（App 公开端点不因此扩面，见 create_content 的落库条件）；
    # 管线打标（pipeline_tagging.tag_content）对已有 tags_json 是**合并**不是覆写，
    # 所以这里预置的标签不会被 worker 的 LLM 标签冲掉。
    tags: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]] | None = Field(
        None, max_length=10,
        description="预置 chips 标签（服务间镜像专用）：≤10 条、单条 ≤32 字，自动去重去空",
    )

    @field_validator("tags")
    @classmethod
    def _dedupe_tags(cls, v: list[str] | None) -> list[str] | None:
        """去空、去重、保序；全空则归 None（与「未传」同形态，不占存储）。

        注意空串的剔除必须在这里做而不能靠 StringConstraints 的 min_length：
        约束校验先于 validator，`min_length=1` 会把「纯空格条目」直接 422——
        而语义上 strip 后为空的条目应该是被丢弃，不是让整个请求失败。
        """
        if not v:
            return None
        out: list[str] = []
        for t in v:
            if t and t not in out:
                out.append(t)
        return out or None


class ContentOut(BaseModel):
    id: str
    content_type: str
    content_class: str | None
    text: str | None
    taken_at: datetime | None
    place: str | None
    emotion: dict | None
    tags: list[str] = []
    status: str
    audio_processing: dict[str, Any] | None = None
    created_at: datetime
    # 媒体下发 URL（Valet Key · 2026-08-31）：<image :src> 带不了 Bearer header，
    # 改发短时效签名 URL。缩略图 24h / 原图 15m（config.media_*_ttl）。
    # 相对路径（/api/v1/media/...）由客户端拼 BASE_URL；COS 后端返回绝对 URL。
    # 契约「默认缩略图 + 原图按需」：列表/卡片只用 thumbnail_url，点开详情才取 original_url。
    thumbnail_url: str | None = None
    original_url: str | None = None
    # BA1 字段补齐批（迁移 f2a3b4c5d6e7）：全可选默认 None，老数据不受影响
    duration: int | None = None        # 音频时长秒
    remark: str | None = None          # 用户备注
    size_bytes: int | None = None      # 原件体积
    tags_json: list | None = None      # 预留 AI 打标
    ai_description: str | None = None  # 预留照片 AI 描述


class ProfileSensitiveCreate(BaseModel):
    """画像级敏感增/改（B1-6 对话式：POST /api/v1/profile/sensitive）"""

    topic: str = Field(..., min_length=1, max_length=64, description="敏感话题（用户原话/短语）")
    disposition: str = Field(
        "forbid",
        pattern=r"^(allow|mention|caution|review|forbid)$",
        description="处置级别：allow/mention/caution/review/forbid",
    )
    evidence: list[str] | None = Field(None, description="证据（用户原话等）")
    locked: bool = Field(False, description="用户显式标记（永不过期语义强化）")


class ProfileSensitiveOut(BaseModel):
    """画像级敏感输出（B1-6：GET /api/v1/profile/sensitive）"""

    id: int
    topic: str
    disposition: str
    evidence: list
    locked: bool
    added_at: datetime
    updated_at: datetime


class ProfileSensitiveDeleteOut(BaseModel):
    """画像级敏感删除出参（DELETE /api/v1/profile/sensitive）"""

    deleted: bool
    topic: str


class CosPresign(BaseModel):
    """STS 临时密钥 + 上传路径（决策 #10：30 秒有效）"""

    tmp_secret_id: str
    tmp_secret_key: str
    session_token: str
    expired_at: datetime
    cos_key: str


# ---------- W2-1 删除/回收站 + W2-2 收藏（2026-09-05） ----------

TRASH_RETENTION_DAYS = 30  # 回收站保留期（对齐客户端 trash 页文案「保留 30 天」）


class ContentRemarkUpdate(BaseModel):
    """内容备注更新入参（PATCH /api/v1/contents/{id}；BB1）

    extra="forbid"：仅允许 remark 单字段，越权字段（status/user_id 等）显式 422；
    remark 可为 null（清除备注）。
    """

    model_config = ConfigDict(extra="forbid")

    remark: str | None = Field(None, max_length=2000, description="用户备注；null=清除")


class ContentDeleteOut(BaseModel):
    """软删出参（DELETE /api/v1/contents/{content_id}；W2-1）

    permanent_at：软删=保留期截止时间；restore 场景为 null。
    """

    content_id: str
    deleted: bool
    permanent_at: datetime | None = Field(None, description="保留期截止（30 天后彻底清除）；恢复时为 null")


class FavoriteOut(BaseModel):
    """收藏切换出参（POST/DELETE /api/v1/contents/{content_id}/favorite；W2-2）

    持久化：contents.extra JSONB 键 favorite_at（ISO 字符串，零迁移）——
    favorite=false 时 favorite_at=null。
    """

    content_id: str
    favorite: bool
    favorite_at: datetime | None = None


class TrashItemOut(BaseModel):
    """回收站条目（GET /api/v1/trash；W2-1）"""

    id: str
    content_type: str
    text: str | None = None
    place: str | None = None
    taken_at: datetime | None = None
    deleted_at: datetime
    days_left: int = Field(description="距彻底清除剩余天数（向下取整，最小 0）")


class TrashClearOut(BaseModel):
    """回收站清空出参（DELETE /api/v1/trash）

    2026-10-14（台账 §7.25①）：新增云端清尾计数与告警——原实现只删 DB 行（Qdrant 向量 /
    对象存储原件残留），接口却只回 `cleared` ⇒ 用户以为"彻底清空"（**隐私承诺相关**）。
    新字段均有默认值 ⇒ 老客户端读不到也不受影响（向后兼容）。
    """

    cleared: int
    vectors_purged: int = Field(0, description="请求删除的向量条数（Qdrant 不回逐点结果）")
    objects_purged: int = Field(0, description="实际删除的对象存储原件/缩略图个数")
    warnings: list[str] = Field(default_factory=list, description="清尾失败项；空=全部清干净")
