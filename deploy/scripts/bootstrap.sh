#!/usr/bin/env bash
# ============================================================================
# 忆述光华 · 一次性初始化（S1 卡 · 幂等）
#
# 做什么：建运行时目录 → 生成最小 deploy/.env（仅容器参数，若缺失）→ 起三个依赖服务
#         → 等健康 → alembic upgrade head → 断言关键表存在 → 打印下一步
# 不做：不装 Python 依赖、不下载模型（那是 pull_models.sh）、不装 systemd（那是 deploy_one.sh）
#
# 幂等契约（验收项）：重复执行不重建目录、不重复迁移、不产生重复容器。
# 用法（仓库根）：bash deploy/scripts/bootstrap.sh
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
COMPOSE=(docker compose -f "${DEPLOY_DIR}/docker-compose.yml")

log()  { printf '[bootstrap] %s\n' "$*"; }
warn() { printf '[bootstrap][WARN] %s\n' "$*" >&2; }
die()  { printf '[bootstrap][FAIL] %s\n' "$*" >&2; exit 1; }

# ---------- 0. 前置检查 ----------
# 三类失败分开报（2026-09-21 实测踩坑）：CLI 不存在 / CLI 在但不是 Compose v2 / CLI 与 compose 都在但 daemon 没起。
# 第一种最常见的误因是「在 Windows 开发机上用 WSL 的 bash 跑本脚本」——那里的 PATH 看不到 Windows 的
# docker.exe，会得到与「没装 Docker」一模一样的报错。本套脚本的设计运行环境是**目标 Linux 服务器**。
if ! command -v docker >/dev/null 2>&1; then
	die "未找到 docker：目标机未安装 Docker Engine，或 docker 不在 PATH。**本套脚本的设计运行环境是目标 Linux 服务器**（见 deploy/README.md §8）。"
fi
if ! docker compose version >/dev/null 2>&1; then
	die "docker 存在，但 'docker compose' 子命令不可用——需要 Compose **v2**（'docker compose'，不是 'docker-compose'）。"
fi
if ! docker info >/dev/null 2>&1; then
	die "Docker CLI 与 Compose 就绪，但 daemon 未运行（docker info 失败）——先启动 dockerd（Linux）或 Docker Desktop（Windows/macOS）再重跑。"
fi

# Python 解释器：优先 backend/.venv（生产约定），否则 python3，最后 python
PYTHON_BIN=""
if [[ -x "${REPO_ROOT}/backend/.venv/bin/python" ]]; then
	PYTHON_BIN="${REPO_ROOT}/backend/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
	PYTHON_BIN="$(command -v python)"
fi

# ---------- 1. 运行时目录（幂等：-p 不报错） ----------
log "创建运行时目录"
mkdir -p \
	"${DEPLOY_DIR}/data/postgres" \
	"${DEPLOY_DIR}/data/redis" \
	"${DEPLOY_DIR}/data/qdrant" \
	"${DEPLOY_DIR}/data/wal-archive" \
	"${DEPLOY_DIR}/backup/pg" \
	"${DEPLOY_DIR}/logs"

# ---------- 2. deploy/.env（缺失时生成最小版） ----------
if [[ ! -f "${DEPLOY_DIR}/.env" ]]; then
	warn "deploy/.env 不存在 → 生成仅含容器参数的最小版本（随机 PG 密码）"
	PG_PW=""
	if command -v openssl >/dev/null 2>&1; then
		PG_PW="$(openssl rand -hex 24)"
	else
		PG_PW="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
	fi
	cat > "${DEPLOY_DIR}/.env" <<EOF
# 本文件由 bootstrap.sh 自动生成（最小版：仅够拉起三个依赖服务）
# 生产部署请用 deploy/.env.production.template 的完整版本覆盖（见 S3 卡 / deploy/RUNBOOK.md）
POSTGRES_USER=postgres
POSTGRES_PASSWORD=${PG_PW}
POSTGRES_DB=yishu
POSTGRES_PORT=5432
REDIS_PORT=6379
QDRANT_HTTP_PORT=6333
QDRANT_GRPC_PORT=6334
API_PORT=8010
EOF
	chmod 600 "${DEPLOY_DIR}/.env"
	log "已生成 deploy/.env（权限 600）"
else
	log "deploy/.env 已存在 → 保持不变（幂等）"
fi

# ---------- 3. 起依赖服务 ----------
# WAL 归档开启时先把归档目录属主改成 postgres（官方镜像 uid=999）：
# 属主不对 → archive_command 反复失败 → PG 持续保留 WAL 直至 pg_wal 打满磁盘（RUNBOOK §6 排障 1）。
PG_ARCHIVE_MODE_EFFECTIVE="$(grep -E '^PG_ARCHIVE_MODE=' "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2- || true)"
if [[ "${PG_ARCHIVE_MODE_EFFECTIVE:-off}" == "on" ]]; then
	log "PG_ARCHIVE_MODE=on → 修正 WAL 归档目录属主（uid 999）"
	"${COMPOSE[@]}" run --rm -T --user root --entrypoint sh postgres -c \
		"mkdir -p /var/lib/postgresql/wal-archive && chown 999:999 /var/lib/postgresql/wal-archive && chmod 700 /var/lib/postgresql/wal-archive"
fi

log "启动依赖服务（postgres / redis / qdrant）"
"${COMPOSE[@]}" up -d

# ---------- 4. 等健康 ----------
log "等待 PostgreSQL 就绪（最长 120s）"
PG_USER="$(grep -E '^POSTGRES_USER=' "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2- || true)"
PG_DB="$(grep -E '^POSTGRES_DB=' "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2- || true)"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-yishu}"

pg_ready=0
for _ in $(seq 1 60); do
	if "${COMPOSE[@]}" exec -T postgres pg_isready -U "${PG_USER}" -d "${PG_DB}" >/dev/null 2>&1; then
		pg_ready=1
		break
	fi
	sleep 2
done
[[ "${pg_ready}" -eq 1 ]] || die "PostgreSQL 120s 内未就绪，请查：${COMPOSE[*]} logs postgres"

# ---------- 5. 迁移到 head ----------
if [[ -z "${PYTHON_BIN}" ]]; then
	warn "未找到可用 Python → 跳过 alembic 迁移与表断言。装好 backend/.venv 后重跑本脚本即可补齐。"
else
	# 生产库的 vector 扩展由 initdb/01-extensions.sql 提供；此处再兜一次（幂等），
	# 覆盖「数据目录已存在、initdb 不再执行」的场景。
	log "确保 pgvector 扩展存在（幂等）"
	"${COMPOSE[@]}" exec -T postgres \
		psql -U "${PG_USER}" -d "${PG_DB}" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

	log "运行 alembic upgrade head（重复执行 no-op；DATABASE_URL 读自 backend/.env）"
	( cd "${REPO_ROOT}/backend" && "${PYTHON_BIN}" -m alembic upgrade head )

	# ---------- 6. 表存在性断言（迁移后自证） ----------
	log "断言关键表存在"
	missing=""
	for t in users devices contents events event_items sync_state; do
		found="$("${COMPOSE[@]}" exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
			"SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='${t}'" | tr -d '[:space:]')"
		[[ "${found}" == "1" ]] || missing="${missing} ${t}"
	done
	if [[ -n "${missing}" ]]; then
		die "迁移后仍缺失表：${missing}（检查 alembic 输出与 DATABASE_URL 是否指向本实例）"
	fi
	log "关键表齐全 ✅"
fi

# ---------- 7. 汇总 ----------
log "依赖服务状态："
"${COMPOSE[@]}" ps

cat <<'NEXT'

[bootstrap] 完成。下一步：
  1) bash deploy/scripts/pull_models.sh     # 预置 SenseVoice / BGE-M3 / 校验 SetFit
  2) bash deploy/scripts/deploy_one.sh      # 安装 systemd 并起 API + worker（或单独跑本步前的其余脚本）
  3) bash deploy/scripts/healthcheck.sh     # 三依赖 + API + DB 表 自证
  生产变量（JWT_SECRET / REFRESH_TOKEN_HMAC_KEY / MEDIA_URL_SECRET / STORAGE_BACKEND=cos …）
  见 deploy/.env.production.template 与 deploy/RUNBOOK.md（S3 卡产出）。
NEXT
