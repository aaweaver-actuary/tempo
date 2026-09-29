import { spawnSync } from "node:child_process";

const kind = process.argv[2];
const value = kind === "file" ? process.env.FILE : kind === "grep" ? process.env.VIEW : null;
if (!value?.trim()) throw new Error(`Set ${kind === "file" ? "FILE" : "VIEW"} to a non-empty browser selection`);
const flag = kind === "file" ? "--browser-file" : kind === "grep" ? "--browser-grep" : null;
if (!flag) throw new Error(`Unknown focused PostgreSQL browser mode: ${kind}`);
const result = spawnSync(process.execPath, ["scripts/test-postgres-docker.mjs", flag, value], {
  stdio: "inherit", env: process.env,
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
