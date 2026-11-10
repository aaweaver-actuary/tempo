// Receipt for one release attempt. Runtime target fingerprints remain separate.
import { createHash, randomUUID } from "node:crypto";
import { linkSync, unlinkSync, readFileSync, lstatSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { atomicJson } from "./tempo-deployment.mjs";

const imagePattern = /^sha256:[0-9a-f]{64}$/;
const shaPattern = /^[0-9a-f]{40}$/;
const roles = ["schema", "api", "foreground-worker", "background-worker", "background-scheduler", "web", "maintenance"];
const dependencies = ["postgres", "redis"];
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return JSON.stringify(value);
  throw new Error("Candidate identity contains unsupported value");
}
const same = (a, b) => canonical(a) === canonical(b);
function validateIdentity(identity) {
  if (!identity || !shaPattern.test(identity.source?.revision ?? "") || !shaPattern.test(identity.source?.tree ?? "") || identity.source?.clean !== true)
    throw new Error("Candidate source must be clean and identify exact revision and tree");
  if (!identity.context || !identity.daemon_id || !/^linux\/(amd64|arm64)$/.test(identity.platform ?? ""))
    throw new Error("Candidate context, daemon and platform are required");
  if (!identity.recipes || Object.keys(identity.recipes).sort().join(",") !== [...roles].sort().join(","))
    throw new Error("Candidate receipt requires all resolved role recipes");
  for (const role of roles) {
    const recipe = identity.recipes[role];
    if (!recipe || typeof recipe !== "object" || Array.isArray(recipe) || !Object.keys(recipe).length)
      throw new Error(`Missing resolved recipe for ${role}`);
  }
  if (!identity.build_dependencies || typeof identity.build_dependencies !== "object" || Array.isArray(identity.build_dependencies)
      || !Object.keys(identity.build_dependencies).length || Object.values(identity.build_dependencies).some(value => !imagePattern.test(value)))
    throw new Error("Candidate resolved build dependency identities are required");
  if (!identity.runtime_dependencies || Object.keys(identity.runtime_dependencies).sort().join(",") !== dependencies.sort().join(",")
      || Object.values(identity.runtime_dependencies).some(value => !imagePattern.test(value)))
    throw new Error("Candidate PostgreSQL and Redis image identities are required");
  canonical(identity);
}
function validateImages(images) {
  if (!images || Object.keys(images).sort().join(",") !== [...roles].sort().join(",")
      || Object.values(images).some(value => !imagePattern.test(value)))
    throw new Error("Candidate receipt requires every immutable role image");
}
export function createCandidateReceipt({ owner, identity, images }) {
  if (typeof owner !== "string" || !owner.trim()) throw new Error("Release artifact owner is required");
  validateIdentity(identity); validateImages(images);
  const build_identity_sha256 = createHash("sha256").update(canonical(identity)).digest("hex");
  return { version: 1, state: "complete", owner, identity, build_identity_sha256, images };
}
export function writeCandidateReceipt(path, candidate) {
  const receipt = createCandidateReceipt(candidate);
  const temporary = `${path}.${randomUUID()}.candidate`;
  try {
    atomicJson(temporary, receipt);
    // Publish only if this release attempt still owns an unused destination.
    linkSync(temporary, path);
  } finally {
    try { unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  return receipt;
}
// inspect returns the current Docker inspect object; no startup may follow a mismatch.
export async function verifyCandidateReceipt({ receipt, owner, observed, inspect }) {
  validateIdentity(observed);
  if (!receipt || receipt.version !== 1 || receipt.state !== "complete" || receipt.owner !== owner || !owner)
    throw new Error("Candidate artifact receipt owner or state mismatch");
  validateIdentity(receipt.identity); validateImages(receipt.images);
  const expected = createCandidateReceipt({ owner, identity: receipt.identity, images: receipt.images });
  if (receipt.build_identity_sha256 !== expected.build_identity_sha256 || !same(receipt.identity, observed))
    throw new Error("Candidate source, recipe, dependencies, platform or daemon mismatch");
  if (typeof inspect !== "function") throw new Error("Candidate image inspection is required");
  const [os, architecture] = observed.platform.split("/");
  for (const role of roles) {
    const image = await inspect(receipt.images[role], role);
    if (!image || image.Id !== receipt.images[role] || image.Os !== os || image.Architecture !== architecture
        || image.Config?.Labels?.["org.opencontainers.image.revision"] !== observed.source.revision)
      throw new Error(`Candidate ${role} image is unavailable or mismatched`);
  }
  return { ...receipt.images };
}

// Accept only simple literal FROM instructions; unresolved/dynamic recipes are
// not qualified for reuse. The exact Dockerfile remains part of the source tree.
export function literalBaseImages(dockerfile) {
  const stages = new Set(), bases = [];
  for (const line of dockerfile.split(/\r?\n/)) {
    if (!/^\s*FROM\s/i.test(line)) continue;
    const match = line.match(/^\s*FROM\s+([^\s]+)(?:\s+AS\s+([\w.-]+))?\s*(?:#.*)?$/i);
    if (!match || match[1].includes("$") || match[1].startsWith("--")) throw new Error("Unsupported dynamic candidate FROM");
    const name = match[1];
    if (!stages.has(name.toLowerCase()) && name !== "scratch") bases.push(name);
    if (match[2]) stages.add(match[2].toLowerCase());
  }
  if (!bases.length || new Set(bases).size !== bases.length) throw new Error("Candidate base image set is empty or ambiguous");
  return bases.sort();
}

export function buildMaterials(metadata, { dockerfile, platform, expectedImageId }) {
  const provenance = metadata?.["buildx.build.provenance"], descriptor = metadata?.["containerimage.descriptor"];
  if (descriptor?.digest !== expectedImageId || provenance?.invocation?.environment?.platform !== platform)
    throw new Error("Candidate build metadata output or platform differs");
  const expected = literalBaseImages(dockerfile);
  const materials = provenance?.materials;
  if (!Array.isArray(materials)) throw new Error("Candidate build materials are unavailable");
  const result = {};
  for (const material of materials) {
    const match = /^pkg:docker\/([^?]+)\?platform=(.+)$/.exec(material?.uri ?? "");
    if (!match) throw new Error("Unexpected candidate build material");
    const split = match[1].lastIndexOf("@");
    if (split < 1) throw new Error("Malformed candidate build material");
    const name = `${match[1].slice(0, split)}:${match[1].slice(split + 1)}`;
    let actualPlatform;
    try { actualPlatform = decodeURIComponent(match[2]); } catch { throw new Error("Malformed candidate build material platform"); }
    const digest = `sha256:${material.digest?.sha256 ?? ""}`;
    if (!expected.includes(name) || Object.hasOwn(result, name) || actualPlatform !== platform || !imagePattern.test(digest))
      throw new Error("Candidate build materials do not match literal FROM inputs");
    result[name] = digest;
  }
  if (!same(Object.keys(result).sort(), expected)) throw new Error("Candidate build materials are incomplete");
  return result;
}

// Compose generates project-specific output names and project labels. All other
// resolved fields, including every argument and label, remain part of identity.
export function normalizeCandidateBake(specification, project) {
  if (typeof project !== "string" || !project || !specification?.target) throw new Error("Resolved candidate Bake targets required");
  const names = roles.filter(role => role !== "maintenance");
  if (!names.every(name => specification.target[name])) throw new Error("Resolved candidate Bake targets are incomplete");
  return Object.fromEntries(names.map(name => {
    const target = structuredClone(specification.target[name]);
    if (target.tags !== undefined && (!Array.isArray(target.tags)
      || target.tags.length !== 1 || ![`${project}-${name}`, `${project}-${name}:latest`].includes(target.tags[0])))
      throw new Error(`Unexpected candidate ${name} output tag`);
    delete target.tags;
    if (target.labels?.["com.docker.compose.project"] !== undefined) {
      if (target.labels["com.docker.compose.project"] !== project) throw new Error("Candidate Compose project label mismatch");
      delete target.labels["com.docker.compose.project"];
    }
    return [name, target];
  }));
}

// Explicit release-owner operation; it only removes the tags recorded by this
// attempt, after verifying all present tags and the original daemon first.
export async function cleanupCandidateTags({ receipt, owner, context, daemonId, inspectTag, removeTag }) {
  if (!receipt || receipt.version !== 1 || receipt.state !== "complete" || receipt.owner !== owner
      || !/^[-a-z0-9]{12,80}$/.test(owner ?? "") || receipt.identity?.context !== context || receipt.identity?.daemon_id !== daemonId)
    throw new Error("Candidate cleanup owner or Docker daemon differs");
  const expected = createCandidateReceipt({ owner, identity: receipt.identity, images: receipt.images });
  if (expected.build_identity_sha256 !== receipt.build_identity_sha256) throw new Error("Candidate cleanup receipt identity differs");
  const present = [];
  for (const role of roles) {
    const tag = `tempo-release-${owner}-${role}`;
    const observed = await inspectTag(tag);
    if (observed === null) continue; // A previous partial cleanup may have removed it.
    if (observed.Id !== receipt.images[role]) throw new Error(`Candidate cleanup tag mismatch for ${role}`);
    present.push(tag);
  }
  for (const tag of present) await removeTag(tag);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [, , action, path, owner] = process.argv;
  if (action !== "--cleanup" || !path || !owner || process.argv.length !== 5) throw new Error("Usage: node scripts/release-candidate-artifacts.mjs --cleanup <receipt> <owner>");
  if (["DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"].some(name => process.env[name]))
    throw new Error("Candidate cleanup requires a named context without host or TLS overrides");
  if (!lstatSync(path).isFile()) throw new Error("Candidate receipt must be a regular file");
  const receipt = JSON.parse(readFileSync(path, "utf8"));
  const context = receipt.identity?.context;
  if (typeof context !== "string" || !context || context.startsWith("-")) throw new Error("Candidate receipt context invalid");
  const docker = args => spawnSync("docker", ["--context", context, ...args], { encoding: "utf8", timeout: 15_000 });
  const daemon = docker(["info", "--format", "{{.ID}}"]);
  if (daemon.error || daemon.status !== 0 || !daemon.stdout.trim()) throw new Error("Candidate cleanup daemon unavailable");
  await cleanupCandidateTags({ receipt, owner, context, daemonId: daemon.stdout.trim(),
    inspectTag: async tag => {
      const result = docker(["image", "inspect", tag]);
      if (!result.error && result.status === 1 && /no such image/i.test(result.stderr ?? "")) return null;
      if (result.error || result.status !== 0) throw new Error(`Candidate cleanup cannot inspect ${tag}`);
      const images = JSON.parse(result.stdout);
      if (!Array.isArray(images) || images.length !== 1) throw new Error(`Candidate cleanup inspection incomplete: ${tag}`);
      return images[0];
    },
    removeTag: async tag => {
      const result = docker(["image", "rm", tag]);
      if (result.error || result.status !== 0) throw new Error(`Candidate cleanup cannot remove ${tag}`);
    },
  });
  unlinkSync(path);
  console.log("PASS release-owned candidate tags and receipt removed");
}
