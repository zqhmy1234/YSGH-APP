"""guard.py —— B5b 域：护栏扩展（违规词回流 / 事件级敏感 LLM 补漏 / 检测器抽象）

任务归属：Wave 1 Agent C（B5b 护栏域）独占本文件。

内容：
1. 可插拔检测器抽象（B5b-7）：规则 / 百炼托管两实现轻量接口，不引入重框架
   （无注册表/无 DI 容器），需要时工厂函数 + 列表即可换实现。
   【B5b 选型结论（2026-08-14 调研，见 05b_安全护栏_B5b.md §调研结论）】
   自部署护栏（NeMo Guardrails / Qwen3Guard 8B Q4）淘汰：CPU 自部署 5-10s/次
   灾难且已有百炼托管可用；MVP 挂百炼托管（qwen_response_check，按 token 计费，
   零部署零 GPU），flash 自检降为托管不可用时的兜底；可插拔接口保留，未来私有化
   可再加回自部署实现。
2. detect_event_sensitive：事件级敏感 LLM 补漏（规则未命中 → qwen-flash 分类，
   抓\"他说以后别联系了\"类表达；mock/未配 key → []，静默降级）。
3. reflow_violation_words：违规词回流（检测违规 → sensitive_words level=3，
   自动入规则表；进程内经 sensitive_words.add_violation_word 热加入）。

现有规则预检 + moderate 已由 base.moderate 提供，本模块只做扩展。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.db.models import SensitiveWord
from app.services.llm_ops.base import chat_text, llm_available

logger = logging.getLogger("yishu.guard")

# 事件级敏感类别（与 external/sensitive_words 的 _EVENT_CATEGORY_FILES 对齐）
EVENT_CATEGORIES = ("分手", "离世", "健康", "金钱", "家庭矛盾", "法律纠纷", "意外创伤")

# LLM 分类系统提示：只输出类别名，一行一个（容错解析：按类别名子串匹配）
_EVENT_GUARD_SYSTEM = (
    "你是回忆类应用的敏感话题识别器。判断用户叙述是否涉及以下敏感事件类别之一："
    f"{'、'.join(EVENT_CATEGORIES)}。"
    "只输出命中的类别名，每行一个；完全不涉及则输出：无。"
    "不要输出任何解释或标点。"
)

# 回流词默认类别种子（LLM 判敏感且规则未覆盖 → 固化为规则词，防重复调 LLM）
_CATEGORY_SEED_WORDS = {
    "分手": ["分手", "别联系了"],
    "离世": ["去世", "离世"],
    "健康": ["生病", "住院"],
    "金钱": ["欠债", "失业"],
    "家庭矛盾": ["吵架", "家暴"],
    "法律纠纷": ["官司", "拘留"],
    "意外创伤": ["车祸", "受伤"],
}


@dataclass
class DetectionResult:
    """检测结果（跨实现统一结构）"""

    detector: str          # 实现名：rule / managed
    pass_: bool            # True = 未命中敏感（放行）
    categories: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    detail: str = ""


class SensitiveDetector(ABC):
    """检测器抽象基类（轻量：三方法即可接入，不引入重框架）"""

    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """是否可用（如托管护栏需已配 key / 已接线）"""

    @abstractmethod
    def detect(self, text: str) -> DetectionResult:
        """检测文本 → DetectionResult（实现内部保证不抛异常，失败视为可用性降级）"""


class RuleDetector(SensitiveDetector):
    """实现①：规则层（本地事件词表 + 运行时回流词；零模型、确定性、所有模式生效）"""

    name = "rule"

    def available(self) -> bool:
        return True

    def detect(self, text: str) -> DetectionResult:
        from app.services.external.sensitive_words import check_event_sensitive

        r = check_event_sensitive(text)
        return DetectionResult(
            detector=self.name,
            pass_=r["pass"],
            categories=r["categories"],
            matched=r["matched"],
            detail="event-rule",
        )


class ManagedDetector(SensitiveDetector):
    """实现②：百炼托管护栏（预留——dashscope.py:25 注释：正式为 qwen_response_check /
    X-DashScope-DataInspection header，当前未接线，先用 qwen-flash chat 补漏）"""

    name = "managed"

    def available(self) -> bool:
        return llm_available()

    def detect(self, text: str) -> DetectionResult:
        if not self.available():
            return DetectionResult(detector=self.name, pass_=True, detail="unavailable")
        try:
            answer = chat_text(_EVENT_GUARD_SYSTEM, text).strip()
            categories = _parse_categories(answer)
            return DetectionResult(
                detector=self.name,
                pass_=not categories,
                categories=categories,
                matched=[],
                detail="llm",
            )
        except Exception as exc:  # noqa: BLE001 —— 补漏失败静默降级（不阻断入库）
            logger.warning("事件敏感 LLM 补漏失败，降级放行: %s", exc)
            return DetectionResult(detector=self.name, pass_=True, detail=f"error:{exc}")


def _parse_categories(answer: str) -> list[str]:
    """解析 LLM 输出 → 命中类别列表（容错：子串匹配 + 去重 + 顺序稳定）"""
    if not answer:
        return []
    found: list[str] = []
    for cat in EVENT_CATEGORIES:
        if cat in answer and cat not in found:
            found.append(cat)
    return found


def detect_event_sensitive(text: str) -> list[str]:
    """事件级敏感 LLM 补漏入口：规则未命中时调用，返回类别名列表。

    - mock / 未配 key → []（规则层已兜底，本地联调确定性）
    - LLM 异常 → []（静默降级，不阻断内容入库）
    """
    result = ManagedDetector().detect(text)
    return result.categories


def reflow_violation_words(
    db,
    words: list[str],
    category: str | None = None,
    user_id: str | None = None,
) -> int:
    """违规词回流（B5b）：检测违规 → 写 sensitive_words(level=3) 自动入规则表。

    - moderate 命中（reject）→ 命中词回流（全局 level=3，user_id=None）
    - LLM 判敏感且规则未覆盖 → 该类别种子词回流（防重复调 LLM）
    已存在 (word, user_id) 的行跳过（唯一约束幂等）；进程内热加入立即生效。
    返回实际插入条数。
    """
    from sqlalchemy import select

    inserted = 0
    for raw in words:
        word = (raw or "").strip()
        if not word:
            continue
        exists = db.execute(
            select(SensitiveWord.id).where(
                SensitiveWord.word == word,
                SensitiveWord.user_id == user_id,
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(SensitiveWord(word=word, level=3, user_id=user_id))
        from app.services.external.sensitive_words import add_violation_word

        add_violation_word(word, category)
        inserted += 1
    if inserted:
        db.commit()
    return inserted


def reflow_llm_categories(db, categories: list[str]) -> int:
    """LLM 判敏感且规则未覆盖 → 类别种子词回流（level=3 全局）"""
    total = 0
    for cat in categories:
        for w in _CATEGORY_SEED_WORDS.get(cat, []):
            total += reflow_violation_words(db, [w], category=cat)
    return total
