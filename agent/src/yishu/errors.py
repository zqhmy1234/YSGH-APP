"""错误分类（替代 `coze_coding_utils.error.classifier`）。

上游 `classify_error(exc)` / `ErrorClassifier` 把异常归到平台错误码（用于 Coze 侧遥测与
HTTP 状态映射）。本服务不需要平台错误码，但**需要同一件事的实用版本**：
把异常归成("类别", http 状态, 是否可重试)，供 HTTP 层决定返回码与是否记 ERROR 级日志。

刻意保留 "retryable" 概念：上游用它区分"该退避重试"与"该立刻失败"，
本服务在调用百炼/抓取时同样需要（见 `yishu/fetch.py`）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassifiedError:
    kind: str          # config | auth | rate_limit | timeout | upstream | bad_input | internal
    http_status: int   # 建议的响应码
    retryable: bool


_RULES: tuple[tuple[type[BaseException], ClassifiedError], ...] = ()


def _build_rules() -> tuple[tuple[type[BaseException], ClassifiedError], ...]:
    """延迟构建规则表（避免模块导入期依赖可选库）。"""
    rules: list[tuple[type[BaseException], ClassifiedError]] = [
        (ValueError, ClassifiedError("bad_input", 400, False)),
        (KeyError, ClassifiedError("bad_input", 400, False)),
        (TimeoutError, ClassifiedError("timeout", 504, True)),
        (ConnectionError, ClassifiedError("upstream", 502, True)),
    ]
    return tuple(rules)


def classify_error(exc: BaseException) -> ClassifiedError:
    """把异常归成 (kind, http_status, retryable)。未识别 → internal/500/重试一次。"""
    global _RULES
    if not _RULES:
        _RULES = _build_rules()
    for exc_type, classified in _RULES:
        if isinstance(exc, exc_type):
            return classified
    # 配置类错误的显式标记（本服务用 RuntimeError 表达"缺配置"）
    if isinstance(exc, RuntimeError) and "未配置" in str(exc):
        return ClassifiedError("config", 500, False)
    return ClassifiedError("internal", 500, True)


class ErrorClassifier:
    """上游同名类的兼容壳（仅 `.classify` 语义，上游其余方法无人调用）。"""

    def classify(self, exc: BaseException) -> ClassifiedError:
        return classify_error(exc)


__all__ = ["ClassifiedError", "ErrorClassifier", "classify_error"]
