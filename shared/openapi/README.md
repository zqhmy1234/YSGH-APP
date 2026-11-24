# Generated OpenAPI for the Agent

`docs/openapi.json` is the live backend export (76 paths at 2026-11-23) and is
the public-user contract. Agent-facing L3 endpoints are deliberately
`include_in_schema=False` and therefore absent from it; their contract lives in
`shared/contracts/agent_api.py` and is pinned by
`backend/tests/agent_contract/test_shared_contract_parity.py`.

This directory is reserved for the generated agent-facing OpenAPI subset once
the six endpoints are promoted out of the internal surface. Until then, adding a
second JSON here would create a second source of truth, so it stays empty by
design (see `docs/adr/ADR-001-external-agent-over-http.md`).
