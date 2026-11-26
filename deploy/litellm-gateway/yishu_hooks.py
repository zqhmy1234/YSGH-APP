"""Server-local LiteLLM startup hooks (LITELLM_WORKER_STARTUP_HOOKS).

Kept outside site-packages so a LiteLLM upgrade never silently carries a patch
forward; the hook is imported at proxy startup and fails loudly if the upstream
API it patches is gone.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("yishu.litellm_hooks")
_patched = False


def patch_prisma_engine_slot() -> None:
    """Restore LiteLLM's Prisma engine instrumentation on prisma-client-py 0.15.

    LiteLLM 1.110.2 writes its tracked engine to ``client._Prisma__engine``, a
    name that only exists in older prisma-client-py releases. 0.15 stores the
    engine in the slotted ``_internal_engine`` behind the ``_engine`` property,
    so the write raises ``AttributeError``; LiteLLM's connect retry then sees
    ``is_connected() == True`` and continues without the tracked engine.

    Patching ``PrismaWrapper._write_engine`` to use the supported property
    setter restores the per-engine drain tracking and removes the misleading
    startup warning. It is a no-op after the first successful call.
    """
    global _patched
    if _patched:
        return
    from litellm.proxy.db import prisma_client as prisma_module

    def _write_engine(prisma_client: object, engine: object) -> None:
        prisma_client._engine = engine  # type: ignore[attr-defined]

    prisma_module.PrismaWrapper._write_engine = staticmethod(_write_engine)
    _patched = True
    logger.warning(
        "yishu hook: patched PrismaWrapper._write_engine to use the prisma-client-py 0.15 _engine setter"
    )
