#!/bin/bash
set -eo pipefail

# 2026-10-06：删除了 Coze devbox 的目录初始化块（读 COZE_PROJECT_ENV /
# COZE_WORKSPACE_PATH 在本项目下永远为空，纯空跑）。

# uv 安装依赖
if [ -n "$PIP_TARGET" ]; then
  echo "[setup] Deploy mode (uv): installing to PIP_TARGET=$PIP_TARGET"
  uv export --frozen --no-hashes --no-dev | uv pip install --no-cache --target "$PIP_TARGET" -r -
else
  echo "[setup] Devbox mode (uv): installing to .venv"
  if [ -f "uv.lock" ]; then
    uv sync --frozen || uv sync
  else
    uv sync
  fi
  touch .venv/.uv_ready
fi
