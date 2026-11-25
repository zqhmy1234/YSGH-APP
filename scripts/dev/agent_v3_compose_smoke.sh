#!/usr/bin/env bash
# L0 compose smoke for Agent V3: infrastructure + agent + optional live model probe.
#
# Unlike scripts/dev/agent_v3_smoke.sh (process-only, fake LLM), this script
# checks the real compose stack in deploy/compose.agent-v3.yml:
#   postgres(pgvector) / redis / qdrant / minio / agent-v3
#
# Usage (repo root):
#   bash scripts/dev/agent_v3_compose_smoke.sh            # requires stack already up
#   SKIP_UP=0 bash scripts/dev/agent_v3_compose_smoke.sh  # also runs compose up -d
#   LINGQU_LIVE=1 COMPANY_LLM_API_KEY=... bash scripts/dev/agent_v3_compose_smoke.sh
#
# Every port is bound to 127.0.0.1. The report is written to
# docs/agent-v3/l0-compose-smoke.json.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}" || exit 1
COMPOSE=(docker compose -f deploy/compose.agent-v3.yml)
REPORT="${ROOT}/docs/agent-v3/l0-compose-smoke.json"
FAIL=0
RESULTS=()

report() {
  local name="$1" code="$2" detail="${3:-}"
  RESULTS+=("${code}|${name}|${detail}")
  if [[ "${code}" == "0" ]]; then echo "PASS  ${name}  ${detail}"; else echo "FAIL  ${name}  ${detail}"; FAIL=$((FAIL+1)); fi
}

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
  report "docker" 1 "docker not installed / not in PATH"
  write_report "blocked" "docker_missing"
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  report "docker daemon" 1 "docker CLI present but daemon unreachable"
  write_report "blocked" "docker_daemon_unreachable"
  exit 2
fi

if [[ "${SKIP_UP:-1}" != "1" ]]; then
  echo "==> docker compose up -d (no build)"
  "${COMPOSE[@]}" up -d || { report "compose up" 1 "up failed"; write_report "fail" "compose_up_failed"; exit 1; }
fi

# The agent API is token-protected when AGENT_V3_TOKEN is set. Read the same
# value the container got, so the smoke works without printing it.
AGENT_TOKEN="${AGENT_V3_TOKEN:-}"
if [[ -z "${AGENT_TOKEN}" ]] && docker inspect yishu-v3-agent >/dev/null 2>&1; then
  AGENT_TOKEN="$(docker inspect yishu-v3-agent --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^AGENT_V3_TOKEN=//p' | head -1)"
fi
AUTH=()
[[ -n "${AGENT_TOKEN}" ]] && AUTH=(-H "X-Agent-Token: ${AGENT_TOKEN}")

echo "==> wait for agent-v3 /health (max 90s)"
ok=0
for _ in $(seq 1 45); do
  if curl -fsS -m 3 "http://127.0.0.1:${V3_AGENT_PORT:-8300}/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ "${ok}" == "1" ]] && report "agent /health" 0 "$(curl -fsS -m 3 http://127.0.0.1:${V3_AGENT_PORT:-8300}/health)" || report "agent /health" 1 "90s 内未就绪"

models="$(curl -fsS -m 10 "${AUTH[@]}" "http://127.0.0.1:${V3_AGENT_PORT:-8300}/v1/v3/models" 2>/dev/null || true)"
if [[ -n "${models}" ]]; then
  provider_count="$(printf '%s' "${models}" | python3 -c 'import json,sys;print(len({p["provider"] for p in json.load(sys.stdin)["profiles"]}))' 2>/dev/null || echo 0)"
  default_profile="$(printf '%s' "${models}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["default_profile"])' 2>/dev/null || echo "")"
  if [[ "${provider_count}" == "5" && "${default_profile}" == "deepseek/deepseek-v4.1-flash" ]]; then
    report "Lingqu catalog" 0 "5 providers, default ${default_profile}"
  else
    report "Lingqu catalog" 1 "providers=${provider_count} default=${default_profile}"
  fi
else
  report "Lingqu catalog" 1 "/v1/v3/models 无响应（检查 X-Agent-Token）"
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
    probe="$(curl -fsS -m 120 -X POST "${AUTH[@]}" "http://127.0.0.1:${V3_AGENT_PORT:-8300}/v1/v3/models/probe" -H 'content-type: application/json' -d '{"profile_id":"deepseek/deepseek-v4.1-flash"}' 2>/dev/null || true)"
    echo "${probe}" | grep -q '"state":"ok"'
    report "live model probe" $? "DeepSeek: $(echo "${probe}" | head -c 160)"
  fi
else
  echo "SKIP  live model probe  （设置 LINGQU_LIVE=1 + COMPANY_LLM_API_KEY 才发真实请求）"
fi

echo
if [[ "${FAIL}" -eq 0 ]]; then echo "L0 compose smoke: ALL PASS"; write_report "pass" "${SKIP_UP:-1}" >/dev/null; write_report "pass"; exit 0; fi
echo "L0 compose smoke: ${FAIL} FAIL"; write_report "fail" "${FAIL} checks failed"; exit 1
