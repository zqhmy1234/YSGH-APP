#!/usr/bin/env bash
# ============================================================================
# 忆述光华 · 一次性初始化（S1 卡 · 幂等）
#
# 做什么：建运行时目录 → 生成最小 deploy/.env（仅容器参数，若缺失）→ 起三个依赖服务
#         → 等健康 → **容器内跑 scripts/init_db.py 建库** → 断言关键表存在 → 打印下一步
# 不做：不下载模型（那是 pull_models.sh）、不装 systemd（那是 deploy_one.sh）
#
# 🔴 2026-09-28 修正：本脚本原来跑 `alembic upgrade head` 建库，那是**跑不通的** ——
#    baseline 迁移 431bcaa8bd54 的 upgrade() 只做 ALTER + DROP 遗留表、**不建表**，
#    它假设核心表（users/contents/events…）已由 schema.sql 建过 ⇒ 空库上第一条
#    alter_column('contents') 就报 relation "contents" does not exist；
#    而 set -euo pipefail 会让脚本**死在下面那句「关键表齐全 ✅」之前**，
#    即 RUNBOOK §1.3 写的验证判据在全新服务器上**根本不可达**。
#    （项目自己的脚本早已承认：scripts/check_schema_drift.py:190,214）
#    现改为在**容器内**跑 scripts/init_db.py（ORM create_all + 10 张无模型表 + stamp head），
#    于是**宿主不需要任何 Python** —— 这正是容器化部署的前提。
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

# 注：2026-09-28 起本脚本**不再探测宿主 Python** —— 建库改在容器内跑
# （拓扑 A · 容器化）。宿主侧 systemd 方案（拓扑 B）若要用，其 Python 依赖
# 由 deploy_one.sh / RUNBOOK §1.1 负责，与本脚本无关。

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
	# 2026-09-28：AGENT_SERVICE_TOKEN 也在这里生成。
	#   它是 backend ↔ agent 之间的**共享密钥**（backend 发 X-Agent-Token 头，agent 校验），
	#   两侧必须同名同值 —— 放在同一个 .env 里就天然满足，不会配歪。
	#   它是**自洽**的随机串（不依赖任何外部账号），所以交给脚本生成最合适；
	#   compose 里用 ${AGENT_SERVICE_TOKEN:?...} 强制要求非空，此处生成即满足，无需人工去要。
	#   ⚠️ 但 agent 的百炼 key（AGENT_DASHSCOPE_API_KEY）**必须人工提供**（另一个账号），
	#      本最小版不生成它 ⇒ 跑 `--profile app` 时 compose 会以明确信息拒绝启动，这是预期。
	AGENT_TOKEN=""
	if command -v openssl >/dev/null 2>&1; then
		AGENT_TOKEN="$(openssl rand -hex 32)"
	else
		AGENT_TOKEN="$(head -c 64 /dev/urandom | od -An -tx1 | tr -d ' \n')"
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
# backend ↔ agent 共享密钥（两侧同名同值；见 docker-compose.yml 的 agent 服务）
AGENT_SERVICE_TOKEN=${AGENT_TOKEN}
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

# ---------- 5. 建库（容器内跑 scripts/init_db.py） ----------
# 生产库的 vector 扩展由 initdb/01-extensions.sql 提供；此处再兜一次（幂等），
# 覆盖「数据目录已存在、initdb 不再执行」的场景。
log "确保 pgvector 扩展存在（幂等）"
"${COMPOSE[@]}" exec -T postgres \
	psql -U "${PG_USER}" -d "${PG_DB}" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

# 管理员串（建角色/库/扩展需超级用户）只在这一次 run 里注入：
#   不写进 deploy/.env（免得应用容器常驻超级用户口令）、不进镜像。
PG_PW="$(grep -E '^POSTGRES_PASSWORD=' "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2- || true)"
[[ -n "${PG_PW}" ]] || die "deploy/.env 里没有 POSTGRES_PASSWORD，无法建库。"

log "建库 / 补表（容器内 python /app/init_db.py，幂等）"
"${COMPOSE[@]}" --profile app run --rm -T \
	-e "YSG_ADMIN_DATABASE_URL=postgresql://${PG_USER}:${PG_PW}@postgres:5432/postgres" \
	backend python /app/init_db.py

# ---------- 6. 表存在性断言（与 init_db.py 自带的冒烟检查互为独立证据） ----------
# 断言表里特意补上 profile_sensitive / profile_l2_evidence：后者**没有 ORM 模型**、
# 由代码裸 SQL 写入（profile_annotator.py:278）—— 2026-09-28 那次"16 条 DDL 静默全废"
# 正是它没建成；只抽样 ORM 表会漏掉这一类。
log "断言关键表存在"
missing=""
for t in users devices contents events event_items sync_state profile_sensitive profile_l2_evidence; do
	found="$("${COMPOSE[@]}" exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
		"SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='${t}'" | tr -d '[:space:]')"
	[[ "${found}" == "1" ]] || missing="${missing} ${t}"
done
if [[ -n "${missing}" ]]; then
	die "建库后仍缺失表：${missing}（查上一段 init_db.py 的输出；它自己也会报缺失表并非 0 退出）"
fi
log "关键表齐全 ✅"

# ---------- 7. 汇总 ----------
log "依赖服务状态："
"${COMPOSE[@]}" ps

cat <<'NEXT'

[bootstrap] 完成（三个依赖服务 + 数据库就绪）。下一步按你选的拓扑走：

  【A · 容器化】← 本项目采用的部署方式（2026-09-28 拍板）
    0) 在 deploy/.env 里补 agent 的百炼 key（🔴 **必须与后端不同账号**）：
         AGENT_DASHSCOPE_API_KEY=<Token Plan 账号的百炼 key>
       （AGENT_SERVICE_TOKEN 本脚本已自动生成，两侧同名同值、不用管；
         缺 AGENT_DASHSCOPE_API_KEY 时 compose 会以明确信息拒绝启动 —— 这是刻意的 fail-closed）
    1) bash deploy/scripts/pull_models.sh        # 预置 SenseVoice / BGE-M3 / 校验 SetFit
    2) docker compose -f deploy/docker-compose.yml --profile app up -d --build
                                                 # 起 backend(宿主 127.0.0.1:API_UPSTREAM_PORT)
                                                 #   + worker + agent（agent **不对外暴露端口**）
    3) 宿主 nginx：监听 8010 → 反代 127.0.0.1:8011
       cp deploy/nginx/ysg.conf /etc/nginx/conf.d/ && nginx -t && systemctl reload nginx
    4) bash deploy/scripts/healthcheck.sh        # 三依赖 + API + DB 表 自证
       # 另可单独验 agent（AI 对话页依赖它；返回 db_configured:true 才算通）：
       #   docker compose -f deploy/docker-compose.yml exec agent curl -fsS http://127.0.0.1:8300/health

  【B · 宿主 systemd】← 仓库原作者方案，仍然可用
    1) bash deploy/scripts/pull_models.sh
    2) bash deploy/scripts/deploy_one.sh         # 安装 systemd 并起 API + worker
    3) bash deploy/scripts/healthcheck.sh

  生产变量（JWT_SECRET / REFRESH_TOKEN_HMAC_KEY / MEDIA_URL_SECRET / STORAGE_BACKEND=cos …）
  见 deploy/.env.production.template 与 deploy/RUNBOOK.md。
NEXT
