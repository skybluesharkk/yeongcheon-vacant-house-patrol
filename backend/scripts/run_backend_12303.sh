#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
PID_FILE="$ROOT_DIR/.backend-${PORT}.pid"
LOG_FILE="$ROOT_DIR/backend-${PORT}.log"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID"
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

if command -v lsof >/dev/null 2>&1; then
  for PID in $(lsof -ti tcp:"$PORT" 2>/dev/null || true); do
    kill "$PID" 2>/dev/null || true
  done
  sleep 1
fi

cd "$ROOT_DIR"
if command -v setsid >/dev/null 2>&1; then
  setsid python3 backend/server.py --host "$HOST" --port "$PORT" > "$LOG_FILE" 2>&1 &
else
  nohup python3 backend/server.py --host "$HOST" --port "$PORT" > "$LOG_FILE" 2>&1 &
fi
echo "$!" > "$PID_FILE"
echo "backend started: http://$HOST:$PORT pid=$(cat "$PID_FILE") log=$LOG_FILE"
