"""用户照片的**图片内容审核**（D-24 · 2026-10-25 用户拍板「只审增量、不回填存量」）。

## 这个模块存在的理由（一个被忽视的上架阻断项）

实测（2026-10-25）：
  · 全仓 `check_image` **只被 `wechat/service.py:237` 一处调用** ⇒ 微信来图会审，
    但**用户自己上传的 4154 张照片一张都没过图片审核**；
  · `contents.sensitive_status` 的默认值是 `"正常"`（`db/models/content.py:62` 的
    server_default）⇒ 那些照片显示的"正常"是**出厂默认值，不是审核结论**；
  · 现有唯一的相关钩子 `mark_sensitive_on_ingest` 看的是 `content.text` ——
    也就是 caption/OCR 出来的**文字**，属"文本审核"而非"图片审核"。

⇒ 两者合起来会造成一种最坏的错觉：**以为审了，其实没审**。应用商店对 UGC 图片
有审核要求，这个缺口是真实的上架阻断项。

## 为什么挂在 caption 之前（顺序很重要）

本钩子在 `pipeline_photo` 拿到 `image_path` 之后、调 Qwen3-VL 出 caption **之前**执行。
理由有两条：
  ① **省成本**：违规图不必再花一次 VL 往返（每张约 1~3s + tokens）；
  ② **避免污染**：违规图不该产生 caption 并被索引进 Qdrant（否则它会出现在检索结果里）。

## 诚实边界（三条，不要读成"已经全审了"）

1. **只在 `storage_backend == "cos"` 时生效**：腾讯 CI 的 `image_audit` 要的是
   **COS 对象 key**（它自己去拉图）。本地/测试用 `fs` 后端**没有公网对象**，
   故本钩子会 `skipped` 并**显式日志**——不是静默跳过。
2. **只审增量，不回溯存量**（用户 2026-10-25 拍板）：4154 张存量不重跑，
   历史 `sensitive_status` 仍可能是出厂默认值。**不得据此宣称"存量已审"**。
3. **fail-open 但可观测**：审核服务异常时**不阻断照片入库**（照片是用户自己的私有记忆，
   为一次外部服务抖动把用户的照片卡在 `failed` 是不划算的），
   但必须 `logger.warning` + 打 `extra.image_moderation` 痕迹，便于事后补审。

## 与两套审核体系的关系

本仓有两套：
  · `llm_ops/moderate`（规则 + Qwen3Guard LLM）—— **文本**主链路；
  · `external/content_safety`（腾讯 CI / 阿里云）—— 有 `check_text`/`check_image`。
本模块走**后者**的 `check_image`（它是唯一实现了"图片审核"的那条链），
不改动前者。统一出口属 D-24 的 B 方案，本批不做。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("yishu.pipeline_ext.media_safety")

# 命中审核时的 sensitive_status 取值。与 `pipeline_ext/sensitive.py` 一致用「待复核」
# （语义见其 `:188` 注释：30 天降级逻辑不该把"审核命中"当敏感话题计数）。
_STATUS_REVIEW = "待复核"


def moderate_photo_on_ingest(db: Any, content: Any, cos_key: str | None) -> str:
    """照片入库时的图片审核。返回结论：`allow` / `review` / `skipped` / `error`。

    **任何异常都不向上抛** —— 见模块文档「fail-open 但可观测」。

    @param cos_key 照片的 COS 对象 key；为空 ⇒ 直接 `skipped`（没有可审的对象）
    """
    from app.core.config import settings

    # 开关：默认开（用户已拍板）；关掉时**不静默**，日志写明"本次未审图片"
    if not getattr(settings, "content_image_moderation_enabled", True):
        logger.info("图片审核已关闭（CONTENT_IMAGE_MODERATION_ENABLED=false）content=%s", content.id)
        return "skipped"

    if not cos_key:
        # fs 后端 / 尚未落 COS：没有可审对象。**不是"审过了"**，如实记录。
        logger.info(
            "图片审核跳过（无 cos_key，storage=%s）content=%s —— 该照片未过图片审核",
            settings.storage_backend,
            content.id,
        )
        return "skipped"

    try:
        from app.services.external.content_safety import get_content_safety

        adapter = get_content_safety()
        result = adapter.check_image(cos_key)
    except Exception as exc:  # noqa: BLE001 —— 审核不可用不得阻断入库
        logger.warning(
            "图片审核调用失败（照片仍入库，但**未审**）content=%s: %s", content.id, exc
        )
        _trace(content, "error")
        return "error"

    passed = bool(result.get("pass"))
    labels = result.get("labels") or []
    provider = result.get("provider") or "?"

    if passed:
        _trace(content, "allow", provider=provider)
        return "allow"

    # 命中：标「待复核」+ 留标签痕迹（**不删照片** —— 用户自己的记忆，删是更重的动作；
    # 待复核的内容会被主动提及路径（回响/推送）跳过，语义与 sensitive.py 一致）。
    content.sensitive_status = _STATUS_REVIEW
    _trace(content, "review", provider=provider, labels=labels, reason=result.get("reason") or "")
    logger.warning(
        "图片审核命中，标记待复核 content=%s provider=%s labels=%s", content.id, provider, labels
    )
    return "review"


def _trace(content: Any, verdict: str, **fields: Any) -> None:
    """把审核结论写进 `content.extra.image_moderation`（可观测 + 便于事后补审）。

    为什么放 extra 而不是新增列：extra 是既有 JSONB，随管线进 Qdrant payload；
    新增列要迁移，而本项还不值得一次迁移。**若将来要按审核状态批量补审，
    应升为正式列**（那时 extra 里已有数据可回填）。
    """
    try:
        from app.services.pipeline_common import patch_extra

        patch_extra(content, image_moderation={"verdict": verdict, **fields})
    except Exception:  # noqa: BLE001 —— 痕迹写不进去不影响主流程
        logger.debug("图片审核痕迹写入失败 content=%s", getattr(content, "id", "?"))
