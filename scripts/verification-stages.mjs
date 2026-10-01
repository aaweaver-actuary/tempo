import { join } from "node:path";

// Shared local/CI inventory: command definitions are identical across owners.
export function verificationStages({ python, tier, outputDirectory }) {
  const unitProfilePath = join(outputDirectory, `unit-files-${tier}.json`);
const stages = [
  ["capabilities", "node", ["scripts/check-test-capabilities.mjs", "--docker", "--loopback", "--workspace-mount"]],
  ["unit", "npm", ["run", "test:unit", "--", "--reporter=default", "--reporter=json", `--outputFile.json=${unitProfilePath}`]],
  ["defense_engine", "node", ["scripts/test-defense-engine.mjs"]],
  ["backend", python, ["-m", "pytest", "backend/tests", "-q", "-o", "cache_dir=.pytest_cache", "--rootdir=."]],
  ["rust_format", "cargo", ["fmt", "--all", "--", "--check"]],
  ["rust_lint", "cargo", ["clippy", "--all-targets", "--", "-D", "warnings"]],
  ["rust_test", "cargo", ["test", "--workspace"]],
  ["lint", "npm", ["run", "lint"]],
  ["typecheck", "npm", ["run", "typecheck"]],
  ["wasm_build", "npm", ["run", "build:wasm"]],
  ["local_build", "npm", ["run", "build:local"]],
  // The PostgreSQL runner owns the single regular Playwright matrix.
  ["postgres_docker", "node", ["scripts/test-postgres-docker.mjs"]],
  ["visual", "npm", ["run", "test:visual"]],
];
const stagesByTier = {
  fast: ["unit"],
  python: ["backend"],
  backend: ["defense_engine", "backend"],
  rust: ["rust_format", "rust_lint", "rust_test"],
  integration: ["defense_engine", "backend", "rust_format", "rust_lint", "rust_test"],
  ui: ["capabilities", "postgres_docker", "visual"],
  browser: ["capabilities", "postgres_docker"],
  "ci-frontend": ["unit", "lint", "typecheck"],
  "ci-backend": ["defense_engine", "backend"],
  "ci-build": ["rust_format", "rust_lint", "rust_test", "wasm_build", "local_build"],
  full: stages.map(([name]) => name),
};
  return { stages, stagesByTier };
}
