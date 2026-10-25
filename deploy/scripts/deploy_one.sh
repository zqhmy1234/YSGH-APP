#!/usr/bin/env bash
# ============================================================================
# 忆述光华 · 单命令部署（S1 卡 · 幂等）
#
# ⚠️ 命名澄清：本项目历史上另有同名昵称的脚本 `deploy_one.sh`（**客户端推包**：adb install +
#    cli launch + reverse 隧道），它从未入库。本脚本是**服务端**一键部署，与推包无关。
#
# 串起来做四件事（每步本身幂等）：
#   ① bootstrap.sh   依赖服务 + 迁移 + 表断言
#   ② pull_models.sh 模型预置（SenseVoice / BGE-M3 / SetFit 校验）
#   ③ 渲染 systemd unit → /etc/systemd/system/ → daemon-reload → enable + restart
#   ④ healthcheck.sh 七项自证
#
# 用法（仓库根）：
#   bash deploy/scripts/deploy_one.sh              # 全流程
#   SKIP_MODELS=1 bash deploy/scripts/deploy_one.sh   # 跳过模型（依赖已预置时提速）
#   SKIP_SYSTEMD=1 bash deploy/scripts/deploy_one.sh  # 只起依赖+迁移（业务进程另行托管）
# 退出码：0=全绿；非 0=失败（输出末尾给出失败步骤）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"

SKIP_MODELS="${SKIP_MODELS:-0}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"
RUN_USER="${RUN_USER:-$(id -un)}"

log()  { printf '\n[deploy] === %s ===\n' "$*"; }
warn() { printf '[deploy][WARN] %s\n' "$*" >&2; }
die()  { printf '[deploy][FAIL] %s\n' "$*" >&2; exit 1; }

log "0. 前置检查"
command -v docker >/dev/null 2>&1 || die "未找到 docker"
docker compose version >/dev/null 2>&1 || die "未找到 'docker compose'（需要 Compose v2）"
[[ -f "${DEPLOY_DIR}/.env" ]] || warn "deploy/.env 不存在——bootstrap.sh 会生成最小版；生产请改用 .env.production.template"
[[ -x "${REPO_ROOT}/backend/.venv/bin/python" ]] || warn "backend/.venv 不存在——alembic/systemd 的 ExecStart 都会失败，请先建 venv 并 pip install -r backend/requirements.txt"

# 生产口径软校验（不阻断：本脚本也要能在 dev 环境跑通）
if [[ -f "${DEPLOY_DIR}/.env" ]]; then
	app_env="$(grep -E '^APP_ENV=' "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2- || true)"
	if [[ "${app_env}" != "production" ]]; then
		warn "APP_ENV='${app_env:-<未设置>}'（非 production）——生产部署请置 production"
	else
		for k in JWT_SECRET REFRESH_TOKEN_HMAC_KEY; do
			v="$(grep -E "^${k}=" "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2- || true)"
			case "${v}" in
				""|change-me*) warn "${k} 为空或仍是默认值 → APP_ENV=production 启动时会直接抛错（config.py:201-230，fail-closed 设计）" ;;
			esac
		done
	fi
fi

log "1. 依赖服务 + 迁移（bootstrap.sh）"
bash "${SCRIPT_DIR}/bootstrap.sh"

if [[ "${SKIP_MODELS}" == "1" ]]; then
	log "2. 模型预置 —— SKIP_MODELS=1，跳过"
else
	log "2. 模型预置（pull_models.sh）"
	bash "${SCRIPT_DIR}/pull_models.sh"
fi

if [[ "${SKIP_SYSTEMD}" == "1" ]]; then
	log "3. systemd 安装 —— SKIP_SYSTEMD=1，跳过"
else
	log "3. 安装并重启 systemd 服务（api / worker / agent）"
	install_systemd() {
		local src="$1" name="$2"
		# 占位符渲染（模板里的 @DEPLOY_ROOT@ / @RUN_USER@）
		sed -e "s|@DEPLOY_ROOT@|${REPO_ROOT}|g" -e "s|@RUN_USER@|${RUN_USER}|g" "${src}" \
			| sudo tee "/etc/systemd/system/${name}" >/dev/null
		sudo chmod 644 "/etc/systemd/system/${name}"
		printf '[deploy] 已写入 /etc/systemd/system/%s\n' "${name}"
	}
	install_systemd "${DEPLOY_DIR}/systemd/yishu-api.service" "yishu-api.service"
	install_systemd "${DEPLOY_DIR}/systemd/yishu-worker.service" "yishu-worker.service"
	# D-23（2026-10-25 · 4520 条 processing 卡死根因修复）：requeue_job 的**调度**。
	# 此前它只存在于 RUNBOOK 的一行 cron 表格里、**从未真正安装过**（全仓零执行路径）
	# ⇒ `process_content` 承诺的"超龄重扫兜底"从来没发生 ⇒ 4520 条永久卡死且完全静默。
	# ⚠️ 是 .timer 而非 .service 单独 enable：enable timer 才会定时触发（service 是 oneshot，
	#    单独 enable service 不会按周期跑）。两者都要装（timer 靠 Unit= 找 service）。
	install_systemd "${DEPLOY_DIR}/systemd/yishu-requeue.service" "yishu-requeue.service"
	install_systemd "${DEPLOY_DIR}/systemd/yishu-requeue.timer" "yishu-requeue.timer"
	# W4 部署接线（2026-10-06，docs/功能对齐计划_20261006.md）：agent 服务此前
	# 没有任何部署单元——本机能跑、线上不存在，「客户端↔Agent 接线完成」的
	# 验收前提就是它在线上。前置：agent/.venv（uv sync --frozen；依赖含
	# langchain/langgraph，与 backend 的 venv 互不通用）。
	if [[ ! -x "${REPO_ROOT}/agent/.venv/bin/python" ]]; then
		warn "agent/.venv 不存在——yishu-agent 会启动失败；先 cd agent && uv sync --frozen"
	fi
	install_systemd "${DEPLOY_DIR}/systemd/yishu-agent.service" "yishu-agent.service"
	sudo systemctl daemon-reload
	# enable 一次（重复 enable 无副作用）；随后统一 restart（避免 enable --now 与 restart 叠加）
	sudo systemctl enable yishu-api.service yishu-worker.service yishu-agent.service >/dev/null
	# D-23：timer 必须 enable（并 restart 使新的 OnCalendar 立即生效）。
	# requeue.service 是 oneshot，**不要** enable（enable 它会在开机时跑一次完整重投，
	# 那是意外成本；它应当只由 timer 触发）。
	sudo systemctl enable --now yishu-requeue.timer >/dev/null
	sudo systemctl restart yishu-requeue.timer
	sudo systemctl restart yishu-api.service yishu-worker.service yishu-agent.service
	sleep 3
	sudo systemctl --no-pager --lines=0 status yishu-api.service yishu-worker.service yishu-agent.service || true
	sudo systemctl list-timers yishu-requeue.timer --no-pager || true
fi

log "4. 自证（healthcheck.sh）"
if bash "${SCRIPT_DIR}/healthcheck.sh"; then
	log "全部完成 ✅"
	if [[ "${SKIP_SYSTEMD}" != "1" ]]; then
		cat <<NEXT

[deploy] 后续（上机后回填 docs/服务器规格核算_20260921.md §7 实测校验表）：
  free -m                                              # 校验项 1
  ps -o rss= -p "\$(pgrep -f 'uvicorn app.main:app')"    # 校验项 2（API 进程 RSS）
  ps -o rss= -p "\$(pgrep -f 'app.workers.worker')"      # 校验项 3（worker 进程 RSS）
  du -sh backend/models ~/.cache/huggingface            # 校验项 5（磁盘分账）
NEXT
	fi
else
	die "healthcheck 未全绿 —— 见上方失败项；排障表见 deploy/RUNBOOK.md（S3 卡）"
fi
