#!/usr/bin/env bash
# ============================================================================
# 忆述光华 · 部署自证（S1 卡）
#
# 七项检查，逐项打印 PASS/FAIL，**收集全部结果后统一退出**（不用 set -e：排障时需要一次看全）。
#   1 容器在跑（postgres/redis/qdrant）      2 PostgreSQL 可连（pg_isready）
#   3 pgvector 扩展存在                     4 关键表存在（迁移已生效）
#   5 Redis PONG（RQ 队列后端）             6 Qdrant /healthz（REST 探活）
#   7 API /healthz == {"status":"ok"}
#
# 用法（仓库根）：bash deploy/scripts/healthcheck.sh
# 退出码：0=全绿；1=有 FAIL（输出末尾列出失败项，供 RUNBOOK 排障表引用）
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose -f "${DEPLOY_DIR}/docker-compose.yml")

API_URL="${API_URL:-http://127.0.0.1:8010}"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"

PG_USER="$(grep -E '^POSTGRES_USER=' "${DEPLOY_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
PG_DB="$(grep -E '^POSTGRES_DB=' "${DEPLOY_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-yishu}"

pass_count=0
fail_items=()

report() { # report <名称> <0|1> <详情>
	local name="$1" ok="$2" detail="${3:-}"
	if [[ "${ok}" -eq 0 ]]; then
		printf '  %-34s PASS  %s\n' "${name}" "${detail}"
		pass_count=$((pass_count + 1))
	else
		printf '  %-34s FAIL  %s\n' "${name}" "${detail}"
		fail_items+=("${name}")
	fi
}

printf '=== 忆述光华 部署自证（%s）===\n' "$(date '+%F %T')"

# 1. 容器在跑
running="$("${COMPOSE[@]}" ps --services --status running 2>/dev/null | tr '\n' ' ' | tr -s ' ')"
miss=""
for s in postgres redis qdrant; do
	[[ " ${running} " == *" ${s} "* ]] || miss="${miss} ${s}"
done
if [[ -z "${miss}" ]]; then
	report "1 依赖容器在跑" 0 "running: ${running}"
else
	report "1 依赖容器在跑" 1 "未运行:${miss} → docker compose -f deploy/docker-compose.yml up -d"
fi

# 2. PostgreSQL 可连
if "${COMPOSE[@]}" exec -T postgres pg_isready -U "${PG_USER}" -d "${PG_DB}" >/dev/null 2>&1; then
	report "2 PostgreSQL 可连" 0 "${PG_USER}@${PG_DB}"
else
	report "2 PostgreSQL 可连" 1 "pg_isready 失败（容器是否在跑？密码是否与 deploy/.env 一致？）"
fi

# 3. pgvector 扩展
has_vector="$("${COMPOSE[@]}" exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
	"SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | tr -d '[:space:]')"
if [[ "${has_vector}" == "1" ]]; then
	report "3 pgvector 扩展存在" 0 "extname=vector"
else
	report "3 pgvector 扩展存在" 1 "缺失 → CREATE EXTENSION vector;（见 deploy/initdb/01-extensions.sql）"
fi

# 4. 关键表
tbl_missing=""
for t in users devices contents events event_items sync_state; do
	found="$("${COMPOSE[@]}" exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
		"SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='${t}'" 2>/dev/null | tr -d '[:space:]')"
	[[ "${found}" == "1" ]] || tbl_missing="${tbl_missing} ${t}"
done
tbl_total="$("${COMPOSE[@]}" exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
	"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d '[:space:]')"
if [[ -z "${tbl_missing}" ]]; then
	report "4 关键表存在" 0 "public 表共 ${tbl_total} 张"
else
	report "4 关键表存在" 1 "缺失:${tbl_missing} → 跑 bash deploy/scripts/bootstrap.sh"
fi

# 5. Redis
pong="$("${COMPOSE[@]}" exec -T redis redis-cli ping 2>/dev/null | tr -d '[:space:]')"
if [[ "${pong}" == "PONG" ]]; then
	report "5 Redis 响应 PONG" 0 "（AOF 已开，策略 noeviction）"
else
	report "5 Redis 响应 PONG" 1 "返回 '${pong:-<空>}'"
fi

# 6. Qdrant
qdrant_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${QDRANT_URL}/healthz" 2>/dev/null || true)"
if [[ "${qdrant_code}" == "200" ]]; then
	report "6 Qdrant /healthz" 0 "${QDRANT_URL}/healthz → 200"
else
	report "6 Qdrant /healthz" 1 "HTTP ${qdrant_code:-<无响应>}（端口 6333 未绑 127.0.0.1？容器未起？）"
fi

# 7. API
api_body="$(curl -s --max-time 5 "${API_URL}/healthz" 2>/dev/null || true)"
if [[ "${api_body}" == '{"status":"ok"}' ]]; then
	report "7 API /healthz" 0 '{"status":"ok"}'
else
	report "7 API /healthz" 1 "响应 '${api_body:-<无响应>}'（systemd: systemctl status yishu-api）"
fi

printf '=== 结果：%d/7 PASS ===\n' "${pass_count}"
if [[ "${#fail_items[@]}" -gt 0 ]]; then
	printf '失败项：\n'
	for i in "${fail_items[@]}"; do printf '  - %s\n' "${i}"; done
	exit 1
fi
exit 0
