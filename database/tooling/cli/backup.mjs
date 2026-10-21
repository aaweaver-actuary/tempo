// Existing native backup commands; caller owns receipt updates and lifecycle order.
export function createPostgresBackup(compose, name, user, database) {
  return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec",
    'test ! -e "/backups/$1"; pg_dump -h postgres -U "$2" -d "$3" -Fc -f "/backups/$1.partial"; pg_restore -l "/backups/$1.partial" >/dev/null; mv "/backups/$1.partial" "/backups/$1"; cd /backups; sha256sum "$1" > "$1.sha256"; sha256sum -c "$1.sha256"',
    "sh", name, user, database,
  ], { echo: true });
}

export function createBackupRestoreDatabase(compose, user, restoreDatabase) {
  return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec",
    'createdb -h postgres -U "$1" "$2"', "sh", user, restoreDatabase], {});
}

export function restorePostgresBackup(compose, user, restoreDatabase, name) {
  return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec",
    'pg_restore -h postgres -U "$1" -d "$2" --no-owner --no-privileges --single-transaction "/backups/$3"',
    "sh", user, restoreDatabase, name], {});
}

export function removeBackupRestoreDatabase(compose, user, restoreDatabase) {
  return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec",
    'dropdb -h postgres -U "$1" "$2"', "sh", user, restoreDatabase], {});
}
