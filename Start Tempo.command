#!/bin/zsh
set -e

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

open_when_ready() {
  for attempt in {1..900}; do
    if curl --silent --fail http://127.0.0.1:3000 >/dev/null 2>&1 && curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      open http://localhost:3000
      return
    fi
    sleep 1
  done
  echo "Tempo has not become ready yet. Run 'docker compose logs api foreground-worker background-worker' and check /api/health."
}

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "Tempo now requires Docker Desktop. Start Docker Desktop and double-click this file again."
  read "?Press Return to close."
  exit 1
fi

if ! docker compose config >/dev/null; then
  echo "PostgreSQL setup is incomplete. Check docs/POSTGRES-MAINTENANCE.md for external volumes and secret-file paths."
  read "?Press Return to close."
  exit 1
fi

echo "Starting Tempo with Docker and PostgreSQL. This window can stay open while you use the app."
open_when_ready &
READY_PID=$!
trap 'kill "$READY_PID" >/dev/null 2>&1 || true' EXIT INT TERM
docker compose up --build
