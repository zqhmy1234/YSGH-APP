"""用户纠错保护（B-07）——分类覆写守卫的判定与留痕

自 `pipeline.py` 拆出（**只搬代码不改逻辑**，2026-10-24），原因：`pipeline.py` 是
「冷冻核心」，本批加守卫后 571 → 672 行，越过 `audit_harness` 结构棘轮的 600 行阈值。
拆法对齐既有 `pipeline_audio` / `pipeline_photo` / `pipeline_tagging` 卫星模块先例：
**被 patch 的名字留在 `pipeline.py`**（`_classify_content` / `_get_classifier`），
无 patch 目标的纯判定件搬到这里，再由 `pipeline.py` re-export 保持
`app.services.pipeline.<name>` 的导入路径可用。

## 缺陷（B-07）

用户手动改正分类后，后续流水线（RQ 重投 / `requeue_job` 超龄重扫 /
`_process_voice` 二次分类 / 重传）会把这个 `content_class` 又写回模型输出，并把
`class_source` 标成 `setfit` ⇒ **用户在用的功能被静默吃掉**。
项目硬约束（AGENTS.md）：「用户操作优先：手动合并/拆分/确认后，自动算法永不覆盖」。

## 取证（写守卫前实测，**不可凭注释猜**）

- `POST /api/v1/corrections`（`api/corrections.py:67-69`）source=active 时**只写**
  `content.content_class = req.new_label`，**从不写 `class_source`**；
- `services/correction.py::record_correction` 只写 `correction_log` + Qdrant，不碰 contents；
- 全仓 `class_source` 的**赋值点只有** `pipeline.py`（`= "setfit"`）。

⇒ 任务卡假设的「纠错后 `class_source == "correction"`」在存量数据里**从不成立**
（历史行是 `None` 或 `"setfit"`）。若守卫只按该判据写，**永不生效**。
故真值来源 = `correction_log` 里该 content 的最新 **active** 行；`class_source` 标记
只作快速路径，且按 `sql/schema.sql:97` 的既定词表取值（`setfit / llm / rule / user`
—— 是 **`user`**，不是 `correction`）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, CorrectionLog
from app.services.pipeline_common import patch_extra

# 与 pipeline.py 同 logger 名（logging.getLogger 按名注册 → 同一 logger 对象）
logger = logging.getLogger("yishu.pipeline")

# 「分类由用户定」的 class_source 标记（schema.sql:97 词表；"correction" 兼容早期口径）
_USER_DECIDED_CLASS_SOURCES = frozenset({"user", "correction"})


def _active_correction_of(db: Session, content: Content) -> str | None:
    """该 content 的最新**主动**纠错标签（无则 None）

    只认 `source == "active"`：echo/org 是被动信号，按 `api/corrections.py:64-66`
    的既定口径**本就不回写权威字段**，也不能反过来保护一个它没写过的值
    （否则一次随手误点的回声会永久冻结这条内容的分类）。
    同内容多次纠错以最后一次为准（`correction_log.created_at DESC, id DESC`）。
    """
    return db.execute(
        select(CorrectionLog.new_label)
        .where(
            CorrectionLog.content_id == content.id,
            CorrectionLog.source == "active",
        )
        .order_by(CorrectionLog.created_at.desc(), CorrectionLog.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _user_decided_class(db: Session, content: Content) -> str | None:
    """若该内容的分类已由用户决定 → 返回保护依据，否则 None

    两个判据（任一命中即保护）：
      ① `content.class_source` 属用户标记集（快速路径，零查询）；
      ② `correction_log` 存在 active 纠错（**真值来源**——存量数据里 ① 恒不成立）。

    兜底方向（B-07 取舍）：查询本身异常时**按已纠错处理**（保护用户数据），并打
    WARNING 留痕。依据同 AGENTS.md「用户操作优先，自动算法永不覆盖」——分类是自动
    产物、错了能重跑；用户纠错被覆盖则用户不会知道要重做一次。
    """
    marker = (content.class_source or "").strip().lower()
    if marker in _USER_DECIDED_CLASS_SOURCES:
        return f"class_source={marker}"
    try:
        label = _active_correction_of(db, content)
    except Exception as exc:  # noqa: BLE001 —— 读纠错表失败不阻断管线，但按已纠错保护
        logger.warning(
            "读取纠错记录失败，按已纠错处理（不覆写）content=%s: %s", content.id, exc
        )
        return "correction-lookup-failed"
    return f"correction_log active→{label}" if label else None


def _record_classification_skipped(content: Content, evidence: str) -> None:
    """留「本次分类被跳过」的可观测痕迹（不改 content_class/class_source/model_version）

    为什么必须留痕（口径同台账 §7.20「降级要可观测，不静默」）：静默 return 会让
    「分类没跑」与「分类跑了但模型没变」在事后完全不可区分——而这条守卫正是会长期
    跳过模型的路径，一旦判据写错（例如误判所有内容都已纠错），没有任何信号。
    `would_be_label` **刻意不记**：那需要真跑 SetFit（实测 ~27s），为一条审计字段
    付这个代价不值；只记判据来源与时间。
    """
    from datetime import UTC, datetime

    patch_extra(
        content,
        classification_skipped={
            "reason": "user-correction",
            "evidence": evidence,
            "at": datetime.now(UTC).isoformat(),
        },
    )
    logger.info(
        "跳过分类（已有用户纠错，自动算法不覆盖）content=%s 依据=%s 保留 content_class=%r",
        content.id,
        evidence,
        content.content_class,
    )
