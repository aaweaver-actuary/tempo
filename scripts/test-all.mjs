import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
function protectRegressionSuite(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory() && entry.name !== "__pycache__") protectRegressionSuite(path);
    else if (/\.(tsx?|py)$/.test(path) && /\b(?:test|it|describe)\.(?:skip|todo|only)\s*\(|pytest\.mark\.skip|@(?:unittest\.)?skip/.test(readFileSync(path,"utf8"))) {
      throw new Error(`Regression suites cannot contain skipped, todo, or exclusive tests: ${path}`);
    }
  }
}
protectRegressionSuite("tests");
protectRegressionSuite("backend/tests");
const python = existsSync(".venv/bin/python") ? ".venv/bin/python" : "python3";
const commands = [
  ["npm", ["run", "test:unit"]],
  [python, ["-m", "pytest", "backend/tests", "-q", "-o", "cache_dir=.pytest_cache", "--rootdir=."]],
  ["cargo", ["fmt", "--all", "--", "--check"]],
  ["cargo", ["clippy", "--all-targets", "--", "-D", "warnings"]],
  ["cargo", ["test", "--workspace"]],
  ["npm", ["run", "lint"]],
  ["npm", ["run", "typecheck"]],
  ["npm", ["run", "build:wasm"]],
  ["npm", ["run", "build:local"]],
  ["npm", ["run", "test:browser"]],
  ["npm", ["run", "test:docker"]],
  ["npm", ["run", "test:visual"]],
];
for (const [command, args] of commands) {
  const result = spawnSync(command, args, { stdio: "inherit", env: { ...process.env, PYTHONPATH: "backend" } });
  if (result.error) console.error(`Required check could not start: ${result.error.message}`);
  if (result.status !== 0 || result.error) process.exit(result.status ?? 1);
}
