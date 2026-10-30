// Thin composition over existing Study-aware budget and admitted Docker run.
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { readDevelopmentCpuAllocation, runWithDevelopmentBudget } from "./development-budget.mjs";
import { managedDockerEnvironment } from "./development-docker.mjs";

const args = process.argv.slice(2);
if (args[0] !== "--managed-child") {
  process.exitCode = await runWithDevelopmentBudget(process.execPath,
    [fileURLToPath(import.meta.url), "--managed-child", ...args]);
} else {
  const [, kind, python, ...options] = args;
  const scripts = { maia: "./qualify-maia-container.py", frozen: "../database/tests/test-frozen-postgres.py" };
  if (!scripts[kind] || !python) throw new Error("Supply maia|frozen, an existing Python executable, and owning arguments");
  const directory = await mkdtemp(join(tmpdir(), "tempo-qualification-docker-"));
  try {
    const env = managedDockerEnvironment(process.env, directory);
    const requested = env.TEMPO_QUALIFICATION_CPUS;
    if (requested !== undefined) env.TEMPO_QUALIFICATION_CPUS = String(await readDevelopmentCpuAllocation({cpus: Number(requested)}));
    console.log(JSON.stringify({ stage: "qualification-window-allocation", cpus: requested === undefined ? null : Number(env.TEMPO_QUALIFICATION_CPUS),
      source: requested === undefined ? "unrestricted" : "explicit-caller-allocation" }));
    const child = spawn(python, [fileURLToPath(new URL(scripts[kind], import.meta.url)), ...options], { env, stdio: "inherit" });
    const interrupt = signal => child.kill(signal);
    const term = () => interrupt("SIGTERM"), int = () => interrupt("SIGINT");
    process.once("SIGTERM", term); process.once("SIGINT", int);
    try {
      process.exitCode = await new Promise((resolve, reject) => {
        child.once("error", reject);
        child.once("exit", (code, signal) => signal ? reject(new Error("Qualification interrupted by " + signal)) : resolve(code));
      });
    } finally { process.removeListener("SIGTERM", term); process.removeListener("SIGINT", int); }
  } finally { await rm(directory, { recursive: true }); }
}
