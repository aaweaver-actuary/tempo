// Source-only checks: no Docker, temporary resources, or lifecycle mutation.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export function renderInterruptedLifecycle(root) {
  return `
import { readFileSync } from "node:fs";
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

export function checkLifecycleSource({ root = process.cwd(), environment = process.env, render = renderInterruptedLifecycle } = {}) {
  const syntax = spawnSync(process.execPath, ["--check", "--input-type=module"], { input: render(root), encoding: "utf8" });
  if (syntax.status !== 0) throw new Error(`Invalid generated interruption harness: ${syntax.stderr || syntax.error?.message}`);
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
