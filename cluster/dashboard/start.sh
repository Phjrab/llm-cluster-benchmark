#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
RUN_DIR="$PROJECT_ROOT/.run/cluster"
PID_FILE="$RUN_DIR/dashboard_${PORT}.pid"
LOG_FILE="$RUN_DIR/dashboard_${PORT}.log"
TOKEN_FILE="$RUN_DIR/dashboard.token"

mkdir -p "$RUN_DIR"
if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "[ERROR] virtual environment missing: $PROJECT_ROOT/.venv" >&2
  exit 1
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    token="$(cat "$TOKEN_FILE" 2>/dev/null || true)"
    echo "[INFO] dashboard already running (PID=$existing_pid, port=$PORT)"
    echo "[INFO] URL: http://$(hostname -I | awk '{print $1}'):$PORT/?token=$token"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$PROJECT_ROOT"
PYTHONDONTWRITEBYTECODE=1 nohup "$PROJECT_ROOT/.venv/bin/python" -m uvicorn cluster.dashboard.app:app \
  --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
dashboard_pid="$!"
echo "$dashboard_pid" > "$PID_FILE"

health_host="$HOST"
[[ "$health_host" == "0.0.0.0" ]] && health_host="127.0.0.1"
for _ in $(seq 1 60); do
  if curl -fsS "http://$health_host:$PORT/dashboard/health" >/dev/null 2>&1; then
    token="$(cat "$TOKEN_FILE")"
    lan_ip="$(hostname -I | awk '{print $1}')"
    echo "[OK] dashboard started (PID=$dashboard_pid)"
    echo "[OK] URL: http://$lan_ip:$PORT/?token=$token"
    echo "[OK] log=$LOG_FILE"
    exit 0
  fi
  if ! kill -0 "$dashboard_pid" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

echo "[ERROR] dashboard health check failed" >&2
tail -n 60 "$LOG_FILE" >&2 || true
rm -f "$PID_FILE"
exit 1
