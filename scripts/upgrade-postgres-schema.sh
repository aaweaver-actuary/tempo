#!/bin/sh
# Run only in an approved maintenance window against the verified product target.
set -eu

if [ "${1:-}" != "--apply" ] || [ "$#" -ne 1 ]; then
  echo "Usage: scripts/upgrade-postgres-schema.sh --apply (after target approval)" >&2
  exit 2
fi

: "${TEMPO_UPGRADE_EXPECTED_PROJECT:?Set the approved Compose project name}"
docker compose config --format json | python3 -c '
import json, os, sys
configuration = json.load(sys.stdin)
expected = os.environ["TEMPO_UPGRADE_EXPECTED_PROJECT"]
if configuration.get("name") != expected:
    raise SystemExit(f"Compose target differs from approved project {expected}")
for volume in ("tempo-postgres-data", "tempo-postgres-backups", "tempo-redis-data", "tempo-engine-operations"):
    configured = configuration.get("volumes", {}).get(volume, {})
    if not configured.get("external") or configured.get("name") != volume:
        raise SystemExit(f"Unexpected product volume mapping: {volume}")
'
backup_name="tempo-upgrade-$(date -u +%Y%m%dT%H%M%SZ).dump"
restore_database="tempo_upgrade_restore"

docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml build migration
docker compose build api foreground-worker background-worker background-scheduler web defense-engine maia-worker
docker compose stop web defense-engine maia-worker api foreground-worker background-worker background-scheduler
docker compose up -d postgres redis postgres-backup

docker compose exec -T postgres-backup sh -ec "pg_dump -h postgres -U tempo -d tempo -Fc -f /backups/$backup_name && pg_restore -l /backups/$backup_name >/dev/null && sha256sum /backups/$backup_name > /backups/$backup_name.sha256 && cd /backups && sha256sum -c $backup_name.sha256"
docker compose exec -T postgres-backup sh -ec "createdb -h postgres -U tempo $restore_database && pg_restore -h postgres -U tempo -d $restore_database --no-owner --no-privileges /backups/$backup_name"
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm --no-deps migration scripts/verify_postgres_backup.py postgresql://tempo@postgres:5432/tempo "postgresql://tempo@postgres:5432/$restore_database"
docker compose exec -T postgres-backup dropdb -h postgres -U tempo "$restore_database"

# A nonzero migration result exits before any application service starts.
docker compose -f docker-compose.yml -f docker-compose.postgres-maintenance.yml --profile maintenance run --rm --no-deps migration scripts/apply_postgres_migrations.py
docker compose up -d foreground-worker background-worker background-scheduler api

attempt=0
health_body=""
while [ "$attempt" -lt 60 ]; do
  health_body="$(curl --silent --show-error --include --max-time 10 http://127.0.0.1:8000/api/health)" || health_body=""
  if printf '%s\n' "$health_body" | head -n 1 | grep -q ' 200 '; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done
printf '%s\n' "$health_body"
if [ "$attempt" -eq 60 ]; then
  docker compose logs --no-color --tail=60 api
  exit 1
fi

docker compose up -d web defense-engine maia-worker
docker compose ps -a
echo "Schema upgrade startup complete. Verify repeated health and foreground saves before ending maintenance."
