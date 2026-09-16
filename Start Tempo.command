#!/bin/zsh
set -e

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"
mkdir -p data

open_when_ready() {
  for attempt in {1..900}; do
    if curl --silent --fail http://127.0.0.1:3000 >/dev/null 2>&1 && curl --silent --fail http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      open http://localhost:3000
      return
    fi
    sleep 1
  done
  echo "Tempo has not become ready yet. Check the startup messages in this window."
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "Starting Tempo with Docker. This window can stay open while you use the app."
  open_when_ready &
  docker compose up --build
  exit
fi

echo "Docker is not running, so Tempo will start directly on this Mac."
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Tempo needs either Docker Desktop or Node.js 22 or newer."
  echo "Install Docker Desktop, open it, and double-click this file again."
  read "?Press Return to close."
  exit 1
fi

if [[ ! -d node_modules ]]; then
  echo "Installing the web app for the first run…"
  npm install
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Preparing the local API for the first run…"
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi

cleanup() {
  kill "$API_PID" "$WEB_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "Starting the local database and web app…"
TEMPO_DB_PATH="$PROJECT_DIR/data/tempo.db" PYTHONPATH="$PROJECT_DIR/backend" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 3000 &
WEB_PID=$!
open_when_ready &

echo "Tempo will open at http://localhost:3000. Press Control-C here to stop it."
wait
