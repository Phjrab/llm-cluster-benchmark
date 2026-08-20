#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
PORT="${PORT:-8000}"
RUN_DIR="$PROJECT_ROOT/.run/cluster"
PID_FILE="$RUN_DIR/worker_server_${PORT}.pid"
IDENTITY_FILE="$RUN_DIR/worker_server_${PORT}.identity.json"
LOCK_FILE="$RUN_DIR/worker_server_${PORT}.lock"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] virtual environment missing: $PROJECT_ROOT/.venv" >&2
  exit 1
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "[ERROR] flock is required for safe worker lifecycle management" >&2
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
  --module cluster.worker.app
  --port "$PORT"
)
exec 9>"$LOCK_FILE"
chmod 600 "$LOCK_FILE"
if ! flock -w 30 9; then
  echo "[ERROR] another worker lifecycle operation is still running" >&2
  exit 1
fi

had_pid_file=false
[[ -f "$PID_FILE" ]] && had_pid_file=true
set +e
tracked_pid="$("${PROCESS_GUARD[@]}" status --adopt)"
guard_status=$?
set -e

case "$guard_status" in
  0)
    set +e
    worker_pid="$("${PROCESS_GUARD[@]}" stop)"
    stop_status=$?
    set -e
    if [[ "$stop_status" -ne 0 || "$worker_pid" != "$tracked_pid" ]]; then
      echo "[ERROR] worker process identity changed; no further signal was sent" >&2
      exit 1
    fi
    echo "[OK] worker API stopped (PID=$worker_pid)"
    ;;
  3)
    if [[ "$had_pid_file" == "true" ]]; then
      echo "[INFO] stale worker API PID file removed"
    else
      echo "[INFO] no worker API PID file for port $PORT"
    fi
    ;;
  *)
    echo "[ERROR] worker process metadata is unsafe; no process was signalled" >&2
    exit 1
    ;;
esac
