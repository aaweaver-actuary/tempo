#!/bin/sh
set -eu

# Pure admission policy, shared by recurring and explicit maintenance creation.
backup_action() {
  mode=$1 completed=$2 pending=$3 recent=$4
  if [ "$pending" -gt 0 ]; then echo pending; return; fi
  if [ "$completed" -gt 2 ]; then echo rotation; return; fi
  if [ "$mode" = recurring ] && [ "$completed" -eq 2 ]; then echo rotation; return; fi
  if [ "$mode" = recurring ] && [ "$recent" -gt 0 ]; then echo recent; return; fi
  if [ "$completed" -eq 2 ]; then echo stage; else echo create; fi
}

# Operations run only when this script is executed, never during policy tests.
mode=${1:?Expected recurring, manual or preview}
cd /backups
inventory() {
  completed=0 pending=0 recent=0
  for file in tempo-*.dump tempo-*.dump.staged; do
    [ -f "$file" ] || continue
    status=restore-verification-unrecorded
    case "$file" in
      *.staged) status=awaiting-restore-verification ;;
    esac
    if [ -f "$file.restore-verified" ]; then status=restore-verified; fi
    if [ "$status" = awaiting-restore-verification ]; then
      pending=$((pending + 1))
    else
      completed=$((completed + 1))
    fi
    if [ -n "$(find "$file" -mmin -1440 -print)" ]; then recent=1; fi
    if [ "$mode" = preview ]; then printf '%s: %s\n' "$status" "$file"; fi
  done
  for file in tempo-*.partial; do
    [ -e "$file" ] || continue
    pending=$((pending + 1))
    if [ "$mode" = preview ]; then printf 'incomplete: %s\n' "$file"; fi
  done
}

case "$mode" in
  preview)
    inventory
    printf 'Completed copies: %s; incomplete/staged: %s; retained limit: 2.\n' "$completed" "$pending"
    [ ! -d .tempo-backup.lock ] || echo 'Backup ownership lock present; creation may be active or interrupted.'
    echo 'No files changed. Select any permanent removal yourself after checking restore verification and operation/migration receipts; keep two usable copies. One verified replacement may temporarily raise the count to three.'
    exit 0 ;;
  recurring|manual) ;;
  *) echo 'Unknown backup operation.' >&2; exit 1 ;;
esac

# Fail closed after an unclean exit; never steal an uncertain owner's lock.
mkdir .tempo-backup.lock 2>/dev/null || { echo 'Backup owner active or interrupted; inspect ownership and preview before retrying.' >&2; exit 1; }
trap 'rmdir .tempo-backup.lock' EXIT
trap 'trap - EXIT; echo "Interrupted backup; inspect the retained ownership lock and partial before retrying." >&2; exit 1' HUP INT TERM

inventory
action=$(backup_action "$mode" "$completed" "$pending" "$recent")
case "$action" in
  pending|rotation)
    echo "Backup paused ($action): $completed completed copies, $pending incomplete/staged. Run the rotation preview; user-controlled rotation is required. No copies removed." >&2
    [ "$mode" = recurring ] && exit 0
    exit 1 ;;
  recent) echo 'Backup not due: an archive was created within 24 hours.' >&2; exit 0 ;;
esac
if [ "$mode" = manual ]; then
  name=${2:?Expected archive name}
  user=${3:?Expected PostgreSQL user}
  database=${4:?Expected PostgreSQL database}
else
  name=tempo-$(date -u +%Y%m%dT%H%M%SZ).dump
  user=tempo database=tempo
fi
case "$name" in tempo-*.dump) ;; *) echo 'Invalid backup name.' >&2; exit 1 ;; esac
case "$name" in *[!A-Za-z0-9.-]*) exit 1 ;; esac
if [ "$action" = stage ]; then name=$name.staged; fi
test ! -e "$name"
test ! -e "$name.partial"
test ! -e "$name.sha256"
test ! -e "$name.restore-verified"
pg_dump -h postgres -U "$user" -d "$database" -Fc -f "$name.partial"
pg_restore -l "$name.partial" >/dev/null
sha256sum "$name.partial" | sed "s/  .*/  $name/" > "$name.sha256.partial"
mv "$name.partial" "$name"
mv "$name.sha256.partial" "$name.sha256"
sha256sum -c "$name.sha256" >&2
if [ "$action" = stage ]; then echo 'Replacement staged; preserve both retained copies until restore verification succeeds, then rotate manually.' >&2; fi
printf 'TEMPO_BACKUP_FILENAME=%s\n' "$name"
