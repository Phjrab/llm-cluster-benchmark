#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT="${PORT:-8080}"
PID_FILE="$PROJECT_ROOT/.run/cluster/dashboard_${PORT}.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "[INFO] no dashboard PID file for port $PORT"
  exit 0
fi

dashboard_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -n "$dashboard_pid" ]] && kill -0 "$dashboard_pid" 2>/dev/null; then
  kill "$dashboard_pid"
  for _ in $(seq 1 40); do
    if ! kill -0 "$dashboard_pid" 2>/dev/null; then
      break
    fi
    sleep 0.25
  done
  if kill -0 "$dashboard_pid" 2>/dev/null; then
    echo "[WARN] dashboard did not stop after SIGTERM (PID=$dashboard_pid)" >&2
    exit 1
  fi
  echo "[OK] dashboard stopped (PID=$dashboard_pid)"
else
  echo "[INFO] stale dashboard PID file removed"
fi
rm -f "$PID_FILE"
