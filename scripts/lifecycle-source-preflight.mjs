// Source-only checks: no Docker, temporary resources, or lifecycle mutation.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { pathToFileURL } from "node:url";
import { buildMaterials } from "./release-candidate-artifacts.mjs";

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
export const schema65BaselineRevision = "d79ae4fcc4b006985114d85b5fb9a5aca8becbf1";
export const schema65BaselineTree = "3cf62fd976b2f8623f3eb88f576b30639c1d62f4";
// This is a disposable-source and immutable-image binding, never a live target.
export function readSchema65Baseline(environment = process.env, { runGit = spawnSync, verifyBuildMaterials = buildMaterials } = {}) {
  if (["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"].some(name => environment[name])) throw new Error("Schema-65 baseline requires a named Docker context without overrides.");
  const root = environment.TEMPO_LIFECYCLE_SCHEMA65_ROOT, file = environment.TEMPO_LIFECYCLE_SCHEMA65_IMAGES;
  if (!root || !isAbsolute(root) || resolve(root) !== root || !file || !isAbsolute(file)) throw new Error("Schema-65 baseline requires absolute source and image manifest paths.");
  if (realpathSync(root) !== root) throw new Error("Schema-65 source root must be canonical.");
  const git = args => { const result = runGit("git", ["-C", root, ...args], { encoding: "utf8", timeout: 15_000 });
    if (result.error || result.status !== 0) throw new Error("Schema-65 source checkout cannot be verified."); return result.stdout.trim(); };
  if (git(["rev-parse", "HEAD"]) !== schema65BaselineRevision || git(["rev-parse", "HEAD^{tree}"]) !== schema65BaselineTree || git(["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"])) throw new Error("Schema-65 source checkout is stale or dirty.");
  const matching = git(["worktree", "list", "--porcelain"]).split(/\n\s*\n/).map(block => block.split("\n")).filter(lines => lines.includes(`worktree ${root}`));
  if (matching.length !== 1 || !matching[0].includes(`HEAD ${schema65BaselineRevision}`) || !matching[0].includes("detached") || matching[0].some(line => line.startsWith("branch "))) throw new Error("Schema-65 source must be a registered detached exact worktree.");
  if (!/^POSTGRES_SCHEMA_VERSION\s*=\s*65\s*$/m.test(readFileSync(join(root, "database/tempo_storage/ddl/schema_version.py"), "utf8"))) throw new Error("Schema-65 source version differs.");
  let manifest; try { manifest = JSON.parse(readFileSync(file, "utf8")); } catch { throw new Error("Schema-65 image manifest is unavailable or invalid."); }
  const roles = ["schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "migration"];
  if (manifest?.version !== 1 || manifest?.kind !== "local-seed" || manifest?.revision !== schema65BaselineRevision || manifest?.tree !== schema65BaselineTree || manifest?.platform !== "linux/arm64" || !manifest.images || !isDeepStrictEqual(Object.keys(manifest.images).sort(), roles.sort()) || manifest.images.schema !== manifest.images.api || roles.some(role => !/^sha256:[0-9a-f]{64}$/.test(manifest.images[role] ?? ""))) throw new Error("Schema-65 image manifest revision or roles differ.");
  if (manifest.provenance?.source_root !== root || !manifest.provenance.metadata) throw new Error("Schema-65 image build provenance is missing.");
  for (const [name, dockerfile, role] of [["backend", "backend/Dockerfile", "api"], ["web", "Dockerfile.web", "web"], ["migration", "Dockerfile.postgres-maintenance", "migration"]]) {
    let metadata; try { metadata = JSON.parse(readFileSync(join(dirname(file), `${name}-build.json`), "utf8")); }
    catch { throw new Error(`Schema-65 ${name} build metadata is unavailable.`); }
    const observed = verifyBuildMaterials(metadata, { dockerfile: readFileSync(join(root, dockerfile), "utf8"), platform: "linux/arm64", expectedImageId: manifest.images[role] });
    if (!isDeepStrictEqual(observed, manifest.provenance.metadata[name])) throw new Error(`Schema-65 ${name} build materials differ.`);
  }
  if (["foreground-worker", "background-worker", "background-scheduler"].some(role => manifest.images[role] !== manifest.images.api)) throw new Error("Schema-65 backend role images differ from the built source artifact.");
  return { root, manifest };
}
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

export function checkLifecycleSource({ root = process.cwd(), environment = process.env, render = renderInterruptedLifecycle, profile = "full" } = {}) {
  if (!["full", "current63", "current65"].includes(profile)) throw new Error("Unknown lifecycle source profile");
  const nativeSchemaFile = join(root, "database/tempo_storage/ddl/schema_version.py");
  const schemaFile = existsSync(nativeSchemaFile) ? nativeSchemaFile
    : join(root, "database/tempo_legacy_database/ddl/schema_version.py");
  const nativeVersion = existsSync(schemaFile) && readFileSync(schemaFile, "utf8").match(/^POSTGRES_SCHEMA_VERSION\s*=\s*(\d+)\s*$/m);
  if (profile === "current65") {
    if (!nativeVersion || Number(nativeVersion[1]) !== 66 || ["TEMPO_LIFECYCLE_P0_59_ONLY", "TEMPO_PG_STALE_DELIVERY_ONLY", "TEMPO_LIFECYCLE_MANAGED_REHEARSAL", "TEMPO_LIFECYCLE_SHIPPING_REHEARSAL", "TEMPO_LIFECYCLE_STUDY_PREFLIGHT_ONLY", "W08_CAPTURE_ONLY", "W08_COPY_ONLY"].some(name => environment[name] === "1") || environment.TEMPO_LIFECYCLE_SCHEMA62_ROOT || environment.TEMPO_LIFECYCLE_SCHEMA62_IMAGES || environment.TEMPO_LIFECYCLE_SCHEMA61_IMAGES || environment.TEMPO_LIFECYCLE_SCHEMA59_IMAGES)
      throw new Error("Current65 lifecycle requires candidate schema 66 without historical or scenario overrides; no resources acquired.");
    readSchema65Baseline(environment);
    const candidate = spawnSync("git", ["-C", root, "rev-parse", "HEAD"], { encoding: "utf8", timeout: 15_000 });
    const secret = environment.TEMPO_PG_TEST_SECRETS && relative(root, environment.TEMPO_PG_TEST_SECRETS);
    if (secret && (!/^\.tempo-pg-test-secrets-[^/]+$/.test(secret) || !existsSync(environment.TEMPO_PG_TEST_SECRETS))) throw new Error("Current65 task-owned secret path is invalid.");
    const dirtyArgs = ["-C", root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"];
    if (secret) dirtyArgs.push("--", ".", `:(top,exclude,literal)${secret}`);
    const dirty = spawnSync("git", dirtyArgs, { encoding: "utf8", timeout: 15_000 });
    if (candidate.status !== 0 || !/^[0-9a-f]{40}$/.test(candidate.stdout.trim()) || dirty.status !== 0 || dirty.stdout.trim()) throw new Error("Current65 candidate source must be exact and clean before resource acquisition.");
    const syntax = spawnSync(process.execPath, ["--check", "--input-type=module"], { input: render(root), encoding: "utf8" });
    if (syntax.status !== 0) throw new Error(`Invalid generated interruption harness: ${syntax.stderr || syntax.error?.message}`);
    return;
  }
  if (nativeVersion && Number(nativeVersion[1]) >= 64)
    throw new Error(`Schema-${nativeVersion[1]} lifecycle upgrade is not qualified: the historical current63 profile cannot prove that transition. No resources were acquired.`);
  if (profile === "current63" && (!existsSync(schemaFile)
      || !/^POSTGRES_SCHEMA_VERSION\s*=\s*63\s*$/m.test(readFileSync(schemaFile, "utf8"))
      || environment.TEMPO_LIFECYCLE_P0_59_ONLY === "1" || environment.TEMPO_PG_STALE_DELIVERY_ONLY === "1"))
    throw new Error("Current lifecycle profile requires schema 63 and the schema-61 predecessor fixture without other scenario overrides");
  const syntax = spawnSync(process.execPath, ["--check", "--input-type=module"], { input: render(root), encoding: "utf8" });
  if (syntax.status !== 0) throw new Error(`Invalid generated interruption harness: ${syntax.stderr || syntax.error?.message}`);
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
  if (process.argv.slice(2).some(argument => !["--current63", "--current65"].includes(argument)) || process.argv.length > 3)
    throw new Error("Unknown lifecycle source option");
  checkLifecycleSource({ profile: process.argv[2] === "--current63" ? "current63" : process.argv[2] === "--current65" ? "current65" : "full" });
  console.log("PASS lifecycle source preflight");
}
