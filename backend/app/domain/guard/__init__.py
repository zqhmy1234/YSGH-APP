"""Guard-domain pure checks (empathy, never model self-evaluation)."""

from app.domain.guard.empathy import GLOBAL_TABOO, EmpathyVerdict, check_empathy

__all__ = ["GLOBAL_TABOO", "EmpathyVerdict", "check_empathy"]
