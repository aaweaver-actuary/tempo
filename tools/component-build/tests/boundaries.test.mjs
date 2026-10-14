import assert from "node:assert/strict";
import { test } from "node:test";
import { cpSync, mkdtempSync, writeFileSync, readFileSync, rmSync, symlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { repository, checkTypeScript, checkConsumer, validateManifest } from "../check-components.mjs";
const manifest = JSON.parse(readFileSync(resolve(repository, "components.json"), "utf8"));
const chess = manifest.components.find(component => component.id === "chess-line");
const scheduling = manifest.components.find(component => component.id === "scheduling");
const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3";
function fixture(t, component) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-m02-boundary-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  cpSync(resolve(repository, component.root, "src"), join(directory, "src"), { recursive: true });
  if (component === scheduling) cpSync(resolve(repository, component.root, "pyproject.toml"), join(directory, "pyproject.toml"));
  if (component === chess) {
    cpSync(resolve(repository, component.root, "package.json"), join(directory, "package.json"));
    const dependencyRoot = existsSync(resolve(repository, "node_modules/chess.js")) ? "node_modules" : "packages/chess-line/node_modules";
    symlinkSync(resolve(repository, dependencyRoot), join(directory, "node_modules"));
  }
  return directory;
}
function checkPython(directory) {
  return spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), directory, JSON.stringify(scheduling.dependencies), JSON.stringify(scheduling.publicExports)], { encoding: "utf8" });
}
for (const [kind, invalidImport] of [
  ["direct", 'import "fastapi";'],
  ["lazy", 'async function hidden() { return import("services/content/internal"); }'],
  ["conditional", 'if (false) { import("undeclared-analysis"); }'],
  ["source escape", 'import "../../../app/main";'],
  ["unresolved dynamic", 'async function hidden(path: string) { return import(path); }'],
]) test(`M02 TypeScript rejects ${kind} forbidden imports`, t => {
  const directory = fixture(t, chess);
  const entry = join(directory, "src/index.ts");
  writeFileSync(entry, readFileSync(entry, "utf8") + "\n" + invalidImport);
  assert.throws(() => checkTypeScript(directory, chess), /dependency|escape|loader/i);
});
test("M02 TypeScript rejects forbidden transitive imports", t => {
  const directory = fixture(t, chess);
  writeFileSync(join(directory, "src/bridge.ts"), 'import "services/learning/private";');
  const entry = join(directory, "src/index.ts");
  writeFileSync(entry, readFileSync(entry, "utf8") + '\nimport "./bridge.js";');
  assert.throws(() => checkTypeScript(directory, chess), /Undeclared dependency/);
});
test("M02 TypeScript rejects private exports and undeclared dependency metadata", t => {
  const directory = fixture(t, chess);
  const metadataPath = join(directory, "package.json");
  const metadata = JSON.parse(readFileSync(metadataPath, "utf8"));
  metadata.exports["./private"] = "./dist/private.js";
  writeFileSync(metadataPath, JSON.stringify(metadata));
  assert.throws(() => checkTypeScript(directory, chess), /Private exports/);
  delete metadata.exports["./private"];
  metadata.dependencies.react = "19.2.6";
  writeFileSync(metadataPath, JSON.stringify(metadata));
  assert.throws(() => checkTypeScript(directory, chess), /Dependencies differ/);
  assert.throws(() => checkConsumer('import { hidden } from "@tempo/chess-line";', "@tempo/chess-line", chess.publicExports), /Unavailable export/);
  assert.throws(() => checkConsumer('async function hidden() { return import("@tempo/chess-line/private"); }', "@tempo/chess-line", chess.publicExports), /Private/);
});
for (const [kind, invalidImport] of [
  ["direct", "import fastapi"],
  ["lazy", "def hidden():\n    import app.main"],
  ["conditional", "if False:\n    import services.content"],
  ["source escape", "from ...app import main"],
  ["unresolved dynamic", "def hidden():\n    __import__('app.main')"],
  ["dynamic importlib", "import importlib\ndef hidden():\n    importlib.import_module('app.main')"],
  ["aliased dynamic loader", "from importlib import import_module as loader\ndef hidden():\n    loader('app.main')"],
]) test(`M02 Python rejects ${kind} forbidden imports`, t => {
  const directory = fixture(t, scheduling);
  const implementation = join(directory, "src/tempo_scheduling/_scheduler.py");
  writeFileSync(implementation, readFileSync(implementation, "utf8") + "\n" + invalidImport + "\n");
  const result = checkPython(directory);
  assert.notEqual(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stderr, /undeclared|escape|dynamic loader/);
});
test("M02 Python rejects forbidden transitive imports", t => {
  const directory = fixture(t, scheduling);
  writeFileSync(join(directory, "src/tempo_scheduling/bridge.py"), "import services.content\n");
  assert.notEqual(checkPython(directory).status, 0);
});
test("M02 Python public API policy rejects private and unavailable consumer exports", t => {
  const directory = fixture(t, scheduling);
  for (const source of ["from tempo_scheduling._scheduler import Schedule", "from tempo_scheduling import hidden", "def hidden():\n    import tempo_scheduling._scheduler"]) {
    const consumer = join(directory, "consumer.py");
    writeFileSync(consumer, source);
    const result = spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), "consumer", consumer, "tempo_scheduling", JSON.stringify(scheduling.publicExports)], { encoding: "utf8" });
    assert.notEqual(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stderr, /Private/);
  }
});
test("M02 manifest rejects duplicate missing escaping and false implemented components", () => {
  for (const change of [copy => copy.components.push(copy.components[0]), copy => delete copy.components[0].maintainer,
    copy => copy.components[0].root = "../outside", copy => copy.components[0].status = "implemented",
    copy => copy.components[0].tests = [{ command: "pretend" }]]) {
    const copy = structuredClone(manifest); change(copy);
    assert.throws(() => validateManifest(copy));
  }
});
test("M02 zero-selected Node tests fail", t => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-m02-empty-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const runner = resolve(repository, "tools/component-build/test-node.mjs");
  assert.notEqual(spawnSync(process.execPath, [runner, directory]).status, 0);
  writeFileSync(join(directory, "one.test.mjs"), 'import { test } from "node:test"; test("one", () => {});');
  const result = spawnSync(process.execPath, [runner, directory, "never-matches"], { encoding: "utf8" });
  assert.notEqual(result.status, 0, result.stdout);
  assert.match(result.stderr, /Zero selected tests/);
});

test("M02 separate test discovery preserves every legacy unit and runner file", async () => {
  const { validateInventory, selectFiles } = await import("../test-lane.mjs");
  const counts = validateInventory();
  assert.ok(counts.unitFiles > 0 && counts.runnerFiles > 0);
  assert.ok(selectFiles("producer-parity").includes("tests/unit/api-schema-parity-regressions.test.ts"));
  assert.ok(selectFiles("runners").includes("tests/runner/tempo-cli.test.mjs"));
  assert.throws(() => selectFiles("frontend", "does-not-exist"), /Zero selected tests/);
});

test("M02 Python rejects undeclared dependency metadata and accidental public exports", t => {
  const directory = fixture(t, scheduling);
  const metadataPath = join(directory, "pyproject.toml");
  const original = readFileSync(metadataPath, "utf8");
  writeFileSync(metadataPath, original.replace('"fsrs==6.3.2"', '"fsrs==6.3.2", "psycopg==3.3.6"'));
  assert.match(checkPython(directory).stderr, /Dependencies differ/);
  writeFileSync(metadataPath, original);
  const entry = join(directory, "src/tempo_scheduling/__init__.py");
  writeFileSync(entry, readFileSync(entry, "utf8").replace('"unlock_ready"]', '"unlock_ready", "_scheduler"]'));
  assert.match(checkPython(directory).stderr, /public exports differ/);
});
