#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RUN_DIR="$PROJECT_ROOT/.run/cluster"
PID_FILE="$RUN_DIR/worker_server_${PORT}.pid"
LOG_FILE="$RUN_DIR/worker_server_${PORT}.log"

mkdir -p "$RUN_DIR"
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "[ERROR] virtual environment missing: $PROJECT_ROOT/.venv" >&2
  exit 1
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "[INFO] worker API already running (PID=$existing_pid, port=$PORT)"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$PROJECT_ROOT"
PYTHONDONTWRITEBYTECODE=1 nohup "$PROJECT_ROOT/.venv/bin/python" -m uvicorn cluster.worker.app:app \
  --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
worker_pid="$!"
echo "$worker_pid" > "$PID_FILE"

health_host="$HOST"
[[ "$health_host" == "0.0.0.0" ]] && health_host="127.0.0.1"
for _ in $(seq 1 60); do
  if curl -fsS "http://$health_host:$PORT/cluster/health" >/dev/null 2>&1; then
    echo "[OK] worker API started (PID=$worker_pid, port=$PORT)"
    echo "[OK] log=$LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$worker_pid" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

echo "[ERROR] worker API health check failed" >&2
tail -n 50 "$LOG_FILE" >&2 || true
rm -f "$PID_FILE"
exit 1
