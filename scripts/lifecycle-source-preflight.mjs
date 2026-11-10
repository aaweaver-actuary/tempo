// Source-only checks: no Docker, temporary resources, or lifecycle mutation.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { pathToFileURL } from "node:url";

export function renderInterruptedLifecycle(root) {
  return `
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { commandExecutor } from ${JSON.stringify(join(root, "scripts/tempo-deployment.mjs"))};
import { createRuntime, executeLifecycle } from ${JSON.stringify(join(root, "scripts/tempo-runtime.mjs"))};
const [registration, stateDirectory, override, prepared] = process.argv.slice(2);
const target = JSON.parse(readFileSync(registration, "utf8"));
target.composeFiles.push(override);
const preparedImages = JSON.parse(readFileSync(prepared, "utf8"));
const execute = commandExecutor({ root: target.root, environment: process.env });
const run = async (command, args, options) => {
  const result = await execute(command, args, options);
  if (args.includes("up") && args.includes("foreground-worker")) process.kill(process.pid, "SIGKILL");
  return result;
};
const runtime = createRuntime(target, { run, stateDirectory, revision: preparedImages.revision,
  evidence: { commit: preparedImages.revision }, preparedImages });
await runtime.inspectTarget();
await executeLifecycle({ recreate: false }, runtime);
`;
}

export const schema62BridgeRevision = "0765a54e4a404d187f044dde2f902075b4a5e038";
export const schema62BridgeTree = "5a48d33a64180be6cfe67e6bdd4996ddeed4bfe0";
const schema62Roles = ["schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "migration", "game-feedback-worker"];
export function readSchema62Bridge(environment = process.env, { runGit = spawnSync } = {}) {
  if (["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"].some(name => environment[name]))
    throw new Error("Schema-62 bridge requires a named Docker context without host or TLS overrides.");
  const root = environment.TEMPO_LIFECYCLE_SCHEMA62_ROOT, file = environment.TEMPO_LIFECYCLE_SCHEMA62_IMAGES;
  if (!root || !isAbsolute(root) || resolve(root) !== root || !file || !isAbsolute(file))
    throw new Error("Schema-63 lifecycle requires an absolute schema-62 source root and image manifest.");
  if (realpathSync(root) !== root) throw new Error("Schema-62 source root must be a canonical directory.");
  const git = args => {
    const result = runGit("git", ["-C", root, ...args], { encoding: "utf8", timeout: 15_000 });
    if (result.error || result.status !== 0) throw new Error("Schema-62 source checkout cannot be verified.");
    return result.stdout.trim();
  };
  if (git(["rev-parse", "HEAD"]) !== schema62BridgeRevision || git(["rev-parse", "HEAD^{tree}"]) !== schema62BridgeTree ||
      git(["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"]))
    throw new Error("Schema-62 source checkout is stale or dirty.");
  const worktrees = git(["worktree", "list", "--porcelain"]).split(/\n\s*\n/).map(block => block.split("\n"));
  const matching = worktrees.filter(lines => lines.includes(`worktree ${root}`));
  if (matching.length !== 1 || !matching[0].includes(`HEAD ${schema62BridgeRevision}`) || !matching[0].includes("detached") ||
      matching[0].some(line => line.startsWith("branch ")))
    throw new Error("Schema-62 source must be a registered detached worktree at the exact revision.");
  const version = readFileSync(join(root, "database/tempo_legacy_database/ddl/schema_version.py"), "utf8");
  if (!/^POSTGRES_SCHEMA_VERSION\s*=\s*62\s*$/m.test(version)) throw new Error("Schema-62 source version differs.");
  let manifest;
  try { manifest = JSON.parse(readFileSync(file, "utf8")); } catch { throw new Error("Schema-62 image manifest is unavailable or invalid."); }
  if (manifest?.version !== 1 || manifest?.kind !== "local-seed" || manifest?.revision !== schema62BridgeRevision || manifest?.tree !== schema62BridgeTree || manifest?.platform !== "linux/arm64" ||
      !/^https:\/\/\S+$/.test(manifest.provenance?.receipt ?? "") || !manifest.images ||
      !isDeepStrictEqual(Object.keys(manifest.images).sort(), [...schema62Roles].sort()) || manifest.images.schema !== manifest.images.api ||
      schema62Roles.some(role => !/^sha256:[0-9a-f]{64}$/.test(manifest.images[role] ?? "")))
    throw new Error("Schema-62 image manifest revision or roles differ.");
  return { root, manifest };
}
export function assertBridgeTargetIdentity(previous, next, { previousRoot, currentRoot } = {}) {
  if (!previous || !next || !previousRoot || !currentRoot || previousRoot === currentRoot ||
      previous.root !== previousRoot || next.root !== currentRoot || previous.project !== next.project ||
      previous.context !== next.context || previous.daemonId !== next.daemonId ||
      previous.postgresVolumeKey !== next.postgresVolumeKey || previous.postgresMajor !== next.postgresMajor ||
      previous.webUrl !== next.webUrl || !isDeepStrictEqual(previous.volumes, next.volumes) ||
      !isDeepStrictEqual(previous.ports, next.ports))
    throw new Error("Schema-62 to current target identity differs.");
}

export function assertBridgePreparedBinding(binding, revision, images, fingerprint) {
  if (!binding || binding.revision !== revision || !/^[0-9a-f]{64}$/.test(fingerprint ?? "") ||
      binding.configFingerprint !== fingerprint || !isDeepStrictEqual(binding.images, images))
    throw new Error("Schema-63 candidate image or configuration binding differs.");
}

export function checkLifecycleSource({ root = process.cwd(), environment = process.env, render = renderInterruptedLifecycle } = {}) {
  const syntax = spawnSync(process.execPath, ["--check", "--input-type=module"], { input: render(root), encoding: "utf8" });
  if (syntax.status !== 0) throw new Error(`Invalid generated interruption harness: ${syntax.stderr || syntax.error?.message}`);
  const schemaFile = join(root, "database/tempo_legacy_database/ddl/schema_version.py");
  const schema63 = existsSync(schemaFile) && /^POSTGRES_SCHEMA_VERSION\s*=\s*63\s*$/m.test(readFileSync(schemaFile, "utf8"));
  if (schema63 && environment.TEMPO_LIFECYCLE_P0_59_ONLY === "1" &&
      (environment.TEMPO_LIFECYCLE_MANAGED_REHEARSAL === "1" || environment.TEMPO_LIFECYCLE_SHIPPING_REHEARSAL === "1"))
    throw new Error("Schema-63 continuation of the schema-59 managed rehearsal is not qualified; its original guard must be preserved.");
  if (schema63 || environment.TEMPO_LIFECYCLE_SCHEMA62_ROOT || environment.TEMPO_LIFECYCLE_SCHEMA62_IMAGES)
    readSchema62Bridge(environment);
  const schema59 = environment.TEMPO_LIFECYCLE_P0_59_ONLY === "1";
  const file = schema59 ? environment.TEMPO_LIFECYCLE_SCHEMA59_IMAGES : environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES;
  // The schema-61 path may acquire its manifest later, after quiet admission.
  if (!file) {
    if (schema59) throw new Error("Provide the task-owned schema-59 image manifest.");
    return;
  }
  let manifest;
  try { manifest = JSON.parse(readFileSync(file, "utf8")); }
  catch (error) { throw new Error(`Cannot read historical image manifest: ${error.message}`); }
  const revision = schema59 ? "5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a" : "4aa0ad1b731402365c1b866a6ba7bed484d2b6ac";
  if (manifest?.revision !== revision) throw new Error("Unexpected historical image revision.");
  for (const role of ["api", "web", "migration"])
    if (typeof manifest.images?.[role] !== "string" || !manifest.images[role].trim())
      throw new Error(`Missing historical image for ${role}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  checkLifecycleSource();
  console.log("PASS lifecycle source preflight");
}
