import { spawnSync } from "node:child_process";
const result = spawnSync(process.execPath, ["--test", "--test-reporter=tap", ...(process.argv[2] ? ["--test-name-pattern=" + process.argv[2]] : []), "tests/chess-line.test.mjs"], { encoding: "utf8" });
process.stdout.write(result.stdout ?? "");
process.stderr.write(result.stderr ?? "");
const passed = [...(result.stdout ?? "").matchAll(/^ok \d+ - (.+)$/gm)].filter(match => !/\.test\.mjs(?: #.*)?$/.test(match[1]) && !match[1].includes("# SKIP")).length;
console.log(JSON.stringify({ executedIdentities: [...(result.stdout ?? "").matchAll(/^ok \d+ - (.+)$/gm)].map(match => match[1]), passed }));
if (result.status !== 0 || !passed) { console.error("Tests failed or zero selected tests"); process.exitCode = 1; }
