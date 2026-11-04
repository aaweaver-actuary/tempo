import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { strict as assert } from "node:assert";

const worker = fileURLToPath(new URL("../../scripts/maia-coverage-worker.mjs", import.meta.url));
const denial = fileURLToPath(new URL("../fixtures/maia-parent-import-denial.mjs", import.meta.url));

for (const [name, status] of [
  ["Study-only", { available: true, active: false, study_only_mode: true }],
  ["foreground active", { available: true, active: true, study_only_mode: false }],
  ["foreground unavailable", { available: false, active: false, study_only_mode: false }],
]) {
  test(`Maia parent reaches control without smoke inference when ${name}`, async () => {
    const result = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, ["--import", denial, worker], {
        env: { ...process.env, TEMPO_MAIA_SMOKE: "0", TEMPO_MAIA_TEST_STATUS: JSON.stringify(status), TEMPO_API_URL: "http://127.0.0.1:1" },
        stdio: ["ignore", "pipe", "pipe"],
      });
      let stderr = "";
      child.stderr.setEncoding("utf8").on("data", chunk => { stderr += chunk; });
      const timeout = setTimeout(() => { child.kill("SIGKILL"); reject(new Error(`Worker never completed controlled read: ${stderr}`)); }, 3000);
      child.once("error", error => { clearTimeout(timeout); reject(error); });
      child.once("exit", (code, signal) => { clearTimeout(timeout); resolve({ code, signal, stderr }); });
    });
    assert.equal(result.code, 0, result.stderr || result.signal);
  });
}
