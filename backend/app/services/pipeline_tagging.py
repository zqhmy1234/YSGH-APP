"""内容 AI 管线 · chips 自由标签任务（BA3 AI 链批）

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
`tag_content` 是低优先级 RQ 任务（low 队列），由 `process_content` 在尾部
`enqueue_unique(tag_content, ...)` 投递。

⚠️ RQ pickle 路径：`app.services.pipeline.tag_content` 是 worker 启动期预导入路径
（`app/workers/worker.py`），pipeline.py 需从本模块 re-export 同名函数以保持该路径可用；
新任务将以本模块路径 pickle，两条路径均可在 worker 内解析（pipeline → 本模块 单向导入）。
本模块内**不含**任何被 monkeypatch 的名字，故可安全外移。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Content
from app.db.session import SessionLocal
from app.services.pipeline_common import logger

# W3（2026-10-08）：tags_json 总量上限——agent 镜像会预置 L3 主题（≤10 条），
# LLM 打标再并入新标签；不设上限的话重复处理可无限膨胀（App chips 渲染也吃不消）。
_MAX_TAGS = 20


def merge_tags(existing: list | None, new: list[str]) -> list[str]:
    """合并预置标签与 LLM 新标签：**已有在前、去重保序、封顶 _MAX_TAGS**。

    为什么不能整体覆写（2026-10-08 修正）：W1 起 agent 镜像会在创建时把 L3
    主题预置进 tags_json（App 详情页唯一在展示的分类面）；原实现
    `content.tags_json = list(tags)` 是无条件覆写 ⇒ 镜像行被 worker 处理时
    预置主题被 LLM 标签冲掉——「保存时有、处理完没了」的静默丢失。
    语义：已有的排前面（agent 预置的是用户可见的主题口径），LLM 只补新。
    """
    merged: list[str] = []
    for t in list(existing or []) + list(new):
        if t and t not in merged:
            merged.append(t)
    return merged[:_MAX_TAGS]


def tag_content(content_id: str) -> dict:
    """BA3（AI 链批）：低优先级 RQ 任务 —— 文本/语音类 chips 自由标签

    标签口径（F1 拍板）：AI 动态生成、不限死枚举，写 contents.tags_json（list[str]，
    与 ContentOut 出参对接）；与 SetFit 5 类（content_class）互不相关。
    失败语义（不静默）：LLM 未配置/超时/解析失败 → log 错误原因（AiTaggingError.code），
    tags_json 维持 None；不 re-raise（RQ 视为成功不再重投，重试上限由 ai_tagging 内部
    with_retry 3 次承担）——打标是增强项，不阻塞主内容 done。

    W3（2026-10-08）：写入从「整体覆写」改为 `merge_tags` 合并——已有预置标签
    （agent 镜像的 L3 主题）保留在前，LLM 新标签去重并入。
    """
    from app.services.ai_tagging import AiTaggingError, generate_tags

    db: Session = SessionLocal()
    try:
        content = db.get(Content, content_id)
        if content is None:
            return {"content_id": content_id, "status": "not-found"}
        try:
            tags = generate_tags(content)
        except AiTaggingError as exc:
            logger.warning(
                "AI 打标失败 content=%s: %s %s", content_id, exc.code, exc
            )
            db.rollback()
            return {"content_id": content_id, "status": "failed", "error": exc.code}
        content.tags_json = merge_tags(content.tags_json, tags)
        db.commit()
        logger.info("AI 打标成功 content=%s tags=%s", content_id, content.tags_json)
        return {"content_id": content_id, "status": "succeeded", "tags": content.tags_json}
    finally:
        db.close()
