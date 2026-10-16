"""对话上下文组装（附件契约最小可用 · 2026-10-16 · 台账 §7.14 / 审计 A1-A3·D7）。

**为什么单独成模块**：这三个纯函数的职责是"把请求体里的附件与用户画像转成 agent 可读的文本"，
与 `api/chat.py` 的路由/鉴权/落库/护栏无关；且端点文件已到下沿（结构棘轮 600 行）——
把它们搬出来是为了**拆分**而不是"挪个地方"：本模块无 Session 生命周期管理、无异常映射，可单测。

三层一致（客户端 → 本后端 → agent）：
  · 客户端选图 → **既有上传链**落库拿 `content_id` → 随 `attachments[]` 上行；
  · 本模块读**既有** caption 字段转成文本提示（**不上视觉模型**，不解析图片本身）；
  · agent 侧只做字段透传（`ChatRequest.attachments` 仅留痕），真正被 AI 读到的是 `message`。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, UserProfile
from app.schemas.chat import ChatAttachment

# 给 agent 的 message 上限：与 agent 侧 `ChatRequest.message` 的 max_length 严格对齐
# （agent/src/main.py）。超限会让 agent 直接 422 ⇒ 用户看到"失败"，故本端先自行裁。
AGENT_MESSAGE_MAX = 8000
# 分隔符预算（blocks 之间各插一个 "\n\n"）
_BLOCK_SEP = "\n\n"


def content_caption(db: Session, user_id: str, content_id: str | None) -> str:
    """读某条**本人**内容的 caption。

    照片入站管线把 caption（无 caption 时是 OCR 文本）写进 `contents.text`
    （services/pipeline_photo.py:61/76）——本任务明确**不上视觉模型**，只读既有字段。
    caption 是异步管线产物，可能尚未生成 ⇒ 空串（调用方如实走"内容暂不可读"分支，不编造）。
    非本人内容 ⇒ 空串（`user_id` 过滤即 IDOR 防护：不得把他人内容的 caption 喂给 AI）。
    """
    if not content_id:
        return ""
    # 畸形 id 视同不存在（不暴露内部错误路径；口径同 api/chat.py `_resolve_conversation_id`）
    try:
        cid = str(uuid.UUID(content_id))
    except (ValueError, AttributeError, TypeError):
        return ""
    text = db.execute(
        select(Content.text).where(Content.id == cid, Content.user_id == user_id)
    ).scalar_one_or_none()
    return (text or "").strip()


def attachment_hint(db: Session, user_id: str, attachments: list[ChatAttachment]) -> str:
    """附件 → agent 可读的文本提示（空列表 ⇒ 空串，行为与接入前完全一致）。"""
    if not attachments:
        return ""
    lines: list[str] = []
    for a in attachments:
        if a.kind == "image":
            caption = content_caption(db, user_id, a.content_id)
            # 读不到 caption 时**如实说不可读**：绝不编造图片内容（假状态比缺失更贵）
            lines.append(f"用户附了一张图，内容是：{caption}" if caption else "用户附了一张图（内容暂不可读）")
        else:
            lines.append(f"用户附了一个文件：{a.name}" if a.name else "用户附了一个文件")
    return "\n".join(lines)


def profile_context(db: Session, user_id: str) -> str:
    """用户画像上下文（`user_profile.dimensions`）—— 由 `include_profile` 开关。

    为什么由后端携带画像：agent 侧零画像能力（台账 §7.13），画像数据只在本后端库里。
    端点侧 `include_profile=False` ⇒ **不调用本函数** ⇒ 消息里不含画像
    （参数必须真的生效，不是"空承诺"）；画像为空（冷启动）⇒ 空串，零回归。

    ⚠️ 现状（2026-10-16）：`include_profile` **当前无 UI 消费者**——原两枚隐私开关
    （privacy_ai_chat / privacy_echo）已于 2026-10-14 因"全仓零读零写"整行移除（台账 §7.18），
    客户端恒传 True。保留字段与这里的真行为，是为了"将来重开开关时既有链路可证伪"。
    """
    dims_raw = db.execute(
        select(UserProfile.dimensions).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    dims = dims_raw if isinstance(dims_raw, dict) else {}
    parts: list[str] = []
    for key in sorted(dims.keys()):
        value = dims[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        parts.append(f"{key}={value}")
    if not parts:
        return ""
    return "【用户画像】" + "；".join(parts)


def agent_message(content: str, profile_ctx: str, hint: str) -> str:
    """组装给 agent 的 message：正文 +（可选）画像上下文 +（可选）附件提示。

    ⚠️ 长度预算：**绝不裁用户正文**（超限是客户端契约层该拦的事），只裁我方附加的上下文——
    否则 agent 会 422，用户看到的是"发送失败"而不是"上下文太长"。
    """
    budget = AGENT_MESSAGE_MAX - len(content) - 2 * len(_BLOCK_SEP)
    blocks = [content]
    if budget > 0 and profile_ctx:
        take = profile_ctx if len(profile_ctx) <= budget else profile_ctx[:budget]
        blocks.append(take)
        budget -= len(take)
    if budget > 0 and hint:
        blocks.append(hint if len(hint) <= budget else hint[:budget])
    return _BLOCK_SEP.join(blocks)


def agent_attachment_arg(attachments: list[ChatAttachment]) -> dict:
    """附件结构化透传给 agent 的 kwargs（空 ⇒ `{}`：调用形状与既有桩测试保持一致）。

    为什么仍然透传：契约三层一致（agent `ChatRequest.attachments`）。本轮**实际信息**已经由
    `attachment_hint` 并入 message，故本字段不承载语义（agent 侧仅留痕），
    **不依赖 agent 侧实现细节**即可让 AI 读到附件内容。
    """
    if not attachments:
        return {}
    return {"attachments": [a.model_dump() for a in attachments]}
