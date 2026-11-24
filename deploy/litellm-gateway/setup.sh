#!/usr/bin/env bash
# Deploy the YiShu LiteLLM gateway on the existing server.
# Idempotent: safe to re-run. Secrets are generated on the server and never printed.
set -euo pipefail

ROOT="${LITELLM_ROOT:-/opt/yishu/litellm}"
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PG_CONTAINER="${PG_CONTAINER:-yishu-postgres}"
PORT="${LITELLM_PORT:-4000}"
BIND="${LITELLM_BIND:-127.0.0.1}"
ENABLE_DB="${LITELLM_ENABLE_DB:-0}"
DB_URL=""

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

mkdir -p "$ROOT"
if [[ "$ENABLE_DB" == "1" ]]; then
  install -m 0644 "$PKG_DIR/config.with-db.yaml" "$ROOT/config.yaml"
else
  install -m 0644 "$PKG_DIR/config.yaml" "$ROOT/config.yaml"
fi
install -m 0644 "$PKG_DIR/yishu-litellm.service" "/etc/systemd/system/yishu-litellm.service"

# ---------- Python runtime ----------
if [[ ! -x "$ROOT/.venv/bin/litellm" ]]; then
  echo "[1/6] creating Python venv"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -U pip setuptools wheel
  "$ROOT/.venv/bin/pip" install -q 'litellm[proxy]'
else
  echo "[1/6] Python runtime already present"
fi

# Prisma is required only when virtual keys/budgets are enabled.
if [[ "$ENABLE_DB" == "1" ]] && ! "$ROOT/.venv/bin/python" -c 'import prisma' >/dev/null 2>&1; then
  echo "[1b/6] installing Prisma CLI for LiteLLM database"
  "$ROOT/.venv/bin/pip" install -q 'litellm[extra_proxy]'
fi

# ---------- PostgreSQL database (optional) ----------
if [[ "$ENABLE_DB" == "1" ]]; then
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    echo "postgres container $PG_CONTAINER not running" >&2
    exit 1
  fi

  DB_PASS="$(openssl rand -hex 24)"
  DB_URL="postgresql://litellm:${DB_PASS}@127.0.0.1:5432/litellm"

  echo "[2/6] ensuring role/database (password rotated)"
  docker exec -i "$PG_CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'litellm') THEN
    ALTER ROLE litellm WITH LOGIN PASSWORD '$DB_PASS';
  ELSE
    CREATE ROLE litellm LOGIN PASSWORD '$DB_PASS';
  END IF;
END
\$\$;
SQL

  if ! docker exec "$PG_CONTAINER" psql -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='litellm'" | grep -q 1; then
    docker exec "$PG_CONTAINER" psql -U postgres -d postgres -c \
      "CREATE DATABASE litellm OWNER litellm"
  fi
fi

# ---------- environment ----------
if [[ ! -f "$ROOT/.env" ]]; then
  echo "[3/6] creating .env"
  MASTER="sk-$(openssl rand -hex 32)"
  SALT="sk-$(openssl rand -hex 32)"
  DS_KEY="$(awk -F= '/^DASHSCOPE_API_KEY=/{sub(/^[^=]*=/,""); print}' /opt/yishu/deploy/.env 2>/dev/null || true)"
  DS_WS="$(awk -F= '/^DASHSCOPE_WORKSPACE_ID=/{sub(/^[^=]*=/,""); print}' /opt/yishu/deploy/.env 2>/dev/null || true)"
  DS_REGION="$(awk -F= '/^DASHSCOPE_REGION=/{sub(/^[^=]*=/,""); print}' /opt/yishu/deploy/.env 2>/dev/null || true)"
  : "${DS_REGION:=cn-beijing}"
  if [[ -n "$DS_WS" ]]; then
    DS_BASE="https://${DS_WS}.${DS_REGION}.maas.aliyuncs.com/compatible-mode/v1"
  else
    DS_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
  fi
  umask 077
  {
    echo "LITELLM_MASTER_KEY=$MASTER"
    echo "LITELLM_SALT_KEY=$SALT"
    if [[ "$ENABLE_DB" == "1" ]]; then echo "DATABASE_URL=$DB_URL"; fi
    echo "DEEPSEEK_API_KEY="
    echo "DASHSCOPE_API_KEY=$DS_KEY"
    echo "DASHSCOPE_BASE_URL=$DS_BASE"
  } > "$ROOT/.env"
  chmod 600 "$ROOT/.env"
else
  if [[ "$ENABLE_DB" == "1" ]]; then
    echo "[3/6] .env exists; updating database URL"
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=$DB_URL|" "$ROOT/.env"
  else
    echo "[3/6] .env exists; no-DB mode, disabling DATABASE_URL"
    sed -i "s|^DATABASE_URL=.*|# DATABASE_URL disabled for no-DB mode|" "$ROOT/.env"
  fi
  chmod 600 "$ROOT/.env"
fi

# ---------- service ----------
echo "[4/6] starting systemd service"
systemctl daemon-reload
systemctl enable yishu-litellm >/dev/null 2>&1 || true
systemctl restart yishu-litellm

echo "[5/6] waiting for health"
ok=0
for _ in $(seq 1 90); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT}/health/liveliness" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" -ne 1 ]]; then
  echo "gateway did not become healthy; last logs:" >&2
  tail -40 /var/log/yishu-litellm.log >&2 || true
  exit 1
fi

# ---------- virtual key for agents ----------
echo "[6/6] creating agent access key"
MASTER="$(awk -F= '/^LITELLM_MASTER_KEY=/{sub(/^[^=]*=/,""); print}' "$ROOT/.env")"
if [[ "$ENABLE_DB" == "1" ]]; then
  RESP="$ROOT/.key-response.json"
  curl -fsS -X POST "http://127.0.0.1:${PORT}/key/generate" \
    -H "Authorization: Bearer $MASTER" \
    -H "Content-Type: application/json" \
    -d '{"key_alias":"agent-v3","models":["deepseek-v4.1-flash","deepseek-v4-flash","deepseek-v4-pro","qwen3.8-flash","qwen4-max"],"max_budget":20,"budget_duration":"30d"}' \
    > "$RESP"
  KEY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["key"])' "$RESP")"
  rm -f "$RESP"
  echo "virtual key created (DB mode)"
else
  KEY="$MASTER"
  echo "no-DB mode: agent.key is the master key; enable DB for per-agent keys/budgets"
fi
printf '%s' "$KEY" > "$ROOT/agent.key"
chmod 600 "$ROOT/agent.key"
FP="$(sha256sum "$ROOT/agent.key" | cut -c1-8)"

echo
echo "gateway:  http://${BIND}:${PORT}/v1"
echo "admin UI: http://${BIND}:${PORT}/ui  (use SSH tunnel for remote access)"
echo "agent key file: $ROOT/agent.key (mode 600, sha256 prefix $FP)"
echo "Do not paste the key into chat or git."
