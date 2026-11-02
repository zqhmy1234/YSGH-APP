"""降级计数（2026-10-14 · 台账 §7.12「静默降级显性化」）。

## 为什么需要它

W1 记忆镜像（agent → backend `POST /contents/agent`）与 W2 检索合并
（agent → backend `POST /search/agent`）都是**增强**路径：失败即降级。
降级本身是正确的取舍（不能因为增强挂了就让"保存"这个用户可见动作失败），
但**旧实现只记一条 INFO 日志**（甚至裸 `return`），于是用户侧的形态是：

    AI 说"已记住"、卡片也出了  →  但 App 时间轴 / 检索一片空白、无任何报错

这正是本项目最贵的一类故障（"看起来正常实际是假的"）。本模块把这类降级
**计数**下来，由 agent `/health` 暴露 ⇒ 运维一眼可见"降级正在持续发生"，
而不必去翻日志、也不必等用户报障。

## 为什么单独一个零依赖模块

`/health` 会 import 它。探活接口**不得触发建连/建表**（见 `main.health` 注释），
也不该把 langchain / supabase 这类重依赖拉进探活路径。故这里刻意只依赖标准库。

## 口径

`reason` 用可读的下划线串——它会**同时**成为日志事件名（`degrade_<reason>`）
与 `/health` 的 `degradation` 字段名，所以不要带空格或中划线。
"""
from __future__ import annotations

# reason -> 累计次数
_COUNTS: dict[str, int] = {}


def note(reason: str) -> bool:
    """记一次降级；返回**是否首次**（调用方据此决定 WARNING 还是 INFO）。"""
    before = _COUNTS.get(reason, 0)
    _COUNTS[reason] = before + 1
    return before == 0


def snapshot() -> dict[str, int]:
    """计数快照（只读，供 `/health` 暴露）。`total` 非 0 即代表有路径在降级。"""
    out: dict[str, int] = {"total": sum(_COUNTS.values())}
    for key in sorted(_COUNTS):
        out[key] = _COUNTS[key]
    return out


def reset() -> None:
    """清空计数（**仅供测试**；运行期无任何调用方）。"""
    _COUNTS.clear()
