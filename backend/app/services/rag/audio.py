"""音频检索（B 方案 · 2026-11-01）：录音 → ASR 转写 → 复用文本检索

链路：`POST /api/v1/search/audio`（multipart）→ ASR（复用 `services/external/asr`
双通道）→ 转写文本当查询 → `rag.search`（文字/照片/语音三类结果一并返回）。

为什么在后端做一跳，而不是只靠客户端「先 /asr/transcribe 再 /search」：
客户端两步方案零后端改动、也已接线（`client/utils/search_api.uts::searchByAudio`
打的就是本端点），但①自动化评测/契约校验没有客户端就驱动不了音频检索；
②两步之间失败点翻倍（转写成功、搜索失败时用户只看到一次"搜索失败"）。
后端把 ASR 变成检索链路内部的前置步骤，契约上就是"用音频搜"。

降级口径（与以图搜图 image.py 一致）：
  · 转写**参数/格式类错误**（`AsrError`）→ 上抛给 API 层映射 422/503，
    用户需要知道"这段音频不能用"，不能伪装成"搜不到"；
  · 其它异常（模型/网络）→ 返回 `degraded=True` 的空结果（不抛 500，可重试）；
  · 转写成功但**没有说话**（no_speech/空文本）→ 空结果但 `degraded=False`：
    "你什么都没说"不是系统降级，谎报降级会让客户端弹黄条。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from app.schemas.search import SearchQuery, SearchResult

logger = logging.getLogger("yishu.rag")


def transcribe_for_search(audio_path: str | Path, preferred: str = "auto") -> str:
    """音频 → 查询文本（**测试注入点**：单测 monkeypatch 本函数即可用 fake ASR）

    为什么单独抽一层：`services/external/asr.transcribe` 会真的打百炼/SenseVoice，
    单测与离线评测不能依赖外部 key；抽一层后"假 ASR + 真检索链路"可组合，
    音频检索的结构性断言（返回结构与文本检索同构）才跑得起来。
    `preferred` 透传 ASR 双通道选择（auto/funasr/sensevoice/mock）。
    """
    from app.services.external.asr import transcribe

    result = transcribe(audio_path, preferred=preferred)
    return (result.text or "").strip()


def search_by_audio(
    audio_path: str | Path,
    q: SearchQuery,
    db=None,
    user_id: str | None = None,
    collection: str | None = None,
    preferred: str = "auto",
) -> SearchResult:
    """音频检索主入口：转写 → 文本检索（结果结构与 `rag.search` 完全同构）

    `query/rewritten_query` 填**转写文本**（而不是占位 "[audio]"）：用户需要看到
    "我这句话被听成了什么"，这也是转写质量唯一的用户侧证据；`intent` 用文本检索
    路由的结果（text/image/mixed），客户端因此无需为音频另建一套解析。
    `latency_ms` 计入转写耗时（P95 口径要含 ASR，否则门禁数字会偏乐观）。
    """
    from app.services.external.asr import AsrError

    start = time.perf_counter()

    def _empty(intent: str, degraded: bool, rewritten: str | None = None) -> SearchResult:
        return SearchResult(
            query=q.q,
            rewritten_query=rewritten,
            intent=intent,
            hits=[],
            total=0,
            latency_ms=int((time.perf_counter() - start) * 1000),
            degraded=degraded,
        )

    try:
        text = transcribe_for_search(audio_path, preferred=preferred)
    except AsrError:
        # 参数/格式/服务类错误交给 API 层映射（422 ASR_001 / 503 ASR_002）
        raise
    except Exception as exc:  # noqa: BLE001 —— 转写异常降级为空结果，不抛 500
        logger.warning("音频检索：转写失败，返回降级空结果: %s", exc)
        return _empty(intent="text", degraded=True)

    if not text:
        logger.info("音频检索：转写结果为空（无声/静音），返回空结果")
        return _empty(intent="text", degraded=False)

    # 延迟 import：rag 包 __init__ 会 import 本模块，模块级 import 会成环
    from app.services.rag import search as _text_search

    # 保留调用方显式 intent（audio 端点默认 auto）；只替换查询文本
    sub_query = q.model_copy(update={"q": text})
    result = _text_search(sub_query, db=db, user_id=user_id, collection=collection)
    # 转写 + 检索的总耗时；query 已是转写文本（SearchResult.query 由 search 填 sub_query.q）
    return result.model_copy(update={"latency_ms": int((time.perf_counter() - start) * 1000)})
