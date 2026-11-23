#!/usr/bin/env bash
# Enable LiteLLM Prisma/Postgres mode (virtual keys, budgets, spend logs).
# Idempotent-ish helper for /opt/yishu/litellm. Never prints secrets.
set -euo pipefail

ROOT="${LITELLM_ROOT:-/opt/yishu/litellm}"
PKG_DIR="${LITELLM_PKG_DIR:-/opt/yishu/litellm-gateway}"
SRC_CONFIG="${1:-$PKG_DIR/config.with-db.yaml}"
DB_URL_FILE="$ROOT/.database_url.saved"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

[[ "$(id -u)" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -x "$VENV/bin/litellm" ]] || { echo "missing $VENV/bin/litellm" >&2; exit 1; }
[[ -f "$SRC_CONFIG" ]] || { echo "missing db config: $SRC_CONFIG" >&2; exit 1; }
[[ -s "$DB_URL_FILE" ]] || { echo "missing $DB_URL_FILE" >&2; exit 1; }

DB_URL="$(sed -n 's/^DATABASE_URL=//p' "$DB_URL_FILE" | head -1)"
[[ -n "$DB_URL" ]] || { echo "empty DATABASE_URL in $DB_URL_FILE" >&2; exit 1; }

STAMP="$(date +%Y%m%d%H%M%S)"
install -m 0644 "$ROOT/config.yaml" "$ROOT/config.pre-db-$STAMP.yaml" 2>/dev/null || true
cp -a "$ROOT/.env" "$ROOT/.env.pre-db-$STAMP"
chmod 600 "$ROOT/.env.pre-db-$STAMP"

echo "[1/5] ensuring python toolchain + prisma client"
"$PY" - <<'PY'
import importlib.util
for name in ("prisma", "litellm_proxy_extras.prisma_toolchain"):
    if importlib.util.find_spec(name) is None:
        raise SystemExit(f"missing python package: {name}")
print("python prisma packages ok")
PY

# Node toolchain is bootstrapped lazily by litellm-proxy-extras; run once here so
# the first systemd start is not the thing that pays the npm-install cost.
export PATH="$VENV/bin:/root/.cache/prisma-python/nodeenv/bin:$PATH"
(cd "$VENV/lib/python3.12/site-packages/litellm/proxy" && prisma generate >/dev/null)
echo "prisma client generated"

echo "[2/5] writing DATABASE_URL into $ROOT/.env (no echo)"
if grep -q '^DATABASE_URL=' "$ROOT/.env"; then
  TMP="$(mktemp)"
  awk -v url="$DB_URL" 'BEGIN{done=0} /^DATABASE_URL=/{print "DATABASE_URL=" url; done=1; next} {print} END{if(!done) print "DATABASE_URL=" url}' "$ROOT/.env" > "$TMP"
  install -m 600 "$TMP" "$ROOT/.env"
  rm -f "$TMP"
else
  printf 'DATABASE_URL=%s\n' "$DB_URL" >> "$ROOT/.env"
  chmod 600 "$ROOT/.env"
fi

echo "[3/5] switching config.yaml to DB variant"
install -m 0644 "$SRC_CONFIG" "$ROOT/config.yaml"

echo "[4/5] restarting gateway"
systemctl daemon-reload
systemctl restart yishu-litellm

echo "[5/5] health check"
ok=0
for _ in $(seq 1 90); do
  if curl -fsS -m 3 "http://127.0.0.1:4000/health/liveliness" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
[[ "$ok" -eq 1 ]] || { echo "gateway unhealthy; tail:" >&2; tail -60 /var/log/yishu-litellm.log >&2; exit 1; }

echo "db mode enabled"
echo "backups: $ROOT/config.pre-db-$STAMP.yaml $ROOT/.env.pre-db-$STAMP"
echo "next: create the agent key via /key/generate, then write it to $ROOT/agent.key (mode 600)"
