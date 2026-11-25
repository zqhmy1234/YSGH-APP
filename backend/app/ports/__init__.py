"""Backend L2 ports (15 total, plan §3.2).

Ports are Protocol-only; DTOs live in ``shared/contracts`` or in the domain's
frozen dataclasses. Implementations live in ``app.infra`` and are injected at
the L3 boundary. No port may import SQLAlchemy, Qdrant, httpx or Redis.
"""

from .crisis import CrisisPort
from .echo import EchoPort
from .guard import GuardPort
from .memory import MemoryPort, MemorySnapshot
from .person import PersonPort
from .ports_extra import (
    ConsentPort,
    EvalPort,
    ExportPort,
    IngestPort,
    LifecyclePort,
    PersonaPort,
    TimelinePort,
    VaultPort,
)
from .profile import ProfileDigestPort
from .search import SearchPort

# The 15 protocols (the read-model DTOs are not counted as ports).
PORTS = (
    "SearchPort",
    "TimelinePort",
    "IngestPort",
    "EchoPort",
    "MemoryPort",
    "ConsentPort",
    "VaultPort",
    "LifecyclePort",
    "ExportPort",
    "ProfileDigestPort",
    "GuardPort",
    "CrisisPort",
    "PersonPort",
    "PersonaPort",
    "EvalPort",
)

__all__ = [
    "ConsentPort",
    "CrisisPort",
    "EchoPort",
    "EvalPort",
    "ExportPort",
    "GuardPort",
    "IngestPort",
    "LifecyclePort",
    "MemoryPort",
    "MemorySnapshot",
    "PersonPort",
    "PersonaPort",
    "ProfileDigestPort",
    "SearchPort",
    "TimelinePort",
    "VaultPort",
    "PORTS",
]
