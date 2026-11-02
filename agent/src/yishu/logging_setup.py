"""日志初始化（替代 `coze_coding_utils.log.config.LOG_LEVEL` 与 `log.write_log.setup_logging`）。

上游把日志交给 Coze（JSON 文件 + cozeloop 上报）。本服务用标准 `logging`，
口径与本仓后端一致：单行、含时间/级别/logger 名、级别由 `AGENT_LOG_LEVEL` 控制。

## 2026-10-04 增补（性能优化配套）

1. **request_id 自动注入**：每条日志行带 `rid=`，无需每个调用点手动传。
   实现为 logging Filter + 前缀包装（读 `yishu.observability.current_request_id`），
   这样**已有日志语句零改动**也能带上串联 id。
2. **JSON 行输出**：`AGENT_LOG_JSON=1` 时切到 JSON Lines，便于后续接日志采集。
   默认仍用可读单行（本地排障直接看）。
3. **审计 logger**：`yishu.audit` 单独一路（`observability.log_audit` 使用），
   级别独立可调（`AGENT_AUDIT_LEVEL`，默认 WARNING）——
   鉴权失败 / 越权尝试 / 限流触发走这条，不与普通调试日志混在一起。
4. **HTTP 日志可放开**：`AGENT_HTTP_LOG_LEVEL=INFO` 单独提级 httpx/httpcore/urllib3，
   用于排查 `yishu/fetch.py` 的重定向链（默认降噪到 WARNING）。

降噪策略不变：`httpx`/`httpcore`/`urllib3` 默认降到 WARNING。
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys

LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO").upper()
_FILE_LOG = os.getenv("AGENT_LOG_FILE", "")
_JSON_LOG = os.getenv("AGENT_LOG_JSON", "").strip() not in ("", "0", "false", "False")
_AUDIT_LEVEL = os.getenv("AGENT_AUDIT_LEVEL", "WARNING").upper()
_HTTP_LOG_LEVEL = os.getenv("AGENT_HTTP_LOG_LEVEL", "WARNING").upper()

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_FORMAT_JSON = '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'


class RequestIdFilter(logging.Filter):
    """把当前请求的 request_id 注入每条 LogRecord（供 formatter 前缀使用）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "rid", None) is None:
            from yishu.observability import current_request_id

            record.rid = current_request_id()  # type: ignore[attr-defined]
        return True


def _build_formatter() -> logging.Formatter:
    """构造带 `rid=` 前缀的 formatter。

    前缀拼在 message 上（而非写进 format 串），这样已有日志语句零改动即可带上 rid。
    """
    base = logging.Formatter(_FORMAT_JSON if _JSON_LOG else _FORMAT)

    class _RidFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            if not getattr(record, "rid", None):
                record.rid = "-"  # type: ignore[attr-defined]
            if not str(record.msg).startswith("rid="):
                record.msg = f"rid={record.rid} {record.msg}"  # type: ignore[attr-defined]
            record.message = record.getMessage()
            if self.usesTime():
                record.asctime = self.formatTime(record, self.datefmt)
            return s if (s := super().format(record)) else ""

    return _RidFormatter(base._style._fmt, datefmt=base.datefmt)  # noqa: SLF001


def setup_logging(level: str | None = None) -> None:
    """配置根 logger（幂等，可重复调用）。"""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if _FILE_LOG:
        handlers.append(logging.FileHandler(_FILE_LOG, encoding="utf-8"))

    formatter = _build_formatter()
    rid_filter = RequestIdFilter()
    for h in handlers:
        h.setFormatter(formatter)
        h.addFilter(rid_filter)

    logging.basicConfig(
        level=(level or LOG_LEVEL),
        handlers=handlers,
        force=True,  # 幂等：重复调用时重建 handler，避免叠加
    )
    # 审计通道独立 level（设 AGENT_AUDIT_LEVEL=INFO 可看全量审计）
    logging.getLogger("yishu.audit").setLevel(_AUDIT_LEVEL)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(_HTTP_LOG_LEVEL)
    with contextlib.suppress(Exception):
        logging.getLogger("yishu.observability").setLevel(logging.INFO)


__all__ = ["LOG_LEVEL", "RequestIdFilter", "setup_logging"]
