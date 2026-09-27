import { spawnSync } from "node:child_process";
import { resolvePython } from "./resolve-python.mjs";

const result = spawnSync(resolvePython(), ["backend/benchmarks/pgn_parse.py"], {
  stdio: "inherit",
  env: { ...process.env, PYTHONPATH: "backend" },
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
