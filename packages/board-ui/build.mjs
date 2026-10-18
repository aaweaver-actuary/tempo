import { spawnSync } from "node:child_process";
import { rm, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
const manifest = JSON.parse(await readFile(new URL("package.json", import.meta.url), "utf8"));
if (manifest.name !== "@tempo/board-ui") throw new Error("Unexpected package root");
await rm(new URL("dist/", import.meta.url), { recursive: true, force: true });
const result = spawnSync(process.execPath, [fileURLToPath(import.meta.resolve("typescript/bin/tsc")), "-p", "tsconfig.json"],
  { cwd: root, stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
