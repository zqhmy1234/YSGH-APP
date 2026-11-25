"""忆述光华 Agent service package.

Layering (hard rule, checked by ``tests/contract/test_import_boundary.py``):

    main.py -> api -> harness -> agents/memory -> ports -> harness.contracts
    adapters -> ports/contracts
    platform -> stdlib + fastapi only

No module outside ``adapters`` may speak HTTP/SQL/Qdrant; no module imports
``backend``. Cross-process DTOs live in ``shared/contracts``.
"""
