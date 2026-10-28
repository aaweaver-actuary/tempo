import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { createPostgresBackup, markPostgresBackupVerified } from "../../database/tooling/cli/backup.mjs";

const script = readFileSync(new URL("../../database/tooling/cli/postgres-backup.sh", import.meta.url), "utf8");
const policy = script.split("# Operations run only")[0];
function action(mode, completed, pending = 0, recent = 0) {
  const result = spawnSync("sh", ["-ec", `${policy}\nbackup_action "$@"`, "sh", mode, String(completed), String(pending), String(recent)], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}

test("backup retention caps recurring copies at two and never silently blocks one maintenance replacement", () => {
  assert.equal(action("recurring", 0), "create");
  assert.equal(action("recurring", 1), "create");
  assert.equal(action("recurring", 2), "rotation");
  assert.equal(action("manual", 2), "stage");
  for (const mode of ["recurring", "manual"]) {
    assert.equal(action(mode, 3), "rotation");
    assert.equal(action(mode, 105), "rotation");
  }
});

test("backup restart within 24 hours skips a dump while explicit maintenance remains available", () => {
  assert.equal(action("recurring", 1, 0, 1), "recent");
  assert.equal(action("manual", 1, 0, 1), "create");
  assert.equal(action("manual", 2, 0, 1), "stage");
});

test("failed or unfinished backup staging prevents retries from accumulating or displacing valid copies", () => {
  for (const mode of ["recurring", "manual"]) {
    for (const count of [0, 1, 2, 3]) assert.equal(action(mode, count, 1), "pending");
  }
});

test("backup rotation preview and both creation paths preserve archives and share fail-closed ownership", () => {
  assert.equal(spawnSync("sh", ["-n"], { input: script, encoding: "utf8" }).status, 0);
  assert(!/\b(?:rm|unlink)\b|\s-delete\b/.test(script), "no archive removal operation");
  assert.match(script, /mkdir \.tempo-backup\.lock/);
  assert.match(script, /trap 'rmdir \.tempo-backup\.lock' EXIT/);
  assert(script.indexOf("preview)") < script.indexOf("mkdir .tempo-backup.lock"), "preview is read only");
  assert.match(script, /tempo-\*\.dump tempo-\*\.dump\.staged/);
  assert.match(script, /tempo-\*\.partial/);
  assert.match(script, /if \[ -f "\$file\.restore-verified" \]/);
  assert(script.indexOf("pg_restore -l") < script.indexOf('mv "$name.partial" "$name"'));
  const compose = readFileSync("docker-compose.yml", "utf8");
  assert.match(compose, /sh \/opt\/tempo\/postgres-backup\.sh recurring/);
  assert.match(compose, /postgres-backup\.sh:\/opt\/tempo\/postgres-backup\.sh:ro/);
  assert(!compose.includes("-delete"));
});

test("maintenance receipts use the confirmed staged filename and reject missing or unexpected confirmation", async () => {
  const name = "tempo-20261028T120000Z-test.dump";
  let invocation;
  const filename = await createPostgresBackup(async (args) => {
    invocation = args;
    return { stdout: `TEMPO_BACKUP_FILENAME=${name}.staged\n` };
  }, name, "tempo", "tempo");
  assert.equal(filename, `${name}.staged`);
  assert(invocation.includes(script));
  assert.deepEqual(invocation.slice(-5), ["sh", "manual", name, "tempo", "tempo"]);
  for (const stdout of ["", "TEMPO_BACKUP_FILENAME=tempo-other.dump\n"]) {
    await assert.rejects(createPostgresBackup(async () => ({ stdout }), name, "tempo", "tempo"), /not confirmed/);
  }
});

test("restore verification is recorded only after the existing real restore comparison and before verified receipts", async () => {
  const runtime = readFileSync("scripts/tempo-runtime.mjs", "utf8");
  const comparison = runtime.indexOf('await maintenance("database/tooling/verify_postgres_backup.py"');
  const marker = runtime.indexOf("await markPostgresBackupVerified(compose, name)");
  const receipt = runtime.indexOf("backupRecord.verified = true");
  assert(comparison > 0 && comparison < marker && marker < receipt);
  assert.match(runtime, /backupRecord\.filename = name; atomicJson/);
  let args;
  await markPostgresBackupVerified(async value => { args = value; }, "tempo-test.dump.staged");
  assert.match(args.at(-3), /sha256sum -c/);
  assert.match(args.at(-3), /touch "\$1\.restore-verified"/);
});
