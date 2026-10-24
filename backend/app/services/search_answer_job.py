"""搜索式回答的**异步**取回通道（A-11 ③ · 2026-10-24）。

## 为什么需要这个模块

搜索是同步 HTTP：召回 ~1s、精排 ~1s、回答再 1~2s ⇒ 端到端已超 P95<3s 验收线。
A-11 原三选项里 ③ 是「先返证据、回答到了再取回」——体验最好，但**不能靠 SSE**：
搜索端点在野 APK 里是普通 POST（无 `enableChunked`），改流式等于要重出包。

可行形态 = **异步结果暂存 + 客户端轮询**（本仓既有先例：分类任务的
`GET /classify/jobs/{id}` + 客户端 `pollClassify`）。这样：

  · **契约向后兼容**：`POST /search` 仍返回完整 `SearchResult`（`answer` 为 None），
    新增的 `answer_job_id` 只有新客户端会看 ⇒ 在野 APK 零影响；
  · **主链路更快**：不等回答就把结果还给用户，P95 不再被回答拖累；
  · **回答不丢**：生成在后台线程里跑，与客户端连不连接无关。

## 存储

Redis（已有连接，见 `app/core/queue.py`）：`yishu:answer:<job_id>` 存 JSON 快照，
TTL 10 分钟（够客户端轮询，又不长期堆垃圾）。用独立 Session 写、失败只 warning ——
**暂存失败不得影响搜索结果本身**（回答是增强项，这是全仓一贯口径）。

## 状态机

  `pending` → （后台生成） → `done`（answer 可能为 None 但 status=done）
                             ↘ `failed`（LLM 不可用/超时/异常；带 reason）

⚠️ `done` + `answer=None` 与 `failed` 是**两回事**：
  前者 = 「证据不足，本来就没有答案」（正常）；后者 = 「系统降级」（可观测）。
   这个区分与 `generate_answer` 的 `failed` 返回值口径一致（见其 docstring）。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

from app.core.queue import redis

logger = logging.getLogger("yishu.search_answer_job")

# 暂存键前缀 + TTL（秒）
_KEY_PREFIX = "yishu:answer:"
_TTL_SECONDS = 600

# 状态取值（与客户端契约一一对应，改动需同步 client/utils/search_api.uts）
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def _key(job_id: str) -> str:
    return _KEY_PREFIX + job_id


def new_job_id() -> str:
    """生成 job id（uuid4 hex，够随机、无路径/注入面）。"""
    return uuid.uuid4().hex


def _write(job_id: str, payload: dict[str, Any]) -> None:
    """写暂存快照。**任何失败只 warning** —— 回答是增强项，不能反过来打断搜索。"""
    try:
        redis.setex(_key(job_id), _TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 —— 缓存写失败不影响主链路
        logger.warning("回答快照写入失败 job=%s: %s", job_id, exc)


def read_job(job_id: str) -> dict[str, Any] | None:
    """读暂存快照；不存在/过期/解析失败一律返回 None（调用方据此 404）。"""
    try:
        raw = redis.get(_key(job_id))
    except Exception as exc:  # noqa: BLE001 —— 读失败按"没有"处理，不抛给端点
        logger.warning("回答快照读取失败 job=%s: %s", job_id, exc)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # 脏数据（格式变更/半截写入）⇒ 当作没有，不把解析异常抛给客户端
        logger.warning("回答快照解析失败 job=%s", job_id)
        return None
    return data if isinstance(data, dict) else None


def start_answer_job(query: str, hits: list) -> str:
    """落 pending 快照并在**后台线程**生成回答；立刻返回 job_id。

    ## 为什么用后台线程而不是 RQ

    RQ 适合「重、可重试、要跨进程」的任务（ASR/照片管线）。本任务：① 总时长 1~2s；
    ② 结果只存 Redis 10 分钟、不落库；③ 失败没有重试价值（LLM 超时重跑也是超时）。
    起一个 RQ worker 轮次反而把延迟从 2s 拉成「排队 + 轮询间隔」。
    线程级失败已 try/except 全吞并写 `failed` 快照 ⇒ 不会静默。

    ## 为什么把 hits 直接闭包进线程

    `generate_answer` 只读 `hits` 的 `text`/`content_id`（已在内存里），不需要 DB Session。
    若传 `db` 进线程，就要处理「Session 跨线程 + 请求结束后被关闭」的问题
    （SQLAlchemy Session 非线程安全）。不传最干净。
    """
    job_id = new_job_id()
    _write(job_id, {"status": STATUS_PENDING, "answer": None, "sources": []})

    def _run() -> None:
        # 函数内 import：避免模块级依赖环（search_answer → embedding → ... 较重），
        # 也让本模块在只读快照时可被轻量导入（例如端点层做参数校验时）。
        import time as _time

        from app.services.llm_ops.search_answer import generate_answer

        t0 = _time.perf_counter()
        try:
            answer, sources, failed = generate_answer(query, hits)
        except Exception as exc:  # noqa: BLE001 —— 绝不让线程异常静默丢失
            logger.exception("回答生成线程异常 job=%s", job_id)
            _write(
                job_id,
                {
                    "status": STATUS_FAILED,
                    "answer": None,
                    "sources": [],
                    "reason": str(exc),
                    "elapsed_ms": int((_time.perf_counter() - t0) * 1000),
                },
            )
            return

        elapsed_ms = int((_time.perf_counter() - t0) * 1000)
        if failed:
            # 系统降级（LLM 不可用/超时）：如实标 failed，让客户端能说"没生成出来"，
            # 而不是伪装成"没有答案"（后者会让用户以为自己没记过这件事）。
            _write(
                job_id,
                {
                    "status": STATUS_FAILED,
                    "answer": None,
                    "sources": [],
                    "reason": "llm-unavailable",
                    "elapsed_ms": elapsed_ms,
                },
            )
            return
        # 正常结束：answer 可能是 None（证据不足）—— 那是"本来就没有"，status 仍是 done。
        _write(
            job_id,
            {
                "status": STATUS_DONE,
                "answer": answer,
                "sources": list(sources or []),
                "elapsed_ms": elapsed_ms,
            },
        )

    threading.Thread(target=_run, name=f"search-answer-{job_id[:8]}", daemon=True).start()
    return job_id
