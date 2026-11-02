#!/bin/bash

set -e
# 导出环境变量

# 2026-10-06：WORK_DIR 不再读 COZE_WORKSPACE_PATH（本项目永远没有这个变量，
# 静默回落到 `.` 会让相对路径取决于启动目录），改为按脚本位置定位。
WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 端口口径与 `src/main.py` 一致：`main.py` 只认 AGENT_HTTP_PORT（默认 8300），
# 它**不解析** `-m http` / `-p`（那是 Coze 形态的参数）。保留 -p 只是把它转成
# 环境变量，避免"脚本说 5000、服务起 8300"这种两边各说各话。
PORT="${DEPLOY_RUN_PORT:-${AGENT_HTTP_PORT:-8300}}"

usage() {
  echo "用法: $0 -p <端口>"
}

while getopts "p:h" opt; do
  case "$opt" in
    p)
      PORT="$OPTARG"
      ;;
    h)
      usage
      exit 0
      ;;
    \?)
      echo "无效选项: -$OPTARG"
      usage
      exit 1
      ;;
  esac
done

# 激活 .venv（devbox 环境），deploy 无 .venv 则跳过
if [ -f "${WORK_DIR}/.venv/bin/activate" ]; then
  source "${WORK_DIR}/.venv/bin/activate"
fi

# `main.py` 从环境变量读端口与工作目录，不再接受 Coze 形态的位置参数。
export AGENT_HTTP_PORT="${PORT}"
python "${WORK_DIR}/src/main.py"
