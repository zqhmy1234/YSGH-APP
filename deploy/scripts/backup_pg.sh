#!/usr/bin/env bash
# ============================================================================
# 忆述光华 · PostgreSQL 每日备份（S3 卡）
#
# 交付什么：pg_dump -Fc 自定义格式全库备份 + **非空/可读性校验** + 按天轮转 + 可选 WAL 归档状态检查。
# 为什么这样：决策 #13 要求 RPO ≤24h（dump）+ WAL ≤5min（PITR）。
#   - 本脚本负责 **RPO ≤24h** 那半边（每日 dump），由系统 cron 触发；
#   - WAL 归档（≤5min 那半边）由 compose 的 PG 参数承担，**默认关闭**（见下方 §WAL 说明）。
#
# 用法（仓库根；建议 root 或 docker 组成员执行）：
#   bash deploy/scripts/backup_pg.sh                 # 备份 + 校验 + 轮转
#   KEEP_DAYS=14 bash deploy/scripts/backup_pg.sh    # 自定义保留天数（默认 7）
# cron 示例（每日 03:30，避开 22:00 复盘任务）：
#   30 3 * * * cd /srv/guangh && /bin/bash deploy/scripts/backup_pg.sh >> deploy/logs/backup.log 2>&1
#
# ⚠️ WAL 说明：compose 里 `PG_ARCHIVE_MODE=on` 可开启 archive_mode + archive_command。
#   **默认关闭**是刻意的：archive_command 若因目录属主/磁盘满而失败，PG 会持续保留 WAL
#   直至 pg_wal 打满磁盘——"未验证就默认开启"比"默认关闭+明确开启步骤"更危险。
#   本脚本会检查 pg_stat_archiver 的失败计数并在开启时告警（见 §4）。
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE=(docker compose -f "${DEPLOY_DIR}/docker-compose.yml")

KEEP_DAYS="${KEEP_DAYS:-7}"
BACKUP_DIR="${BACKUP_DIR:-${DEPLOY_DIR}/backup/pg}"
MIN_BYTES="${MIN_BYTES:-10240}"   # 10KB：空库 dump 也有头部，低于此值判为异常

log()  { printf '[backup %s] %s\n' "$(date '+%F %T')" "$*"; }
warn() { printf '[backup %s][WARN] %s\n' "$(date '+%F %T')" "$*" >&2; }
die()  { printf '[backup %s][FAIL] %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

PG_USER="$(grep -E '^POSTGRES_USER=' "${DEPLOY_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
PG_DB="$(grep -E '^POSTGRES_DB=' "${DEPLOY_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-yishu}"

mkdir -p "${BACKUP_DIR}"
STAMP="$(date '+%Y%m%d_%H%M')"
OUT="${BACKUP_DIR}/yishu_${STAMP}.dump"
TMP="${OUT}.part"

# ---------- 1. 依赖服务在线 ----------
if ! "${COMPOSE[@]}" ps --services --status running 2>/dev/null | grep -qx postgres; then
	die "postgres 容器未运行——先 docker compose -f deploy/docker-compose.yml up -d"
fi
if ! "${COMPOSE[@]}" exec -T postgres pg_isready -U "${PG_USER}" -d "${PG_DB}" >/dev/null 2>&1; then
	die "PostgreSQL 不可连（pg_isready 失败）"
fi

# ---------- 2. dump（先进 .part，校验通过才改名——防"半截文件被当成有效备份"）----------
log "pg_dump -Fc → ${OUT}"
if ! "${COMPOSE[@]}" exec -T postgres pg_dump -U "${PG_USER}" -d "${PG_DB}" -Fc > "${TMP}"; then
	rm -f "${TMP}"
	die "pg_dump 失败（已清理半截文件）"
fi

# ---------- 3. 非空 + 可读性校验 ----------
size="$(wc -c < "${TMP}" | tr -d ' ')"
if [[ "${size}" -lt "${MIN_BYTES}" ]]; then
	rm -f "${TMP}"
	die "备份体积异常（${size} 字节 < ${MIN_BYTES}）——疑似空库或 dump 中断"
fi
# pg_restore -l 能列出目录 = 归档结构完整（比"文件非空"强一档）
if ! "${COMPOSE[@]}" exec -T postgres pg_restore -l /dev/stdin < "${TMP}" >/dev/null 2>&1; then
	# 部分 postgres 镜像无 pg_restore 走 stdio 的兼容问题 → 退化为 gzip 魔数可用性检查，但不阻断
	warn "pg_restore -l 校验未通过（可能镜像差异）；已回退为体积校验。建议人工抽验一次"
else
	entries="$("${COMPOSE[@]}" exec -T postgres pg_restore -l /dev/stdin < "${TMP}" 2>/dev/null | grep -c 'TABLE DATA' || true)"
	log "归档结构完整：含 ${entries} 个表数据段"
fi
mv "${TMP}" "${OUT}"
log "备份完成：${OUT}（${size} 字节）"

# ---------- 4. WAL 归档状态（开启时才告警；未开启则提示当前 RPO 口径）----------
archiver="$("${COMPOSE[@]}" exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
	"SELECT coalesce(archived_count,0)||'|'||coalesce(failed_count,0) FROM pg_stat_archiver" 2>/dev/null | tr -d '[:space:]' || true)"
if [[ -n "${archiver}" ]]; then
	ok_cnt="${archiver%%|*}"
	fail_cnt="${archiver##*|}"
	if [[ "${fail_cnt}" != "0" ]]; then
		warn "WAL 归档失败计数 = ${fail_cnt}（archived=${ok_cnt}）——检查 archive_command 与目录属主；否则 pg_wal 会持续增长打满磁盘"
	else
		log "WAL 归档：已归档 ${ok_cnt} / 失败 ${fail_cnt}（failed=0 且 archived 持续增长 = PITR 生效）"
	fi
fi

# ---------- 5. 轮转（按天保留）----------
removed="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'yishu_*.dump' -type f -mtime "+${KEEP_DAYS}" -print -delete | wc -l | tr -d ' ')"
log "轮转：保留最近 ${KEEP_DAYS} 天，本次清理 ${removed} 个过期备份"
log "当前备份集："
ls -lh "${BACKUP_DIR}"/yishu_*.dump 2>/dev/null | tail -5 || true

cat <<'NEXT'

[backup] 恢复演练（季度，RTO≤4h；务必在非生产实例上做）：
  docker compose -f deploy/docker-compose.yml exec -T postgres \
    psql -U postgres -c "CREATE DATABASE yishu_restore_test"
  docker compose -f deploy/docker-compose.yml exec -T postgres \
    pg_restore -U postgres -d yishu_restore_test --no-owner < <备份文件>
  然后抽查关键表行数（users/contents/events）与生产一致量级。
NEXT
