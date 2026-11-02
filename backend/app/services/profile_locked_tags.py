"""锁定标签（用户显式固定，AI 不再自动写回）—— B1:139 落地。

设计口径（`忆述光华_深度开发设计/01_用户画像系统_B1.md:139`）：
    修改/删除：对话式（"把'养猫'删了，那是我室友的猫" → LLM 定位维度 → 删除 +
    **用户锁定，LLM 不再自动写回**）

为什么需要这个模块：客户端 `TagEditPanel` 的「固定」此前只写本机 storage
（键 `portrait_locked_tags`），后端完全不知情 ⇒ 画像重建/入库标注时 AI 仍会按自己的
口径把用户删掉或固定的标签写回来——「锁定」是空承诺。本模块把集合搬到服务端
（`user_profile.locked_tags`），成为**唯一真源**；客户端 storage 降级为离线兜底缓存。

消费方（写回侧唯二入口）：
  - `services/profile_annotator.apply_annotation`（入库标注 / 画像重建 / 访谈作答
    全部汇流于此）——命中锁定集合即 `action="skipped", reason="locked"`，
    于是「不新增 / 不覆盖 / 不删除」三条同时成立。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserProfile

logger = logging.getLogger("yishu.profile_locked_tags")

# 上限是**输入校验**（防止把整页贴进标签栏），不是产品功能门槛：
# 客户端单标签 maxlength=12，正常用法远达不到。
MAX_TAGS = 50
MAX_TAG_LEN = 32


def normalize_tags(raw) -> list[str]:
    """规整锁定标签集合：去首尾空白、丢空串、去重（保序）；非法即抛 ValueError。

    为什么不静默丢弃非法项：静默丢弃会让「PUT 成功但集合变了」无法解释
    （用户以为固定的标签其实没存上）。调用方转成 422 让客户端看得见。
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("tags 必须是字符串数组")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("tags 必须是字符串数组")
        tag = item.strip()
        if tag == "":
            continue  # 空串/纯空白是客户端的"空输入"，不是非法值
        if len(tag) > MAX_TAG_LEN:
            raise ValueError(f"单个标签过长（上限 {MAX_TAG_LEN} 字符）")
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValueError(f"标签数量超上限（{MAX_TAGS}）")
    return out


def get_locked_tags(db: Session, user_id: str) -> list[str]:
    """读锁定集合。**画像行不存在时返回 []，不建空行**（GET 不该有副作用）。"""
    raw = db.execute(
        select(UserProfile.locked_tags).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    if not raw:
        return []
    return [t for t in raw if isinstance(t, str) and t.strip()]


def locked_tag_set(db: Session, user_id: str) -> set[str]:
    """写回侧用的快速判定集合（`apply_annotation` / `record_hits` 每批读一次）。"""
    return set(get_locked_tags(db, user_id))


def replace_locked_tags(db: Session, user_id: str, tags) -> list[str]:
    """整体覆盖（与客户端「完成即全量落盘」口径一致）：返回规整后的最新集合。

    注意：**不是**合并/增量。用户取消固定某个标签，就是把它从数组里去掉——
    增量语义会让"取消固定"永远无法表达。
    """
    normalized = normalize_tags(tags)
    # 惰性导入：profile_annotator 顶层要 import 本模块（locked_tag_set），
    # 顶层互导会成环。
    from app.services.profile_annotator import get_or_create_profile

    profile = get_or_create_profile(db, user_id)
    profile.locked_tags = normalized
    db.commit()
    logger.info(
        "[锁定标签] 覆盖 user=%s count=%d", user_id, len(normalized)
    )
    return normalized
