"""阿里云百炼客户端（S1-03 外部 API 接入 · 决策 #2/#12）

覆盖三类能力（M1 依赖）：
  1. qwen-flash 文本：RAG 查询改写/路由/精排（B2）
  2. Qwen3-VL 图片塔：图片 → 语义描述 → 向量化（B2 图片检索）
  3. 护栏：内容安全审核（B5b，fail-safe：百炼不可用时默认拒发而非放行）

Mock 模式（MOCK_EXTERNAL_AI=true 或未配 key）：
  - 零费用、确定性输出，契约消费方可本地联调
  - 与真实响应同构（同字段），切真实 key 无代码改动
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.services.external.retry import with_retry

logger = logging.getLogger("yishu.external")

# 模型名（百炼）
QWEN_FLASH = "qwen-flash"            # 文本：改写/路由/精排
QWEN_VL = "qwen3-vl-plus"            # 图片塔
QWEN_GUARD = "qwen-flash"            # 护栏（内容安全）——qwen-guard 不存在（400 实测），B5b 托管护栏接入前用 qwen-flash
# R-4 真·图片塔（多模态 embedding）。**必须用带日期的快照**：无版本号的
# tongyi-embedding-vision-plus 默认 1152 维，放不进本仓 image_vec(1024) 槽位；
# 快照版支持 dimension 参数（官方支持 64~1152）。见 image_tower.py 头部说明。
QWEN_VISION_EMBED = "tongyi-embedding-vision-plus-2026-03-06"
# 注：B5b 定稿为百炼托管护栏（qwen_response_check，header X-DashScope-DataInspection
# 自动匹配），qwen-guard 为 chat 模型兜底；接入托管前先用规则预检 + chat 模型双保险。

# 护栏判定：返回文本含此关键词视为"拦截"
_BLOCK_MARKERS = ("block", "refuse", "拒", "不合法", "违规", "有害")

_REWRITE_SYSTEM = (
    "你是记忆整理 App 的查询改写器。判断输入查询是否需要改写，按规则输出：\n"
    "1. 如果查询已经是简洁准确的关键词（实体+意图清晰，如'买牛奶''马拉松''驼奶的功效'），"
    "   原样返回，不要做任何改动。\n"
    "2. 仅在以下情况改写：①存在错别字/拼音错误（'买牛乃'→'买牛奶'）；"
    "②口语化/指代不明（'上次说的那个''帮我找一下'）；③描述性查询需要扩展为"
    "记忆内容风格的表达（'关于做产品的想法'→'产品创意想法'）。\n"
    "改写必须保持原意，不编造新实体。只输出查询本身，不要解释。"
)

_ROUTE_SYSTEM = (
    "你是查询路由器。判断查询意图，只输出一个词："
    "image（找照片/图片/截图）、text（找文字/笔记/碎片）、mixed（两者都要）。"
)

_GUARD_SYSTEM = (
    "你是内容安全审核员。判断输入是否含违法、色情、暴力、诈骗等违规内容。"
    "合规输出 PASS；违规输出 BLOCK。只输出一个词。"
)


def _llm_available() -> bool:
    """LLM 可用判定：非 mock 且**所配供应商**已配 key（2026-10-15 · 台账 §7.27）

    ⚠️ 刻意不看"另一家"的 key：切到 deepseek 后，即便 dashscope key 有效也不得算可用
    （否则护栏会拿着无效的 dashscope key 去调用 ⇒ fail-closed 拒发全部内容）。
    """
    if settings.mock_external_ai:
        return False
    if settings.llm_provider == "deepseek":
        return bool(settings.deepseek_api_key)
    return bool(settings.dashscope_api_key)


def _ensure_api_key() -> None:
    """同步 settings 到 dashscope SDK 全局 api_key（2026-08-25 排查修复）

    dashscope SDK 只认 env/全局 api_key，不读本项目 settings：
    机器遗留旧格式 DASHSCOPE_API_KEY（sk-4980...）会覆盖 .env 的 sk-ws- 值，
    导致 403 Workspace access denied（旧 key + workspace 头不匹配）。
    settings 本身 env 优先、.env 兜底，这里显式同步即可根治。
    """
    import dashscope

    if settings.dashscope_api_key and dashscope.api_key != settings.dashscope_api_key:
        dashscope.api_key = settings.dashscope_api_key


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def _chat_text(system: str, user: str, model: str = QWEN_FLASH) -> str:
    """文本对话（同步），失败抛异常由调用方降级。

    带统一重试（网络抖动 3 次指数退避，见 external/retry.py）。

    2026-10-15（台账 §7.27）：按 `LLM_PROVIDER` 分派 —— `dashscope`（默认，qwen-flash，
    经 dashscope SDK）或 `deepseek`（任何 OpenAI 兼容端点，见 `external/llm_openai.py`）。
    切到 deepseek 时**`model` 形参（qwen 专用名）被忽略**、改用 `DEEPSEEK_MODEL`：
    调用点无需改动（它们传的 `QWEN_FLASH` 只是默认值）。
    ⚠️ 视觉调用不经此处（`image_caption` 恒走 DashScope），故无"DeepSeek 无视觉模型"问题。
    """
    if settings.llm_provider == "deepseek":
        from app.services.external.llm_openai import chat_text as _openai_chat_text

        return _openai_chat_text(
            system,
            user,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
    from dashscope import Generation

    _ensure_api_key()
    resp = Generation.call(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        result_format="message",
        workspace=settings.dashscope_workspace_id or None,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"dashscope {model} 调用失败: {resp.status_code} {resp.message}")
    return resp.output.choices[0].message.content


def rewrite_query(q: str) -> str:
    """LLM 查询改写（RAG B2；未配置时由调用方走规则兜底）"""
    if not _llm_available():
        raise RuntimeError("百炼未配置（MOCK 或缺 key），走规则兜底")
    return _chat_text(_REWRITE_SYSTEM, q).strip()


def route_query(q: str) -> str:
    """LLM 查询路由（image/text/mixed）"""
    if not _llm_available():
        raise RuntimeError("百炼未配置，走规则兜底")
    answer = _chat_text(_ROUTE_SYSTEM, q).strip().lower()
    for cand in ("image", "mixed", "text"):
        if cand in answer:
            return cand
    return "text"


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def image_caption(image_path: str, prompt: str = "用一句话中文描述这张图片的内容和场景。") -> str:
    """Qwen3-VL 图片塔：图片 → 语义描述（供向量化索引/检索）

    带统一重试（网络抖动 3 次指数退避，AGENTS.md #13 教训落地）。
    """
    if not _llm_available():
        raise RuntimeError("百炼未配置，图片塔不可用（需 DASHSCOPE_API_KEY）")
    from dashscope import MultiModalConversation

    _ensure_api_key()
    resp = MultiModalConversation.call(
        model=QWEN_VL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": f"file://{image_path}"},
                    {"text": prompt},
                ],
            }
        ],
        workspace=settings.dashscope_workspace_id or None,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"dashscope {QWEN_VL} 调用失败: {resp.status_code} {resp.message}")
    parts = resp.output.choices[0].message.content
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


def _extract_embedding(resp) -> list[float]:
    """从多模态向量响应里取第一条向量。

    响应形态（官方文档）：`output.embeddings[i]` = `{index, embedding, type}`，
    `type` ∈ text/image/video/multi_images。
    """
    items = resp.output["embeddings"]
    if not items:
        raise RuntimeError("dashscope 多模态向量返回空 embeddings")
    return [float(x) for x in items[0]["embedding"]]


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def embed_image(image_path: str, *, dimension: int = 1024) -> list[float]:
    """R-4 真图片塔：图片 → 向量（tongyi-embedding-vision-plus 多模态 embedding）

    ⚠️ **本函数尚未在真实账号上验证过**（百炼「多模态向量」未开通）。开通后须先核对：
      ① 该接口是否接受 `workspace=`（MultiModalConversation 接受，embedding 侧待确认）；
      ② `dimension=1024` 是否被接受、返回长度是否恰为 1024（**零切换成立的前提**）；
      ③ `input=[{"image": "file://..."}]` 的本地文件 URL 形态是否支持；
      ④ 文本输入与图片输入是否落在同一向量空间（`embed_text_in_vision` 依赖此点）。
    """
    if not _llm_available():
        raise RuntimeError("百炼未配置，图片塔不可用（需 DASHSCOPE_API_KEY）")
    from dashscope import MultiModalEmbedding

    _ensure_api_key()
    resp = MultiModalEmbedding.call(
        model=QWEN_VISION_EMBED,
        input=[{"image": f"file://{image_path}"}],
        dimension=dimension,
        workspace=settings.dashscope_workspace_id or None,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"dashscope {QWEN_VISION_EMBED} 调用失败: {resp.status_code} {resp.message}")
    return _extract_embedding(resp)


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def embed_text_in_vision(text: str, *, dimension: int = 1024) -> list[float]:
    """真图片塔的**文本通道** —— 与 `embed_image` 同一模型、同一空间。

    用途：索引侧拿不到图片文件时（生产 `_index_after_commit` 时临时文件已删），
    用 caption 走文本输入补齐，避免该照片永久不可搜图。**不是跨空间混入。**
    同上：未在真实账号验证（见 `embed_image` 的核对清单）。
    """
    if not _llm_available():
        raise RuntimeError("百炼未配置，图片塔不可用（需 DASHSCOPE_API_KEY）")
    from dashscope import MultiModalEmbedding

    _ensure_api_key()
    resp = MultiModalEmbedding.call(
        model=QWEN_VISION_EMBED,
        input=[{"text": text}],
        dimension=dimension,
        workspace=settings.dashscope_workspace_id or None,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"dashscope {QWEN_VISION_EMBED} 调用失败: {resp.status_code} {resp.message}")
    return _extract_embedding(resp)


def moderate(text: str) -> dict:
    """内容安全护栏（B5b）：返回 {"pass": bool, "reason": str}

    两级过滤（B5-b-3）：规则预检（所有模式生效）→ 百炼托管检测（真实模式）。
    fail-safe（决策 #12）：真实模式下百炼不可用/异常 → 默认拦截（拒发）。
    修复（审查 MAJOR）：mock 模式原先无条件放行（fail-open），现规则命中即拦截。
    """
    # 第一级：规则预检（免费、确定性、所有模式生效；2026-08-20 升级为开源词库+两档）
    from app.services.external.sensitive_words import check_sensitive

    rule = check_sensitive(text)
    if rule["action"] == "reject":
        return {"pass": False, "reason": rule["reason"], "action": "reject",
                "matched": rule["matched"], "categories": rule["categories"]}
    if rule["action"] == "mask":
        # 号码/广告词打码：LLM 层对打码后文本继续检测（原内容不落库）
        if not _llm_available():
            # 审查 CRITICAL 修复（用户拍板：默认拒发）：生产模式未配 key → fail-closed，
            # 与 SAF-005"百炼不可用默认拒发"一致；开发/测试 mock 保持放行（本地联调）。
            if settings.app_env == "production":
                logger.error("生产环境未配置 DASHSCOPE_API_KEY，护栏默认拒发（fail-closed）")
                return {"pass": False, "reason": "guard-unavailable", "action": "reject",
                        "matched": rule["matched"], "categories": rule["categories"]}
            return {"pass": True, "reason": rule["reason"], "action": "mask",
                    "masked_text": rule["masked_text"], "matched": rule["matched"],
                    "categories": rule["categories"]}
        try:
            answer = _chat_text(_GUARD_SYSTEM, rule["masked_text"], model=QWEN_GUARD).strip().upper()
            blocked = any(m in answer for m in _BLOCK_MARKERS) or answer == "BLOCK"
            if blocked:
                return {"pass": False, "reason": "guard", "action": "reject",
                        "matched": rule["matched"], "categories": rule["categories"]}
        except Exception as exc:  # noqa: BLE001 —— fail-safe：不可用即拦截
            logger.warning("护栏调用失败，fail-safe 拦截: %s", exc)
            return {"pass": False, "reason": "guard-unavailable", "action": "reject",
                    "matched": rule["matched"], "categories": rule["categories"]}
        return {"pass": True, "reason": rule["reason"], "action": "mask",
                "masked_text": rule["masked_text"], "matched": rule["matched"],
                "categories": rule["categories"]}

    if not _llm_available():
        # 审查 CRITICAL 修复（用户拍板：默认拒发）：生产模式未配 key → fail-closed；
        # 开发/测试 mock 保持放行（本地联调契约）。
        if settings.app_env == "production":
            logger.error("生产环境未配置 DASHSCOPE_API_KEY，护栏默认拒发（fail-closed）")
            # D10-1（2026-09-25 P0）：fail-closed 分支**必须带 `action`** ——
            # 调用方（contents / photo_content）按 `action` 分派，缺键 ⇒ 既非 reject
            # 也非 mask ⇒ 两条 if 均不成立 ⇒ **明确判"拒发"却原文入库**。
            return {"pass": False, "reason": "guard-unavailable", "action": "reject",
                    "matched": rule["matched"], "categories": rule["categories"]}
        logger.info("护栏 mock 放行（未配置百炼，规则预检已过）")
        return {"pass": True, "reason": "mock", "action": "allow"}
    try:
        answer = _chat_text(_GUARD_SYSTEM, text, model=QWEN_GUARD).strip().upper()
        blocked = any(m in answer for m in _BLOCK_MARKERS) or answer == "BLOCK"
        return {"pass": not blocked, "reason": "guard" if blocked else "",
                "action": "reject" if blocked else "allow"}
    except Exception as exc:  # noqa: BLE001 —— fail-safe：不可用即拦截
        logger.warning("护栏调用失败，fail-safe 拦截: %s", exc)
        return {"pass": False, "reason": "guard-unavailable", "action": "reject"}
