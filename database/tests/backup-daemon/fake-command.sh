#!/bin/bash
# Fixture-only commands. Bash's timed read avoids creating sleeping descendants.
set -eu
command_name=$1
shift

record() {
  printf '%s\n' "$1" >> /state/events
}

settle() {
  trap '' TERM INT
  record "$command_name:signal:$1"
  IFS= read -r -t 0.3 -u 3 ignored || :
  record "$command_name:settled"
  printf 'settled\n' > "/state/$command_name.settled"
  # The observer must see settlement while PID 1 is still waiting for us.
  for ((attempt = 0; attempt < 100; attempt++)); do
    if [ -f "/state/$command_name.observed" ]; then exit 0; fi
    IFS= read -r -t 0.05 -u 3 ignored || :
  done
  record "$command_name:observation-timeout"
  exit 70
}

block_until_signaled() {
  fifo=/tmp/backup-child-$$
  /usr/bin/mkfifo "$fifo"
  exec 3<> "$fifo"
  /usr/bin/rm "$fifo"
  trap 'settle TERM' TERM
  trap 'settle INT' INT
  record "$command_name:started"
  printf '%s\n' "$$" > "/state/$command_name.started"
  while :; do IFS= read -r -t 30 -u 3 ignored || :; done
}

case "$command_name" in
  pg_dump)
    output=
    while [ "$#" -gt 0 ]; do
      if [ "$1" = -f ]; then output=$2; shift; fi
      shift
    done
    case "$output" in /backups/tempo-*.dump.partial) ;; *) exit 64 ;; esac
    printf 'synthetic PostgreSQL archive\n' > "$output"
    record pg_dump:invoked
    if [ "$BACKUP_FIXTURE_CASE" = dump ]; then block_until_signaled; fi
    ;;
  pg_restore)
    [ "$#" -eq 2 ] && [ "$1" = -l ] && [ -s "$2" ]
    record pg_restore:validated
    ;;
  sleep)
    [ "$#" -eq 1 ] && [ "$1" = 86400 ]
    block_until_signaled
    ;;
  *) exit 64 ;;
esac
