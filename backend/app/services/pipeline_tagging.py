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


def tag_content(content_id: str) -> dict:
    """BA3（AI 链批）：低优先级 RQ 任务 —— 文本/语音类 chips 自由标签

    标签口径（F1 拍板）：AI 动态生成、不限死枚举，写 contents.tags_json（list[str]，
    与 ContentOut 出参对接）；与 SetFit 5 类（content_class）互不相关。
    失败语义（不静默）：LLM 未配置/超时/解析失败 → log 错误原因（AiTaggingError.code），
    tags_json 维持 None；不 re-raise（RQ 视为成功不再重投，重试上限由 ai_tagging 内部
    with_retry 3 次承担）——打标是增强项，不阻塞主内容 done。
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
        content.tags_json = list(tags)
        db.commit()
        logger.info("AI 打标成功 content=%s tags=%s", content_id, tags)
        return {"content_id": content_id, "status": "succeeded", "tags": tags}
    finally:
        db.close()
