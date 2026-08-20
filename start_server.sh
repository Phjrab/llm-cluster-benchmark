#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
find_project_root() {
  local current="$SCRIPT_DIR"

  while [[ "$current" != "/" ]]; do
    if [[ -d "$current/web" && -d "$current/models" ]]; then
      echo "$current"
      return 0
    fi
    current="$(dirname "$current")"
  done

  echo "$SCRIPT_DIR"
}

PROJECT_ROOT="$(find_project_root)"
cd "$PROJECT_ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RUN_DIR="$PROJECT_ROOT/.run"
PID_FILE="$RUN_DIR/chat_server.pid"
IDENTITY_FILE="$RUN_DIR/chat_server.identity.json"
LOG_FILE="$RUN_DIR/chat_server.log"
LOCK_FILE="$RUN_DIR/chat_server.lock"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] .venv not found in $PROJECT_ROOT"
  exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "[ERROR] flock is required for safe server lifecycle management" >&2
  exit 1
fi
PROCESS_GUARD=(
  "$PYTHON_BIN" -m cluster.infrastructure.process_guard
  --pid-file "$PID_FILE"
  --identity-file "$IDENTITY_FILE"
  --cwd "$PROJECT_ROOT"
  --python "$PYTHON_BIN"
  --module web.app
  --host "$HOST"
  --port "$PORT"
)
exec 9>"$LOCK_FILE"
chmod 600 "$LOCK_FILE"
if ! flock -w 30 9; then
  echo "[ERROR] another server lifecycle operation is still running" >&2
  exit 1
fi
export VIRTUAL_ENV="$PROJECT_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"

server_health_ok() {
  "$PYTHON_BIN" - "$HOST" "$PORT" <<'PY'
import json
import sys
import urllib.request

host = "127.0.0.1" if sys.argv[1] == "0.0.0.0" else sys.argv[1]
url = f"http://{host}:{int(sys.argv[2])}/health"
try:
    with urllib.request.urlopen(url, timeout=1.0) as response:
        body = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if isinstance(body, dict) and body.get("ok") is True else 1)
PY
}

server_process_owns_port() {
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
    if server_process_owns_port "$existing_pid" \
      && server_health_ok \
      && server_process_owns_port "$existing_pid"; then
      echo "[INFO] Server already running (PID=$existing_pid)"
      echo "[INFO] URL: http://$HOST:$PORT"
      exit 0
    fi
    echo "[INFO] Replacing unhealthy tracked server (PID=$existing_pid)"
    set +e
    "${PROCESS_GUARD[@]}" stop >/dev/null
    replace_status=$?
    set -e
    if [[ "$replace_status" -ne 0 && "$replace_status" -ne 3 ]]; then
      echo "[ERROR] Unhealthy server could not be stopped safely" >&2
      exit 1
    fi
    ;;
  3)
    ;;
  *)
    echo "[ERROR] Server process metadata is unsafe; no process was signalled" >&2
    exit 1
    ;;
esac

touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
nohup "$PYTHON_BIN" -m uvicorn web.app:app --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 9>&- &
server_pid="$!"
if ! "${PROCESS_GUARD[@]}" record --pid "$server_pid" >/dev/null; then
  echo "[ERROR] Failed to capture server process identity" >&2
  "${PROCESS_GUARD[@]}" terminate-candidate --pid "$server_pid" >/dev/null || true
  exit 1
fi

for _ in $(seq 1 30); do
  if server_process_owns_port "$server_pid" \
    && server_health_ok \
    && server_process_owns_port "$server_pid"; then
    set +e
    verified_pid="$("${PROCESS_GUARD[@]}" status)"
    verified_status=$?
    set -e
    if [[ "$verified_status" -eq 0 && "$verified_pid" == "$server_pid" ]]; then
      echo "[OK] Server started (PID=$server_pid)"
      echo "[OK] URL: http://$HOST:$PORT"
      echo "[OK] Log: $LOG_FILE"
      exit 0
    fi
  fi
  set +e
  "${PROCESS_GUARD[@]}" status >/dev/null 2>&1
  running_status=$?
  set -e
  [[ "$running_status" -eq 0 ]] || break
  sleep 0.2
done

echo "[ERROR] Server process started but health check failed."
tail -n 40 "$LOG_FILE" || true
set +e
"${PROCESS_GUARD[@]}" stop >/dev/null
rollback_status=$?
set -e
if [[ "$rollback_status" -ne 0 && "$rollback_status" -ne 3 ]]; then
  echo "[ERROR] Failed server rollback could not verify process identity" >&2
fi
exit 1
