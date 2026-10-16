import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, resolve, sep, basename } from "node:path";
import { spawnSync } from "node:child_process";

const within = (parent, child) => child === parent || child.startsWith(parent + sep);

/** Workers own their executable packaging rules; the shared harness owns containment. */
export function workerQualifier(root, component) {
  const componentRoot = resolve(root, component.root);
  const qualifier = component.artifact?.qualifier;
  if (typeof qualifier !== "string" || !qualifier || isAbsolute(qualifier)) throw new Error("Missing/escaping worker qualifier: " + component.id);
  const path = resolve(componentRoot, qualifier);
  if (!within(componentRoot, path) || !existsSync(path) || !statSync(path).isFile()) throw new Error("Missing/escaping worker qualifier: " + component.id);
  const canonicalRoot = realpathSync(root);
  const canonicalComponent = resolve(canonicalRoot, component.root);
  if (!within(canonicalRoot, canonicalComponent) || realpathSync(componentRoot) !== canonicalComponent || realpathSync(path) !== resolve(canonicalComponent, qualifier)) throw new Error("Symlink/escaping worker qualifier: " + component.id);
  return { path, cwd: componentRoot };
}

function invoke(root, component, args) {
  const { path, cwd } = workerQualifier(root, component);
  const environment = { ...process.env };
  delete environment.NODE_PATH;
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [path, ...args], {
    cwd, env: environment, input: JSON.stringify(component), encoding: "utf8", maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error || result.status !== 0) throw new Error("Worker qualifier failed: " + component.id + ": " + (result.error?.message ?? result.stderr + result.stdout));
  return result.stdout;
}

export function checkWorker(root, component) {
  let report;
  try { report = JSON.parse(invoke(root, component, ["boundary"])); }
  catch (error) { throw new Error("Invalid worker boundary: " + component.id + ": " + error.message); }
  if (report.success !== true || !Number.isInteger(report.files) || report.files < 1 || !Array.isArray(report.exports) || !report.exports.length) throw new Error("Empty/failed worker boundary: " + component.id);
  return report;
}

export function buildWorker(root, component, reportPath, artifactDirectory) {
  invoke(root, component, ["restricted", "--report", reportPath, "--artifacts", artifactDirectory]);
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const cases = report.cases;
  if (report.complete !== true || !Array.isArray(report.artifacts) || !report.artifacts.length || cases?.success !== true || !Number.isInteger(cases.passed) || cases.passed < 1 || cases.failed !== 0 || !Array.isArray(cases.executedIdentities) || cases.executedIdentities.length !== cases.passed) throw new Error("Incomplete/zero-case worker artifact proof: " + component.id);
  for (const artifact of report.artifacts) {
    if (typeof artifact.name !== "string" || !artifact.name || artifact.name !== basename(artifact.name) || !/^[a-f0-9]{64}$/.test(artifact.sha256)) throw new Error("Invalid worker artifact identity");
    const path = resolve(artifactDirectory, artifact.name);
    if (!within(realpathSync(artifactDirectory), realpathSync(path)) || !statSync(path).isFile()) throw new Error("Escaping worker artifact");
    const actual = createHash("sha256").update(readFileSync(path)).digest("hex");
    if (actual !== artifact.sha256) throw new Error("Worker artifact hash mismatch: " + artifact.name);
  }
  return report;
}
