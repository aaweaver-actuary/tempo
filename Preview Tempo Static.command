#!/bin/zsh
set -e

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

fail() {
  echo "$1"
  read "?Press Return to close."
  exit 1
}

command -v node >/dev/null 2>&1 || fail "Tempo's static preview needs Node.js 22 or newer."
command -v npm >/dev/null 2>&1 || fail "Tempo's static preview needs npm."
command -v cargo >/dev/null 2>&1 || fail "Tempo's static preview needs Rust and Cargo."
command -v wasm-pack >/dev/null 2>&1 || fail "Tempo's static preview needs wasm-pack. Install it from https://rustwasm.github.io/wasm-pack/installer/."

if [[ ! -d node_modules ]]; then
  echo "Installing the web app for the first run…"
  npm install
fi

NEEDS_BUILD=0
if [[ ! -f pages-dist/index.html ]]; then
  NEEDS_BUILD=1
else
  for source_path in app public static tempo-core package.json package-lock.json vite.static.config.ts; do
    if [[ -e "$source_path" ]] && [[ -n "$(find "$source_path" -type f -newer pages-dist/index.html -print -quit)" ]]; then
      NEEDS_BUILD=1
      break
    fi
  done
fi

if (( NEEDS_BUILD )); then
  echo "Building the static Tempo app…"
  npm run build:static
  npm run verify:static
fi

open_when_ready() {
  for attempt in {1..30}; do
    if curl --silent --fail http://127.0.0.1:4173/tempo/ >/dev/null 2>&1; then
      open http://127.0.0.1:4173/tempo/
      return
    fi
    sleep 1
  done
}

echo "Serving Tempo at http://127.0.0.1:4173/tempo/. Press Control-C to stop it."
open_when_ready &
npm run preview:static
