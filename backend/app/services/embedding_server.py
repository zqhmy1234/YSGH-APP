"""共享 embedding 服务（BGE-M3 单实例 + 动态批处理）。

## 为什么要有这个服务（2026-10-31 压测结论）

压测三次实测：

| 条件 | 消费吞吐 | 说明 |
|---|---|---|
| 1 个 worker | 0.22 条/秒 | 每进程加载一份 BGE-M3（fp16 ≈1.2GB） |
| **4 个 worker** | **0.11 条/秒** ❌ | worker 日志 `os error 1455`（页面文件不足）⇒ 模型加载失败、全部降级 |

即：**加进程不但无效，反而更慢**——因为每个进程都独立加载一整套模型。
真正的瓶颈不是队列解耦，而是「模型被 N 个进程各加载一份」。

## 设计（百万请求级）

1. **一份模型**：模型只在本进程加载一次并常驻（预热），worker 内存从 ~1.2GB 降到几十 MB
   ⇒ worker 数量与内存脱钩，16GB 机器可跑 6~10 个 worker。
2. **动态批处理**：请求进队列，攒 `_MAX_BATCH` 条或 `_MAX_WAIT_MS` 毫秒即合并推理。
   单条推理是 CPU 密集且几乎满载 → 批量能把吞吐拉高数倍，同时把尾延迟压住。
3. **线程池推理**：`torch.set_num_threads` 限核，避免 CPU 版默认开满核导致内存峰值超限。
4. **向量缓存**（可选，进程内 LRU）：相同文本（照片 caption / 重复短句）命中即返回，
   实际推理量可降一个数量级。

## 端点

- `POST /embed`  body `{"texts": [...], "want": ["dense","sparse"]}`
  → `{"dense": [[...]], "sparse": [{...}], "cached": n}`
- `GET /health`  就绪与模型是否已加载
- `GET /stats`   批次数 / 平均批大小 / 命中率（用来判断批处理是否生效）

## 启动

```
cd backend
$env:PYTHONPATH='.'
python -m app.services.embedding_server --port 8020
```
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future

logger = logging.getLogger("yishu.embed.server")

_MAX_BATCH = 32
_MAX_WAIT_MS = 10
_CACHE_MAX = 4096


class _Batcher:
    """把零散请求攒成批，交给单一推理线程执行。"""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._cache: OrderedDict[str, object] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = {"batches": 0, "items": 0, "cache_hits": 0, "cache_miss": 0}
        self._thread = threading.Thread(target=self._loop, name="embed-batcher", daemon=True)

    def start(self) -> None:
        # 预热：先跑一次，避免首个真实请求承担模型加载（否则客户端超时）
        from app.services.embedding import encode_dense

        t0 = time.perf_counter()
        encode_dense(["预热"])
        logger.info("模型预热完成 %.1fs", time.perf_counter() - t0)
        self._thread.start()

    # ── 缓存 ──
    @staticmethod
    def _key(text: str, want: tuple[str, ...]) -> str:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        return f"{'|'.join(want)}:{h}"

    def _cache_get(self, key: str):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._stats["cache_hits"] += 1
                return self._cache[key]
            self._stats["cache_miss"] += 1
            return None

    def _cache_put(self, key: str, val) -> None:
        with self._lock:
            self._cache[key] = val
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            s = dict(self._stats)
        s["cache_size"] = len(self._cache)
        if s["batches"]:
            s["avg_batch"] = round(s["items"] / s["batches"], 2)
        return s

    def submit(self, texts: list[str], want: tuple[str, ...]) -> Future:
        fut: Future = Future()
        self._q.put((texts, want, fut))
        return fut

    def _loop(self) -> None:
        from app.services.embedding import encode_dense, encode_sparse

        while True:
            first = self._q.get()
            batch = [first]
            deadline = time.monotonic() + _MAX_WAIT_MS / 1000.0
            # 攒批：条数到上限或等待超时即发
            while len(batch) < _MAX_BATCH:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                try:
                    batch.append(self._q.get(timeout=remain))
                except queue.Empty:
                    break

            # 按 want 分组（同一批里 dense/sparse 需求可能不同）
            todo: list[tuple] = []
            flat: list[str] = []
            for texts, want, fut in batch:
                idx = []
                for t in texts:
                    idx.append(len(flat))
                    flat.append(t)
                todo.append((texts, want, fut, idx))

            try:
                dense = encode_dense(flat) if any("dense" in w for _, w, _, _ in todo) else None
                sparse = encode_sparse(flat) if any("sparse" in w for _, w, _, _ in todo) else None
            except Exception as exc:  # noqa: BLE001 —— 批失败要逐条回传异常
                for _, _, fut, _ in todo:
                    if not fut.done():
                        fut.set_exception(exc)
                continue

            with self._lock:
                self._stats["batches"] += 1
                self._stats["items"] += len(flat)

            for texts, want, fut, idx in todo:
                try:
                    payload: dict = {}
                    if dense is not None and "dense" in want:
                        payload["dense"] = [dense[i] for i in idx]
                    if sparse is not None and "sparse" in want:
                        payload["sparse"] = [sparse[i] for i in idx]
                    for k, t in enumerate(texts):
                        key = self._key(t, want)
                        self._cache_put(
                            key,
                            {
                                kk: (payload[kk][k] if kk in payload else None)
                                for kk in ("dense", "sparse")
                            },
                        )
                    if not fut.done():
                        fut.set_result(payload)
                except Exception as exc:  # noqa: BLE001
                    if not fut.done():
                        fut.set_exception(exc)


_batcher = _Batcher()

# ⚠️ 请求模型必须定义在**模块级**：若定义在 `create_app()` 内部，
# FastAPI 解析不到注解类型，会把 `req` 当成 query 参数 ⇒ 客户端收到
# 422 `Field required: query.req`（实测踩过）。
from pydantic import BaseModel  # noqa: E402


class EmbedRequest(BaseModel):
    texts: list[str]
    want: list[str] = ["dense", "sparse"]


def create_app():
    from fastapi import FastAPI

    app = FastAPI(title="yishu-embedding", version="1.0.0")

    @app.on_event("startup")
    def _startup() -> None:
        _batcher.start()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "stats": _batcher.stats()}

    @app.get("/stats")
    def stats() -> dict:
        return _batcher.stats()

    @app.post("/embed")
    def embed(req: EmbedRequest) -> dict:
        want = tuple(w for w in req.want if w in ("dense", "sparse")) or ("dense",)
        if not req.texts:
            return {"dense": [], "sparse": []}

        # 全命中缓存就不进批处理队列（省一次排队往返）
        hits: list = [None] * len(req.texts)
        miss_idx: list[int] = []
        for i, t in enumerate(req.texts):
            v = _batcher._cache_get(_Batcher._key(t, want))
            if v is None:
                miss_idx.append(i)
            else:
                hits[i] = v

        if miss_idx:
            fut = _batcher.submit([req.texts[i] for i in miss_idx], want)
            got = fut.result(timeout=120)
            for k, i in enumerate(miss_idx):
                hits[i] = {
                    "dense": got["dense"][k] if "dense" in got else None,
                    "sparse": got["sparse"][k] if "sparse" in got else None,
                }

        out: dict = {}
        if "dense" in want:
            out["dense"] = [h["dense"] for h in hits]
        if "sparse" in want:
            out["sparse"] = [h["sparse"] for h in hits]
        out["cached"] = len(req.texts) - len(miss_idx)
        return out

    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8020)
    # 默认只绑回环：本服务只被同机 backend 调用（LAN/模拟器访问的是 backend 8010，不是这里）。
    # 需要跨机调试时显式传 --host 0.0.0.0。
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
