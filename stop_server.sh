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
PORT="${PORT:-8000}"
RUN_DIR="$PROJECT_ROOT/.run"
PID_FILE="$RUN_DIR/chat_server.pid"
IDENTITY_FILE="$RUN_DIR/chat_server.identity.json"
LOCK_FILE="$RUN_DIR/chat_server.lock"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] .venv not found in $PROJECT_ROOT" >&2
  exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "[ERROR] flock is required for safe server lifecycle management" >&2
  exit 1
fi
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
PROCESS_GUARD=(
  "$PYTHON_BIN" -m cluster.infrastructure.process_guard
  --pid-file "$PID_FILE"
  --identity-file "$IDENTITY_FILE"
  --cwd "$PROJECT_ROOT"
  --python "$PYTHON_BIN"
  --module web.app
  --port "$PORT"
)
exec 9>"$LOCK_FILE"
chmod 600 "$LOCK_FILE"
if ! flock -w 30 9; then
  echo "[ERROR] another server lifecycle operation is still running" >&2
  exit 1
fi

cd "$PROJECT_ROOT"
set +e
tracked_pid="$("${PROCESS_GUARD[@]}" status --adopt)"
guard_status=$?
set -e

case "$guard_status" in
  0)
    set +e
    stopped_pid="$("${PROCESS_GUARD[@]}" stop)"
    stop_status=$?
    set -e
    if [[ "$stop_status" -ne 0 ]]; then
      echo "[ERROR] Server process identity changed; no further signal was sent" >&2
      exit 1
    fi
    [[ "$stopped_pid" == "$tracked_pid" ]] || {
      echo "[ERROR] Server process identity changed during stop" >&2
      exit 1
    }
    echo "[OK] Sent stop signal to PID=$stopped_pid"
    echo "[OK] Server stop requested"
    ;;
  3)
    echo "[INFO] No running server found on port $PORT"
    ;;
  *)
    echo "[ERROR] Server process metadata is unsafe; no process was signalled" >&2
    exit 1
    ;;
esac
