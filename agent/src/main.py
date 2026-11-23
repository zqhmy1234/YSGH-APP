"""New Agent service entry point (no legacy LangGraph/Coze imports).

Local: ``uvicorn main:app --port 8400``; staging: port 8300.
The app is built by ``api.routes.app_from_env`` from environment variables, and
the old ``archive/legacy-agent-v1`` code is never imported.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Load ``agent/.env`` without overriding process env (containers win)."""
    env_path = ROOT.parent / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception:  # noqa: BLE001 - env loading is best effort
        return


def app_from_env() -> FastAPI:
    _load_dotenv()
    from api.routes import app_from_env as build

    return build()


# ``uvicorn main:app`` requires a module-level object, not only a factory.
app = app_from_env()


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("AGENT_PORT", "8400")))
