import { readFileSync } from "node:fs";

// Inline the same script used by recurring Compose, including on an older
// recorded Compose configuration. No source mount is needed for CLI operation.
const backupScript = readFileSync(new URL("./postgres-backup.sh", import.meta.url), "utf8");
export async function createPostgresBackup(compose, name, user, database) {
  const result = await compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec",
    backupScript, "sh", "manual", name, user, database,
  ], { echo: true });
  const filename = result.stdout.trim().match(/^TEMPO_BACKUP_FILENAME=(tempo-[A-Za-z0-9-]+\.dump(?:\.staged)?)$/m)?.[1];
  if (!filename || (filename !== name && filename !== `${name}.staged`))
    throw new Error("Backup filename was not confirmed; preserve the archive and inspect the rotation preview.");
  return filename;
}

export function markPostgresBackupVerified(compose, name) {
  return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec",
    'cd /backups; test -s "$1"; sha256sum -c "$1.sha256"; touch "$1.restore-verified"', "sh", name], { echo: true });
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
