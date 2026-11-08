#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${WORK_DIR}/.venv/bin/python"
PORT="${AGENT_HTTP_PORT:-8300}"

if [[ ! -f "${WORK_DIR}/.env" ]]; then
  echo "缺少 agent/.env：先复制 .env.example 并填写配置" >&2
  exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "缺少 agent/.venv：先在 agent 目录运行 uv sync --frozen --dev --python 3.12" >&2
  exit 1
fi

export AGENT_HTTP_PORT="$PORT"
echo "[run] uvicorn main:app 127.0.0.1:${PORT} (cwd=${WORK_DIR}/src)"
cd "${WORK_DIR}/src"
exec "$PYTHON" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
