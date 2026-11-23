"""Trust domain: sensitivity classification and the role gate.

Nothing here calls a model. Sensitivity is a deterministic classifier so the same
text cannot be "public" for one reader and "sensitive" for another by accident.
"""

from __future__ import annotations

from ..contracts import FamilyAction, FamilyRole, PermissionDecision, Sensitivity

CRITICAL_MARKERS = ("自杀", "割腕", "跳楼", "身份证号", "银行卡", "密码", "病历号", "诊断书")
SENSITIVE_MARKERS = ("逝者", "葬礼", "癌症", "化疗", "离婚", "前任", "抑郁", "吃药", "工资", "欠款", "手术")


def classify(text: str, people: tuple[str, ...] = ()) -> Sensitivity:
    if any(marker in text for marker in CRITICAL_MARKERS):
        return Sensitivity.CRITICAL
    if any(marker in text for marker in SENSITIVE_MARKERS):
        return Sensitivity.SENSITIVE
    if people:
        return Sensitivity.PERSONAL
    return Sensitivity.PUBLIC


class TrustGate:
    """Role × sensitivity × action. Default deny for anything unlisted."""

    _ALLOW: dict[FamilyRole, dict[Sensitivity, frozenset[FamilyAction]]] = {
        FamilyRole.OWNER: {s: frozenset(FamilyAction) for s in Sensitivity},
        FamilyRole.ADULT: {
            Sensitivity.PUBLIC: frozenset(FamilyAction),
            Sensitivity.PERSONAL: frozenset({FamilyAction.READ, FamilyAction.WRITE, FamilyAction.EXPORT, FamilyAction.DELETE}),
            Sensitivity.SENSITIVE: frozenset({FamilyAction.READ, FamilyAction.WRITE}),
            Sensitivity.CRITICAL: frozenset({FamilyAction.READ}),
        },
        FamilyRole.TEEN: {
            Sensitivity.PUBLIC: frozenset(FamilyAction),
            Sensitivity.PERSONAL: frozenset({FamilyAction.READ, FamilyAction.WRITE}),
            Sensitivity.SENSITIVE: frozenset({FamilyAction.READ}),
            Sensitivity.CRITICAL: frozenset(),
        },
        FamilyRole.CHILD: {
            Sensitivity.PUBLIC: frozenset({FamilyAction.READ}),
            Sensitivity.PERSONAL: frozenset({FamilyAction.READ}),
            Sensitivity.SENSITIVE: frozenset(),
            Sensitivity.CRITICAL: frozenset(),
        },
        FamilyRole.GUEST: {
            Sensitivity.PUBLIC: frozenset({FamilyAction.READ}),
            Sensitivity.PERSONAL: frozenset(),
            Sensitivity.SENSITIVE: frozenset(),
            Sensitivity.CRITICAL: frozenset(),
        },
    }

    def check(self, role: FamilyRole, sensitivity: Sensitivity, action: FamilyAction) -> PermissionDecision:
        allowed = self._ALLOW.get(role, {}).get(sensitivity, frozenset())
        if action in allowed:
            return PermissionDecision(allow=True, reason="ok")
        return PermissionDecision(allow=False, reason=f"denied:{role.value}/{sensitivity.value}/{action.value}")

    def visible(self, role: FamilyRole, sensitivity: Sensitivity) -> bool:
        return self.check(role, sensitivity, FamilyAction.READ).allow
