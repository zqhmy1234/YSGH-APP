"""Persona port re-exports.

The canonical definitions live in ``harness.persona`` because the context
assembler consumes them directly. This module exists so adapters and agents can
import from the port layer without depending on a harness implementation detail.
"""

from harness.persona import (
    DefaultPersona,
    PersonaPort,
    PersonaSnapshot,
    RelationshipChoices,
)

__all__ = ["DefaultPersona", "PersonaPort", "PersonaSnapshot", "RelationshipChoices"]
