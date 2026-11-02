"""RAG 意图分类与路由（B2 路由 + P1-A 类目路由）

拆包自 services/rag.py（F6，2026-08-27）：
  - _route_query：文本/图片/混合意图（规则词表，确定性零延迟）
  - _resolve_intent：显式 intent 覆盖路由（C 方案，2026-11-01）
  - _classify_query_intent：描述性查询 → 主导类别（P1-A，修复 descriptive 召回缺口）

热路径用词表规则（确定性、零延迟、mock 可用）；LLM/SetFit 分类为后续增强。
"""
from __future__ import annotations

import re

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

# 图片意图关键词（B2 路由；词表增强 WP-F：扩充到常见图片表达）。
# 注意：命中只说明「用户提到了图片」，**不等于**「只要图片」（C 方案 2026-11-01）——
# 是否升级为 mixed 由下方 _route_query 的"实词残留"判据决定。
_IMAGE_HINTS: tuple[str, ...] = (
    "照片", "图片", "拍的", "截图", "这张", "图里", "壁纸", "表情包",
    "相册", "抓拍", "合照", "自拍", "风景照", "图片里", "照片里",
)

# 「指向图片本身」的虚词/指示词（C 方案 2026-11-01）：这些词被移除后若查询里不再剩
# 任何实词，说明用户是在「找那张图」（纯图搜），而不是「找图里提到的内容」。
# 为什么需要：旧实现只要命中 _IMAGE_HINTS 就锁死 content_types=[photo]，
# "照片里的猫是什么时候拍的" 这类**图片只是语境**的查询会被文字/语音条件排除，
# 三模态互搜在产品上永远做不到（本次跨模态任务的根因之一）。
_POINTER_WORDS: tuple[str, ...] = (
    "帮我", "帮忙", "找找", "查找", "找", "看看", "看一下", "一下", "有哪些", "哪些",
    "什么", "什么时候", "哪个", "哪儿", "哪里", "这个", "那个", "那张", "这些", "那些",
    "的", "里", "中", "上", "我", "是", "有", "张", "个", "些", "吗", "呢", "啊",
    "啦", "了", "和", "跟", "与", "还有", "以及",
)
_POINTER_RE = re.compile("|".join(sorted(_POINTER_WORDS, key=len, reverse=True)))
# 数词不计入"实词"：'三张照片' 仍是纯图搜，不是要找"三"这个内容
_NUMBER_CHARS = "零一二三四五六七八九十百千万亿两几"


def _route_query(q: str) -> str:
    """查询路由（B2 + C 方案 2026-11-01：text / image / mixed 三态）

    2026-08-25 调研：LLM 路由实测有害——"货车保险杠前面加装的灯叫什么"被
    误判为 image 意图 → 过滤全部 text 文档 → 空结果（探针复现）。规则词表
    覆盖常见图片表达且 route_acc=1.0（PASS），路由保持规则版。

    2026-11-01（跨模态 C 方案）：路由**不再是二值**。原来的 `if 图词 → image`
    让 `rag/__init__.py` 里三处 `if intent == "mixed"` 的双路融合成为死代码，
    检索层又把 image 解释成"只搜照片"，于是任何含"照片/截图/相册"的查询都
    只能拿到照片。现口径：

      · `image` —— 图词之外**没有实词残留**（"这张照片"/"我的相册"/"三张截图"）：
        用户就是在找那张图，保持单路 + 照片过滤（精确性优先，见 _resolve_intent 注释）；
      · `mixed` —— 图词之外还有实词（"照片里的猫叫什么"/"截图上写的那个地址"）：
        图只是语境，答案可能在文字/语音里 ⇒ 交给检索层双路合并（照片路 + 全量路）；
      · `text`  —— 无图词（行为与改动前逐字一致）。

    判据用"移除图词 + 虚词后是否还剩实词"，而不是再维护一张"图片表达例外表"：
    词表只增不减时例外表必然漂移，而虚词表是稳定的语言层事实。
    """
    if not q:
        return "text"
    if not any(hint in q for hint in _IMAGE_HINTS):
        return "text"
    residue = q
    # 长词优先移除（"照片里" 必须先于 "照片"），避免 "照片里" 被拆成"里"残留
    for hint in sorted(_IMAGE_HINTS, key=len, reverse=True):
        residue = residue.replace(hint, "")
    residue = _POINTER_RE.sub("", residue)
    # 只剩标点/数词 → 纯图搜；还有实词（中文/字母/数字串）→ 图片只是语境
    meaningful = "".join(ch for ch in residue if ch.isalnum() and ch not in _NUMBER_CHARS)
    return "mixed" if meaningful else "image"


def _resolve_intent(requested: str | None, routed: str) -> str:
    """显式 intent 覆盖路由（C 方案 2026-11-01）

    契约（`schemas/search.py::SearchQuery.intent`）三态：`auto|text|image`。
      · `auto`（默认）→ 用规则路由结果（text / image / mixed）；
      · `text` → 强制全量检索（不做照片过滤，也不做双路合并）；
      · `image` → **强制只回照片**（硬过滤 content_types=[photo]）。

    为什么保留 `image` 这条窄路：混合双路在"我只要照片"的场景会带出文字/语音
    噪声，反而降低精确性；显式传入即视为用户明确的收窄要求，必须尊重（回归测试
    `test_explicit_image_intent_stays_photo_only` 锁住该语义）。
    未识别的值（旧客户端/脏数据）按 auto 处理，保持 fail-open。
    """
    if requested in ("text", "image"):
        return requested
    return routed


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
