import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { posix } from "node:path";

const dockerfile = readFileSync("Dockerfile.engine", "utf8");
const copied = dockerfile.split("\n").filter(line => line.startsWith("COPY "))
  .flatMap(line => line.split(/\s+/).slice(1, -1));
const manifest = JSON.parse(readFileSync("components.json", "utf8"));
const suite = manifest.suites.find(item => item.id === "engine-image-packaging");

function gaps(imageInputs, proofInputs) {
  const missing = [];
  const visited = new Set();
  const pending = ["scripts/defense-engine-worker.mjs"];
  for (const file of ["Dockerfile.engine", ".dockerignore", "components.json", "tests/runner/engine-image-packaging.test.mjs", ...imageInputs]) {
    if (!existsSync(file) || !proofInputs.includes(file)) missing.push(file);
  }
  while (pending.length) {
    const file = pending.pop();
    if (visited.has(file)) continue;
    visited.add(file);
    if (!existsSync(file) || !imageInputs.includes(file) || !proofInputs.includes(file)) {
      missing.push(file);
      if (!existsSync(file)) continue;
    }
    const source = readFileSync(file, "utf8");
    const imports = source.matchAll(/(?:^import\s+(?:[^\n]*?\s+from\s+)?|^export\s+[^\n]*?\s+from\s+|new URL\()(["'])(\.[^"']+)\1/gm);
    for (const match of imports) {
      const dependency = posix.normalize(posix.join(posix.dirname(file), match[2]));
      if (dependency.endsWith(".mjs")) pending.push(dependency);
      else if (!existsSync(dependency) || !imageInputs.includes(dependency) || !proofInputs.includes(dependency)) missing.push(dependency);
    }
  }
  return missing;
}

test("engine image copies and declares its worker module closure", () => {
  assert.ok(suite, "engine image proof suite is registered");
  const withoutFamily = values => values.filter(file => file !== "scripts/engine-job-families.mjs");
  assert.ok(gaps(withoutFamily(copied), suite.inputs).includes("scripts/engine-job-families.mjs"));
  assert.ok(gaps(copied, withoutFamily(suite.inputs)).includes("scripts/engine-job-families.mjs"));
  assert.deepEqual(gaps(copied, suite.inputs), []);
});
