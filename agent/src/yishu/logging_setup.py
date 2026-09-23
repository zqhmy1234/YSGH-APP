"""日志初始化（替代 `coze_coding_utils.log.config.LOG_LEVEL` 与 `log.write_log.setup_logging`）。

上游把日志交给 Coze（JSON 文件 + cozeloop 上报）。本服务用标准 `logging`，
口径与本仓后端一致：单行、含时间/级别/logger 名、级别由 `AGENT_LOG_LEVEL` 控制。
"""
from __future__ import annotations

import logging
import os

LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO").upper()
_FILE_LOG = os.getenv("AGENT_LOG_FILE", "")

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(level: str | None = None) -> None:
    """配置根 logger（幂等，可重复调用）。"""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if _FILE_LOG:
        handlers.append(logging.FileHandler(_FILE_LOG, encoding="utf-8"))
    logging.basicConfig(
        level=(level or LOG_LEVEL),
        format=_FORMAT,
        handlers=handlers,
        force=True,  # 幂等：重复调用时重建 handler，避免叠加
    )
    # 第三方库降噪（uvicorn/httpx 的 INFO 在本地开发时很吵）
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


__all__ = ["LOG_LEVEL", "setup_logging"]
