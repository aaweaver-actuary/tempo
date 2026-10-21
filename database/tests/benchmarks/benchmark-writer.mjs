import { spawnSync } from "node:child_process";
import { resolvePython } from "../../../scripts/resolve-python.mjs";

const result = spawnSync(resolvePython(), ["database/tests/benchmarks/writer_latency.py"], {
  stdio: "inherit",
  env: { ...process.env, PYTHONPATH: "backend:database" },
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
