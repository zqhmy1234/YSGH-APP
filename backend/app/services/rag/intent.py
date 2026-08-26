"""RAG 意图分类与路由（B2 路由 + P1-A 类目路由）

拆包自 services/rag.py（F6，2026-08-27）：
  - _route_query：文本/图片/混合意图（规则词表，确定性零延迟）
  - _classify_query_intent：描述性查询 → 主导类别（P1-A，修复 descriptive 召回缺口）

热路径用词表规则（确定性、零延迟、mock 可用）；LLM/SetFit 分类为后续增强。
"""
from __future__ import annotations

# P1-A 类目路由（2026-08-25）：描述性查询 → 类别过滤，把干扰项挡在召回路外
# （审查报告短板-A："关于做产品的想法"/"让我难过的记录" Top-3 全偏，相关文档
# 排名被 voice/todo 干扰项压低，重排救不回——问题在召回层）。
# 设计约束：SetFit 单条 CPU 推理 ~27s（2026-08-20 实测）远超 P95<3s 门禁，
# 热路径用词表规则（确定性、零延迟、mock 可用）；无主导类别 → 不过滤，
# 空结果自动回退全量（与 NER 回退同模式）。LLM/SetFit 分类为后续增强。
_CLASS_RULES: dict[str, tuple[str, ...]] = {
    "emotion": ("难过", "伤心", "想哭", "开心", "高兴", "委屈", "焦虑", "孤独", "烦躁",
                "沮丧", "感动", "暖心", "心酸", "心情", "情绪", "难受", "郁闷", "后悔",
                "遗憾", "害怕", "紧张", "失望", "惊喜", "emo"),
    "idea": ("想法", "灵感", "创意", "主意", "思路", "点子", "规划", "构思", "产品", "项目"),
    "quote": ("感悟", "道理", "名言", "金句", "座右铭", "语录", "警句", "格言", "心得"),
    "todo": ("记得", "要办", "待办", "提醒", "别忘了", "买", "交", "还", "给", "预约",
              "寄", "退", "取", "回", "开会", "体检", "办", "修", "充", "清理", "更新",
              "发", "付", "缴", "房租", "购物", "采购"),
}


def _classify_query_intent(q: str) -> str | None:
    """类目路由（P1-A）：规则词表命中计数 → 主导类别；无命中/并列 → None（不过滤）"""
    if not q:
        return None
    scores = {cls: sum(1 for kw in kws if kw in q) for cls, kws in _CLASS_RULES.items()}
    best = max(scores.values())
    if best <= 0:
        return None
    winners = [cls for cls, s in scores.items() if s == best]
    return winners[0] if len(winners) == 1 else None


def _route_query(q: str) -> str:
    """查询路由（B2：文本/图片/混合意图；规则词表，确定性零延迟）

    2026-08-25 调研：LLM 路由实测有害——"货车保险杠前面加装的灯叫什么"被
    误判为 image 意图 → 过滤全部 text 文档 → 空结果（探针复现）。规则词表
    覆盖常见图片表达且 route_acc=1.0（PASS），路由保持规则版。
    """
    # 规则兜底：图片意图关键词（B2 路由；词表增强 WP-F：扩充到常见图片表达）
    image_hints = [
        "照片", "图片", "拍的", "截图", "这张", "图里", "壁纸", "表情包",
        "相册", "抓拍", "合照", "自拍", "风景照", "图片里", "照片里",
    ]
    if any(h in q for h in image_hints):
        return "image"
    return "text"
