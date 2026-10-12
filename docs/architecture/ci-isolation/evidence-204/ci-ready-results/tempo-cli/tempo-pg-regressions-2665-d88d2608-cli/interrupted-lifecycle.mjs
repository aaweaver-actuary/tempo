
import { readFileSync } from "node:fs";
import { commandExecutor } from "/home/runner/work/tempo/tempo/scripts/tempo-deployment.mjs";
import { createRuntime, executeLifecycle } from "/home/runner/work/tempo/tempo/scripts/tempo-runtime.mjs";
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
