import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { createModel } from "./model.mjs";
import { createProbabilityProvider } from "./probability-provider.mjs";

// Private child mode has one initialize and one inference; the parent owns deadlines/exit.
async function modelChild() {
  let model;
  let running = false;
  process.on("disconnect", () => process.exit(0));
  process.on("message", async (message) => {
    if (running) { process.send({ error: "worker_busy" }); return; }
    running = true;
    try {
      if (message.operation === "initialize" && !model) {
        model = await createModel(message.modelOptions);
        process.send({ value: model.identity });
      } else if (message.operation === "infer" && model) {
        process.send({ value: await model.infer(message.job) });
      } else process.send({ error: "invalid_request" });
    } catch (error) { process.send({ error: error.code ?? "worker_crashed" }); }
    finally { running = false; }
  });
}

/** One JSON request through EOF, one result, clean child exit. No persistent broker. */
export async function probabilityStdio({ input = process.stdin, output = process.stdout, provider = createProbabilityProvider(), signal } = {}) {
  let bytes = 0;
  const chunks = [];
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(() => input.destroy(new Error("input_timeout")), 5000);
  try {
    for await (const chunk of input) {
      bytes += Buffer.byteLength(chunk);
      if (bytes > 65536) throw new Error("input_too_large");
      chunks.push(Buffer.from(chunk));
    }
    clearTimeout(timer);
    const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const result = await provider.evidence(request, { signal: controller.signal });
    output.write(`${JSON.stringify(result)}\n`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    await provider.close();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv[2] === "--model-child") await modelChild();
  else {
    const controller = new AbortController();
    const abort = () => controller.abort();
    process.once("SIGTERM", abort);
    process.once("SIGINT", abort);
    try { await probabilityStdio({ signal: controller.signal }); }
    catch (error) { process.stderr.write(`${JSON.stringify({ error: error.code ?? error.message })}\n`); process.exitCode = 1; }
    finally { process.removeListener("SIGTERM", abort); process.removeListener("SIGINT", abort); }
  }
}
