#!/bin/sh
# Kept for existing maintenance links. The CLI owns target checks and rollout.
set -eu
tempo_directory=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "${1:-}" in
  --plan) shift; exec "$tempo_directory/tempo" migrate --plan "$@" ;;
  --apply) shift; exec "$tempo_directory/tempo" migrate "$@" ;;
  *) echo "Usage: scripts/upgrade-postgres-schema.sh --plan | --apply" >&2; exit 2 ;;
esac
