#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RUN_DIR="$PROJECT_ROOT/.run/cluster"
PID_FILE="$RUN_DIR/worker_server_${PORT}.pid"
IDENTITY_FILE="$RUN_DIR/worker_server_${PORT}.identity.json"
LOG_FILE="$RUN_DIR/worker_server_${PORT}.log"
LOCK_FILE="$RUN_DIR/worker_server_${PORT}.lock"
TOKEN_FILE="$RUN_DIR/worker.token"
SETTINGS_FILE="$RUN_DIR/settings.json"

mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "[ERROR] virtual environment missing: $PROJECT_ROOT/.venv" >&2
  exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "[ERROR] flock is required for safe worker lifecycle management" >&2
  exit 1
fi
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
PROCESS_GUARD=(
  "$PYTHON_BIN" -m cluster.infrastructure.process_guard
  --pid-file "$PID_FILE"
  --identity-file "$IDENTITY_FILE"
  --cwd "$PROJECT_ROOT"
  --python "$PYTHON_BIN"
  --module cluster.worker.app
  --host "$HOST"
  --port "$PORT"
)
exec 9>"$LOCK_FILE"
chmod 600 "$LOCK_FILE"
if ! flock -w 30 9; then
  echo "[ERROR] another worker lifecycle operation is still running" >&2
  exit 1
fi
if [[ -z "${CLUSTER_WORKER_AUTH:-}" ]]; then
  CLUSTER_WORKER_AUTH="false"
  if [[ -f "$SETTINGS_FILE" ]]; then
    CLUSTER_WORKER_AUTH="$($PROJECT_ROOT/.venv/bin/python - "$SETTINGS_FILE" <<'PY'
import json
import sys
try:
    document = json.load(open(sys.argv[1], encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("settings must be an object")
    raw = document.get("worker_api_auth", False)
    value = raw if isinstance(raw, bool) else "worker_api_auth" in document
except (OSError, ValueError):
    # An existing but unreadable security document must not silently disable
    # protection. A missing settings file is handled by the shell default.
    value = True
print("true" if value else "false")
PY
)"
  fi
fi
if [[ "$CLUSTER_WORKER_AUTH" == "true" && ! -f "$TOKEN_FILE" ]]; then
  "$PROJECT_ROOT/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))' >"$TOKEN_FILE"
fi
CLUSTER_API_TOKEN=""
if [[ -f "$TOKEN_FILE" ]]; then
  chmod 600 "$TOKEN_FILE"
  CLUSTER_API_TOKEN="$(tr -d '\r\n' <"$TOKEN_FILE")"
fi
export CLUSTER_API_TOKEN
export CLUSTER_WORKER_AUTH

health_host="$HOST"
[[ "$health_host" == "0.0.0.0" ]] && health_host="127.0.0.1"
worker_health_ok() {
  local authenticated_health curl_token unauthenticated_code
  curl_token="${CLUSTER_API_TOKEN//\\/\\\\}"
  curl_token="${curl_token//\"/\\\"}"
  authenticated_health="$(
    printf 'header = "X-Cluster-Worker-Token: %s"\n' "$curl_token" \
      | curl --config - --fail --silent --show-error "http://$health_host:$PORT/cluster/health" 2>/dev/null \
      || true
  )"
  unauthenticated_code="$(curl -sS -o /dev/null -w '%{http_code}' "http://$health_host:$PORT/cluster/health" 2>/dev/null || true)"
  { [[ "$CLUSTER_WORKER_AUTH" == "true" && "$unauthenticated_code" == "401" ]] || [[ "$CLUSTER_WORKER_AUTH" != "true" && "$unauthenticated_code" == "200" ]]; } \
    && printf '%s' "$authenticated_health" | grep -q "\"worker_api_auth\":$CLUSTER_WORKER_AUTH"
}

worker_process_owns_port() {
  local expected_pid=$1 owning_pid
  owning_pid="$("${PROCESS_GUARD[@]}" owns-port 2>/dev/null)" || return 1
  [[ "$owning_pid" == "$expected_pid" ]]
}

set +e
existing_pid="$("${PROCESS_GUARD[@]}" status --adopt)"
guard_status=$?
set -e
case "$guard_status" in
  0)
    if worker_process_owns_port "$existing_pid" \
      && worker_health_ok \
      && worker_process_owns_port "$existing_pid"; then
      echo "[INFO] worker API already running and authenticated (PID=$existing_pid, port=$PORT)"
      exit 0
    fi
    echo "[INFO] replacing stale or unauthenticated worker API (PID=$existing_pid)"
    set +e
    "${PROCESS_GUARD[@]}" stop >/dev/null
    replace_status=$?
    set -e
    if [[ "$replace_status" -ne 0 && "$replace_status" -ne 3 ]]; then
      echo "[ERROR] stale worker API did not stop; refusing to start a duplicate process" >&2
      exit 1
    fi
    ;;
  3)
    ;;
  *)
    echo "[ERROR] worker process metadata is unsafe; no process was signalled" >&2
    exit 1
    ;;
esac

cd "$PROJECT_ROOT"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
PYTHONDONTWRITEBYTECODE=1 nohup "$PYTHON_BIN" -m uvicorn cluster.worker.app:app \
  --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 9>&- &
worker_pid="$!"
if ! "${PROCESS_GUARD[@]}" record --pid "$worker_pid" >/dev/null; then
  echo "[ERROR] worker API identity capture failed" >&2
  "${PROCESS_GUARD[@]}" terminate-candidate --pid "$worker_pid" >/dev/null || true
  exit 1
fi

for _ in $(seq 1 60); do
  if worker_process_owns_port "$worker_pid" \
    && worker_health_ok \
    && worker_process_owns_port "$worker_pid"; then
    set +e
    verified_pid="$("${PROCESS_GUARD[@]}" status)"
    verified_status=$?
    set -e
    if [[ "$verified_status" -eq 0 && "$verified_pid" == "$worker_pid" ]]; then
      echo "[OK] worker API started (PID=$worker_pid, port=$PORT)"
      echo "[OK] log=$LOG_FILE"
      exit 0
    fi
  fi
  set +e
  "${PROCESS_GUARD[@]}" status >/dev/null 2>&1
  running_status=$?
  set -e
  if [[ "$running_status" -ne 0 ]]; then
    break
  fi
  sleep 0.25
done

echo "[ERROR] worker API health check failed" >&2
tail -n 50 "$LOG_FILE" >&2 || true
set +e
"${PROCESS_GUARD[@]}" stop >/dev/null
rollback_status=$?
set -e
if [[ "$rollback_status" -ne 0 && "$rollback_status" -ne 3 ]]; then
  echo "[ERROR] worker API rollback could not verify the spawned process identity" >&2
fi
exit 1
