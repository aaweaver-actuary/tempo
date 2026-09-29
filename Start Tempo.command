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
    if (( attempt == 60 )); then
      api_id="$(docker compose ps -a -q api 2>/dev/null)"
      if [[ -n "$api_id" && "$(docker inspect --format '{{.State.Restarting}}' "$api_id" 2>/dev/null)" == "true" ]]; then
        break
      fi
    fi
    sleep 1
  done
  echo "Tempo has not become ready. API health response, if the process is listening:"
  curl --silent --show-error --include --max-time 5 http://127.0.0.1:8000/api/health || true
  echo "Recent API startup errors:"
  docker compose logs --no-color --tail=45 api || true
  echo "See docs/STARTUP-BACKGROUND-RECOVERY.md before any schema upgrade."
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
if ! docker compose up --build; then
  echo "Tempo did not start. Recent API startup errors:"
  docker compose logs --no-color --tail=45 api || true
  curl --silent --show-error --include --max-time 5 http://127.0.0.1:8000/api/health || true
  exit 1
fi
