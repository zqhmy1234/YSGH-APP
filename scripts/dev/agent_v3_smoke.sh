#!/usr/bin/env bash
# L0 smoke for Agent V3: infrastructure + Lingqu model catalog.
# This never sends a real LLM request unless LINGQU_LIVE=1 and a company key is set.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}" || exit 1
COMPOSE=(docker compose -f deploy/compose.agent-v3.yml)
REPORT="${ROOT}/docs/agent-v3/l0-smoke.json"
FAIL=0
RESULTS=()
report() { local name="$1" code="$2" detail="${3:-}"; RESULTS+=("${code}|${name}|${detail}"); if [[ "${code}" == "0" ]]; then echo "PASS  ${name}  ${detail}"; else echo "FAIL  ${name}  ${detail}"; FAIL=$((FAIL+1)); fi; }
write_report() {
  local status="$1" note="${2:-}"
  mkdir -p "$(dirname "${REPORT}")"
  python3 - "${REPORT}" "${status}" "${note}" "${RESULTS[@]:-}" <<'PY'
import json, sys
path, status, note, *rows = sys.argv[1:]
checks = []
for row in rows:
    if not row:
        continue
    code, name, detail = row.split("|", 2)
    checks.append({"name": name, "ok": code == "0", "detail": detail})
payload = {"status": status, "note": note, "checks": checks}
open(path, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
PY
}

if ! command -v docker >/dev/null 2>&1; then
  echo "FAIL  docker 未安装或不在 PATH（跳过 L0 运行，先装 Docker Desktop/Engine）" >&2
  report "docker" 1 "docker not installed / not in PATH"
  write_report "blocked" "docker_missing"
  exit 2
fi

if [[ "${SKIP_UP:-0}" != "1" ]]; then
  echo "==> docker compose up -d --build"
  "${COMPOSE[@]}" up -d --build || { echo "FAIL  compose up"; report "compose" 1 "up failed"; write_report "fail" "compose_up_failed"; exit 1; }
fi

echo "==> 等待 agent-v3 /health（最多 90s）"
ok=0
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${V3_AGENT_PORT:-8300}/health" >/tmp/yishu-v3-health.json 2>/dev/null; then ok=1; break; fi
  sleep 2
done
if [[ "${ok}" == "1" ]]; then report "agent /health" 0 "$(cat /tmp/yishu-v3-health.json)"; else report "agent /health" 1 "90s 内未就绪（看 docker logs yishu-v3-agent）"; fi

models="$(curl -fsS "http://127.0.0.1:${V3_AGENT_PORT:-8300}/v1/v3/models" 2>/dev/null || true)"
if [[ -n "${models}" ]]; then
  provider_count="$(printf '%s' "${models}" | python3 -c 'import json,sys;print(len({p["provider"] for p in json.load(sys.stdin)["profiles"]}))' 2>/dev/null || echo 0)"
  default_profile="$(printf '%s' "${models}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["default_profile"])' 2>/dev/null || echo "")"
  if [[ "${provider_count}" == "5" && "${default_profile}" == "deepseek/deepseek-v4.1-flash" ]]; then
    report "Lingqu catalog" 0 "5 providers, default ${default_profile}"
  else
    report "Lingqu catalog" 1 "providers=${provider_count} default=${default_profile}"
  fi
else
  report "Lingqu catalog" 1 "/v1/v3/models 无响应"
fi

"${COMPOSE[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-yishu}" >/dev/null 2>&1
report "postgres" $? "pg_isready"

"${COMPOSE[@]}" exec -T redis redis-cli ping 2>/dev/null | grep -q PONG
report "redis" $? "PING/PONG"

curl -fsS "http://127.0.0.1:${V3_QDRANT_HTTP_PORT:-56333}/readyz" >/dev/null 2>&1
report "qdrant" $? "/readyz"

curl -fsS "http://127.0.0.1:${V3_MINIO_PORT:-59000}/minio/health/live" >/dev/null 2>&1
report "minio" $? "/minio/health/live"

if [[ "${LINGQU_LIVE:-0}" == "1" ]]; then
  if [[ -z "${COMPANY_LLM_API_KEY:-}" ]]; then
    report "live model probe" 1 "LINGQU_LIVE=1 但 COMPANY_LLM_API_KEY 未设置"
  else
    probe="$(curl -fsS -X POST "http://127.0.0.1:${V3_AGENT_PORT:-8300}/v1/v3/models/probe" -H 'content-type: application/json' -d '{"profile_id":"deepseek/deepseek-v4.1-flash"}' 2>/dev/null || true)"
    echo "${probe}" | grep -q '"state":"ok"'
    report "live model probe" $? "DeepSeek"
  fi
else
  echo "SKIP  live model probe  （设置 LINGQU_LIVE=1 + COMPANY_LLM_API_KEY 才发真实请求）"
fi

echo
if [[ "${FAIL}" -eq 0 ]]; then echo "L0 smoke: ALL PASS"; write_report "pass"; exit 0; fi
echo "L0 smoke: ${FAIL} FAIL"; write_report "fail" "${FAIL} checks failed"; exit 1
