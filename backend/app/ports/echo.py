"""EchoPort: today / preferences / exclusions. Silent by default."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

EchoStatus = Literal["ok", "suppressed", "no_candidate", "unavailable"]


@dataclass(frozen=True)
class EchoCandidate:
    echo_id: str
    title: str
    reason: str
    sensitivity: str
    score: float
    why_now: str


@dataclass(frozen=True)
class EchoResult:
    status: EchoStatus
    candidate: EchoCandidate | None = None
    suppressed_reason: str = ""


@runtime_checkable
class EchoPort(Protocol):
    """B4 回响端口（合并八闸，2026-11-24）。

    闸4「角色可见」在此留占位参数 ``role``：**本期不实现**。
    依赖 B8 家庭域（家庭组/成员/角色矩阵）落地后才生效；当前实现须忽略该参数
    并在文档标注，不得假装做了权限判定。默认 "owner"（本人端唯一形态）。

    preference get/set 对应闸1/2/3 的状态源（echo_preference 表），本期已实现
    于 services/echo.py 与 api/echo.py，端口签名在此登记。
    """

    def today(self, user_id: str, *, role: str = "owner") -> EchoResult: ...

    def dismiss(self, user_id: str, echo_id: str) -> bool: ...

    def get_preference(self, user_id: str) -> dict: ...

    def set_preference(self, user_id: str, patch: dict) -> dict: ...
