import { registerHooks } from "node:module";

const workerUrl = new URL("../../scripts/maia-coverage-worker.mjs", import.meta.url).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL === workerUrl && specifier === "./maia-inference.mjs")
      throw new Error("Parent imported smoke-only Maia inference");
    return nextResolve(specifier, context);
  },
});

const status = JSON.parse(process.env.TEMPO_MAIA_TEST_STATUS);
let reads = 0;
globalThis.fetch = async (url) => {
  if (new URL(url).pathname !== "/api/system/foreground-active") {
    console.error(`Unexpected worker request: ${url}`);
    process.exit(2);
  }
  reads += 1;
  if (reads === 1) setTimeout(() => process.exit(0), 120);
  return Response.json(status);
};
process.on("exit", (code) => {
  if (code === 0 && reads !== 1) process.exitCode = 3;
});
