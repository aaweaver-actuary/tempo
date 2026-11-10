// Locked historical images are borrowed; this adapter never deletes or builds them.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { atomicJson } from "./tempo-deployment.mjs";

export const lifecycleFixtureRevision = "4aa0ad1b731402365c1b866a6ba7bed484d2b6ac";
const roles = ["api", "web", "migration"];
const imageId = /^sha256:[0-9a-f]{64}$/;
const registryReference = /^ghcr\.io\/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:\/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}$/;

export function validateLifecycleImageLock(lock) {
  if (!lock || lock.version !== 1 || !["local-seed", "registry"].includes(lock.kind)
      || lock.revision !== lifecycleFixtureRevision || lock.platform !== "linux/arm64")
    throw new Error("Invalid historical image lock version, kind, revision, or platform");
  const provenance = lock.provenance;
  if (!provenance || !["built", "promoted"].includes(provenance.kind)
      || provenance.source_revision !== lifecycleFixtureRevision
      || typeof provenance.receipt !== "string" || !/^https:\/\/\S+$/.test(provenance.receipt))
    throw new Error("Historical image lock requires source-qualified provenance receipt");
  if (!lock.images || Object.keys(lock.images).sort().join(",") !== [...roles].sort().join(","))
    throw new Error("Historical image lock requires exactly api, web and migration roles");
  for (const role of roles) {
    const entry = lock.images[role];
    if (!entry || !imageId.test(entry.expected_local_image_id ?? "")) throw new Error(`Invalid ${role} local image ID`);
    if (Object.hasOwn(entry, "expected_config_digest") || (lock.kind === "registry"
      ? !registryReference.test(entry.reference ?? "") : Object.hasOwn(entry, "reference")))
      throw new Error(`Invalid ${role} historical image reference`);
  }
  return lock;
}

function invokeDocker(args) {
  const result = spawnSync("docker", args, { encoding: "utf8", timeout: args[2] === "pull" ? 180_000 : 15_000 });
  if (result.error) throw result.error;
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

// run is injectable for the focused contract proof. All calls pin the same context.
export async function prepareLifecycleImages({ lock, context, outputPath, run = invokeDocker }) {
  validateLifecycleImageLock(lock);
  if (typeof context !== "string" || !context.trim() || context.startsWith("-")) throw new Error("Explicit Docker context required");
  if (typeof outputPath !== "string" || !outputPath) throw new Error("Run-owned manifest path required");
  const prefix = ["--context", context];
  const info = await run([...prefix, "info", "--format", "{{.ID}}"]);
  if (info.status !== 0 || !info.stdout?.trim()) throw new Error("Cannot identify selected Docker daemon");
  const evidence = { context, daemon_id: info.stdout.trim(), lock_sha256: createHash("sha256").update(JSON.stringify(lock)).digest("hex"), roles: {} };
  const manifest = { revision: lock.revision, images: {} };
  for (const role of roles) {
    const expected = lock.images[role];
    const started = performance.now();
    let result = await run([...prefix, "image", "inspect", expected.expected_local_image_id]);
    let acquisition = "hit";
    if (result.status !== 0) {
      // Only explicit Docker absence permits a registry pull. Daemon failures do not.
      if (!/no such image/i.test(result.stderr ?? "")) throw new Error(`Cannot inspect historical ${role} image`);
      if (lock.kind === "local-seed") throw new Error(`Missing local historical ${role} image`);
      const pull = await run([...prefix, "pull", "--platform", lock.platform, expected.reference]);
      if (pull.status !== 0) throw new Error(`Historical ${role} image pull failed`);
      acquisition = "pull";
      result = await run([...prefix, "image", "inspect", expected.reference]);
      if (result.status !== 0) throw new Error(`Cannot inspect pulled historical ${role} image`);
    }
    let image;
    try { const parsed = JSON.parse(result.stdout); if (!Array.isArray(parsed) || parsed.length !== 1) throw new Error(); [image] = parsed; }
    catch { throw new Error(`Invalid historical ${role} image inspection`); }
    if (image.Id !== expected.expected_local_image_id || image.Os !== "linux" || image.Architecture !== "arm64"
        || image.Config?.Labels?.["org.opencontainers.image.revision"] !== lock.revision)
      throw new Error(`Historical ${role} image identity or platform mismatch`);
    manifest.images[role] = image.Id;
    evidence.roles[role] = { acquisition, expected_id: expected.expected_local_image_id, observed_id: image.Id,
      duration_seconds: (performance.now() - started) / 1000 };
  }
  atomicJson(outputPath, manifest);
  return evidence;
}
