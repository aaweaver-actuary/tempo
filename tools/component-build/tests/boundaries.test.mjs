import assert from "node:assert/strict";
import { test } from "node:test";
import { cpSync, mkdirSync, mkdtempSync, writeFileSync, readFileSync, realpathSync, rmSync, symlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { repository, checkTypeScript, checkConsumer, validateManifest, validateSuite } from "../check-components.mjs";
const manifest = JSON.parse(readFileSync(resolve(repository, "components.json"), "utf8"));
const chess = manifest.components.find(component => component.id === "chess-line");
const scheduling = manifest.components.find(component => component.id === "scheduling");
const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3";
// Node marks test children internally; independent CLI fixtures must not inherit it.
function spawnIsolatedNode(args, options = {}) {
  const env = { ...process.env, ...options.env };
  delete env.NODE_TEST_CONTEXT;
  return spawnSync(process.execPath, args, { ...options, env, timeout: 10_000 });
}
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
function workbenchStorageFixture(t) {
  const root = mkdtempSync(join(tmpdir(), "tempo-m02-workbench-storage-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const directory = join(root, "apps/workbench-web"), owner = join(root, "database/browser/adapters/workbench");
  mkdirSync(join(directory, "src"), { recursive: true });
  mkdirSync(owner, { recursive: true });
  writeFileSync(join(directory, "package.json"), JSON.stringify({ name: "@tempo/workbench-web", version: "0.1.0", type: "module", exports: { ".": "./lib/index.js" }, dependencies: {} }));
  writeFileSync(join(directory, "src/index.ts"), 'export { read } from "../../../database/browser/adapters/workbench/storage.mjs";');
  writeFileSync(join(owner, "storage.mts"), 'import { value } from "./compatibility.cjs"; export function read() { return value; }');
  writeFileSync(join(owner, "compatibility.cts"), 'export const value = "fixture";');
  const component = { id: "workbench-web", root: "apps/workbench-web", artifact: { name: "@tempo/workbench-web", version: "0.1.0" }, dependencies: [], publicExports: ["read"], buildInputs: ["src", "../../database/browser/adapters/workbench"] };
  return { root, directory, owner, component, check: () => checkTypeScript(directory, component, root) };
}
test("M02 TypeScript checks the declared canonical Workbench owner with mjs/mts and cjs/cts mappings", t => {
  const fixture = workbenchStorageFixture(t);
  assert.deepEqual(fixture.check(), { files: 3, exports: ["read"] });
});
test("M02 TypeScript rejects a neighboring database owner even when listed as a build input", t => {
  const fixture = workbenchStorageFixture(t), neighbor = join(fixture.root, "database/browser/adapters/neighbor");
  mkdirSync(neighbor);
  writeFileSync(join(neighbor, "private.mts"), "export const hidden = true;");
  fixture.component.buildInputs.push("../../database/browser/adapters/neighbor");
  writeFileSync(join(fixture.directory, "src/index.ts"), 'import "../../../database/browser/adapters/neighbor/private.mjs"; export const read = true;');
  assert.throws(fixture.check, /Source escape/);
});
test("M02 TypeScript rejects an unlisted file inside the canonical Workbench owner", t => {
  const fixture = workbenchStorageFixture(t);
  fixture.component.buildInputs = ["src", "../../database/browser/adapters/workbench/storage.mts"];
  assert.throws(fixture.check, /Source escape/);
});
test("M02 TypeScript rejects a canonical Workbench owner absent from build inputs", t => {
  const fixture = workbenchStorageFixture(t);
  fixture.component.buildInputs = ["src"];
  assert.throws(fixture.check, /Source escape/);
});
test("M02 TypeScript checks every declared Workbench owner source for transitive private imports", t => {
  const fixture = workbenchStorageFixture(t);
  writeFileSync(join(fixture.owner, "unimported.mts"), 'import "services/learning/private";');
  assert.throws(fixture.check, /Undeclared dependency/);
});
test("M02 TypeScript rejects symlinked Workbench owner sources and input roots", t => {
  const fixture = workbenchStorageFixture(t), outside = join(fixture.root, "outside");
  mkdirSync(outside);
  writeFileSync(join(outside, "hidden.mts"), "export const hidden = true;");
  symlinkSync(join(outside, "hidden.mts"), join(fixture.owner, "hidden.mts"));
  assert.throws(fixture.check, /Source symlink/);
  rmSync(join(fixture.owner, "hidden.mts"));
  rmSync(fixture.owner, { recursive: true });
  symlinkSync(outside, fixture.owner);
  assert.throws(fixture.check, /Source symlink\/escape/);
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
for (const { name, kind, command, originalInputs } of [
  {
    name: "pure", kind: "python", command: "python services/content/run_suite.py pure",
    originalInputs: ["tests", "fixtures", "src", "run_suite.py", "pyproject.toml", "uv.lock", "../../database/tempo_content_database/tests/unit"],
  },
  {
    name: "PostgreSQL", kind: "postgres", command: "python database/tempo_content_database/tooling/run_postgres.py",
    originalInputs: ["../../database/tempo_content_database/tooling/run_postgres.py", "../../database/tempo_content_database/tests/test_postgres.py", "../../database/tempo_content_database/tooling/benchmark.py", "run_suite.py", "src", "fixtures", "pyproject.toml", "uv.lock"],
  },
]) test(`M02 Content ${name} suite preserves relocated database inputs at repository root`, () => {
  const content = manifest.components.find(component => component.id === "content");
  assert.equal(content.status, "implemented");
  assert.equal(content.root, "services/content");
  assert.equal(content.tests.length, 2);
  const suite = content.tests.find(candidate => candidate.kind === kind);
  assert.ok(suite, `Content retains its ${kind} suite`);
  assert.doesNotThrow(() => validateSuite(suite, repository, content.root));
  assert.equal(suite.owner, "content");
  assert.equal(suite.cwd, ".");
  assert.equal(suite.command, command);
  const originalPaths = originalInputs.map(input => resolve(repository, content.root, input));
  assert.deepEqual(suite.inputs, originalPaths.map(path => relative(repository, path)));
  assert.deepEqual(suite.inputs.map(input => realpathSync(resolve(repository, suite.cwd, input))), originalPaths.map(path => realpathSync(path)));
  assert.doesNotThrow(() => validateManifest(manifest, repository, { componentIds: ["content"] }));
});
for (const { id, packageName, publicExports, interfaceExports } of [
  {
    id: "game-evidence", packageName: "tempo_game_evidence",
    // The manifest inventories the legacy root, not the narrower pure interface.
    publicExports: ["normalize_pgn", "replay_positions", "ComparisonSnapshot", "compare_game", "EvidenceStore",
      "LegacyAuthorityToken", "LegacyComparisonSnapshot", "LegacyScopedComparisonSnapshot", "PhysicalCard",
      "preview_legacy_observation"],
    interfaceExports: ["ComparisonSnapshot", "ComparisonSnapshotV2", "LegacyComparisonSnapshot",
      "LegacyScopedComparisonSnapshot", "PhysicalCard", "parse_snapshot", "preview_legacy_observation"],
  },
  {
    id: "learning-remediation", packageName: "tempo_learning_remediation",
    publicExports: ["AcceptedDecision", "AdmissionPort", "AdmissionProposal", "AdmissionResult", "AttemptAuthority",
      "Candidate", "CandidateFactsPort", "ContentAuthority", "CurrentMissProjection", "Eligibility", "ReceiveContext",
      "RemediationError", "ReviewOutcome", "WorkItem", "WorkPage", "Preparation", "PostgresRepository",
      "PAGE_SIZE", "MAX_PREPARATION_PAGES", "evaluate_eligibility", "order_candidates", "propose_admission",
      "validate_delivery", "prepare_admissions", "apply_admission", "current_miss_sql",
      "LegacyAcceptedDecision", "LegacyAuthority", "accepted_storage_identity"],
  },
  {
    id: "game-classification", packageName: "tempo_game_classification",
    publicExports: ["UnimplementedClassification", "FindingKind", "classify_response", "resolve_equivalent_mapping"],
  },
]) test(`M02 ${id} registration matches its existing public package exports`, () => {
  const component = manifest.components.find(candidate => candidate.id === id);
  assert.equal(component.status, "implemented");
  assert.deepEqual(component.publicExports, publicExports);
  const source = resolve(repository, component.root, "src", packageName, "__init__.py");
  const sources = [source];
  if (interfaceExports) sources.push(resolve(repository, component.root, "src", packageName, "interfaces", "__init__.py"));
  const result = spawnSync(python, ["-I", "-c", `
import ast, json, pathlib, sys
results = []
for source in sys.argv[1:]:
    tree = ast.parse(pathlib.Path(source).read_text())
    exports = [ast.literal_eval(node.value) for node in tree.body
               if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)]
    assert len(exports) == 1, "Expected one explicit public export declaration"
    results.append(exports[0])
print(json.dumps(results))
`, ...sources], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), interfaceExports ? [publicExports, interfaceExports] : [publicExports]);
});
test("M02 manifest rejects duplicate missing escaping and false implemented components", () => {
  const plannedComponent = manifest.components.find(component => component.status === "planned");
  assert.ok(plannedComponent, "The false-implemented fixture requires a planned component");
  for (const change of [copy => copy.components.push(copy.components[0]), copy => delete copy.components[0].maintainer,
    copy => copy.components[0].root = "../outside", copy => copy.components.find(component => component.id === plannedComponent.id).status = "implemented",
    copy => copy.components[0].tests = [{ command: "pretend" }]]) {
    const copy = structuredClone(manifest); change(copy);
    assert.throws(() => validateManifest(copy));
  }
});
test("M02 zero-selected Node tests fail", t => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-m02-empty-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const runner = resolve(repository, "tools/component-build/test-node.mjs");
  assert.notEqual(spawnIsolatedNode([runner, directory]).status, 0);
  writeFileSync(join(directory, "one.test.mjs"), 'import { test } from "node:test"; test("one", () => {});');
  const result = spawnIsolatedNode([runner, directory, "never-matches"], { encoding: "utf8" });
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

// Each fixture runs both entry points without importing package product source.
const nodeAccountingScenarios = [
  ["ordinary passing case", 'test("one", () => {});', undefined, 0, 1, 0, 0, 0],
  ["TODO-only file", 'test.todo("later");', undefined, 1, 0, 0, 0, 1],
  ["skip-only file", 'test.skip("later");', undefined, 1, 0, 0, 1, 0],
  ["empty describe suite", 'describe("empty", () => {});', undefined, 1, 0, 0, 0, 0],
  ["unmatched name filter", 'test("one", () => {});', "never-matches", 1, 0, 0, 0, 0],
  ["two nested passing cases", 'describe("suite", () => { test("first", () => {}); test("second", () => {}); });', undefined, 0, 2, 0, 0, 0],
  ["failed case", 'test("broken", () => { throw new Error("visible failure"); });', undefined, 1, 0, 1, 0, 0],
  ["mixed pass TODO and skip", 'test("one", () => {}); test.todo("later"); test.skip("ignored");', undefined, 0, 1, 0, 1, 1],
  ["passing case cannot hide failure", 'test("one", () => {}); test("broken", () => { throw new Error("visible failure"); });', undefined, 1, 1, 1, 0, 0],
  ["skip and TODO reasons", 'test("one", () => {}); test("skip", {skip:"reason"}, () => {}); test("todo", {todo:"reason"}, () => {});', undefined, 0, 1, 0, 1, 1],
  ["repeated source locations", 'for (const label of ["left", "right"]) describe(label, () => test("same", () => {}));', undefined, 0, 2, 0, 0, 0],
  ["repeated nested names", 'describe("left", () => test("same", () => {})); describe("right", () => test("same", () => {}));', undefined, 0, 2, 0, 0, 0],
];
for (const runnerKind of ["generic", "package-local"]) {
  for (const [scenario, source, filter, exitCode, passed, failed, skipped, todo] of nodeAccountingScenarios) {
    test(`M02 ${runnerKind} Node accounting: ${scenario}`, t => {
      const directory = mkdtempSync(join(tmpdir(), "tempo-m02-accounting-"));
      t.after(() => rmSync(directory, { recursive: true, force: true }));
      const testsDirectory = join(directory, "tests");
      mkdirSync(testsDirectory);
      writeFileSync(join(testsDirectory, "chess-line.test.mjs"), 'import { test, describe } from "node:test";\n' + source);
      let runner = resolve(repository, "tools/component-build/test-node.mjs");
      let args = [runner, testsDirectory, ...(filter ? [filter] : [])];
      if (runnerKind === "package-local") {
        cpSync(resolve(repository, "packages/chess-line/test.mjs"), join(directory, "test.mjs"));
        const reporter = resolve(repository, "packages/chess-line/tests/node-test-reporter.mjs");
        if (existsSync(reporter)) cpSync(reporter, join(testsDirectory, "node-test-reporter.mjs"));
        runner = join(directory, "test.mjs");
        args = [runner, ...(filter ? [filter] : [])];
      }
      const result = spawnIsolatedNode(args, { cwd: directory, encoding: "utf8", timeout: 10_000 });
      assert.ifError(result.error);
      const diagnostic = result.stdout + result.stderr;
      const report = result.stdout.trim().split("\n").filter(line => line.startsWith("{")).map(line => JSON.parse(line)).findLast(value => "executedIdentities" in value);
      assert.ok(report, diagnostic);
      assert.equal(result.status, exitCode, diagnostic);
      assert.deepEqual([report.passed, report.failed, report.skipped, report.todo], [passed, failed, skipped, todo], diagnostic);
      assert.equal(report.executedIdentities.length, passed, diagnostic);
      assert.equal(new Set(report.executedIdentities).size, passed, diagnostic);
      for (const identity of report.executedIdentities) assert.match(identity, /chess-line\.test\.mjs:\d+:\d+.*(?:one|first|second|same)/);
      if (failed) assert.match(diagnostic, /visible failure/);
    });
  }
}

function copyNodeRunner(directory) {
  for (const file of ["tools/component-build/test-node.mjs", "scripts/unit-worker-allocation.mjs",
    "scripts/development-budget.mjs", "scripts/host-study-activity.mjs", "config/resource-budgets.json"]) {
    mkdirSync(dirname(join(directory, file)), { recursive: true });
    cpSync(resolve(repository, file), join(directory, file));
  }
}
function laneFixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-m02-lane-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  for (const child of ["tools/component-build", "tests/unit", "tests/runner", "node_modules/vitest"]) mkdirSync(join(directory, child), { recursive: true });
  copyNodeRunner(directory);
  for (const file of ["test-lane.mjs", "node-test-reporter.mjs"]) cpSync(resolve(repository, "tools/component-build", file), join(directory, "tools/component-build", file));
  writeFileSync(join(directory, "tests/unit/consumer.test.ts"), "");
  writeFileSync(join(directory, "tests/runner/example.test.mjs"), 'import {test} from "node:test"; test("runner case",()=>{});');
  writeFileSync(join(directory, "tools/component-build/test-inventory.json"), JSON.stringify({ lanes: {
    frontend: ["tests/unit/consumer.test.ts"], integration: ["tests/unit/consumer.test.ts"],
    "producer-parity": [], "legacy-runner-wrappers": [], runners: ["tests/runner/example.test.mjs"],
  } }));
  writeFileSync(join(directory, "package.json"), JSON.stringify({ scripts: { "build:packages": "node build.mjs" } }));
  writeFileSync(join(directory, "build.mjs"), `import {appendFileSync,writeFileSync} from "node:fs";
appendFileSync("build-calls", "build\\n");
if (process.env.TEMPO_FIXTURE_BUILD_FAIL) process.exit(1);
writeFileSync("artifact", "current");`);
  writeFileSync(join(directory, "node_modules/vitest/vitest.mjs"), `import {readFileSync,writeFileSync} from "node:fs";
if(readFileSync("artifact", "utf8") !== "current") throw new Error("stale artifact");
writeFileSync("consumer-ran", "yes");
writeFileSync(process.argv.find(arg=>arg.startsWith("--outputFile=")).slice(13), JSON.stringify({numPassedTests:1,numFailedTests:0,testResults:[{assertionResults:[{status:"passed",fullName:"consumer"}]}]}));`);
  return directory;
}
for (const lane of ["frontend", "integration"]) test(`M02 ${lane} prepares packages once before consumers and rejects failed builds`, t => {
  const directory = laneFixture(t);
  const runner = join(directory, "tools/component-build/test-lane.mjs");
  const invoke = (filter, env = process.env) => spawnIsolatedNode([runner, lane, filter], { cwd: directory, encoding: "utf8", timeout: 10_000, env });
  assert.notEqual(invoke("missing").status, 0);
  assert.equal(existsSync(join(directory, "build-calls")), false);
  const fresh = invoke("consumer");
  assert.equal(fresh.status, 0, fresh.stdout + fresh.stderr);
  assert.equal(readFileSync(join(directory, "build-calls"), "utf8"), "build\n");
  rmSync(join(directory, "consumer-ran"));
  writeFileSync(join(directory, "artifact"), "stale");
  const failed = invoke("consumer", { ...process.env, TEMPO_FIXTURE_BUILD_FAIL: "1" });
  assert.notEqual(failed.status, 0);
  assert.match(failed.stderr, /Component package build failed/);
  assert.equal(existsSync(join(directory, "consumer-ran")), false);
  assert.equal(readFileSync(join(directory, "artifact"), "utf8"), "stale");
  const rebuilt = invoke("consumer");
  assert.equal(rebuilt.status, 0, rebuilt.stdout + rebuilt.stderr);
  assert.equal(readFileSync(join(directory, "artifact"), "utf8"), "current");
});
test("M02 runners lane shares generic Node accounting without building packages", t => {
  const directory = laneFixture(t);
  const runner = join(directory, "tools/component-build/test-lane.mjs");
  const invoke = filter => spawnIsolatedNode([runner, "runners", "example", ...(filter ? [filter] : [])], { cwd: directory, encoding: "utf8", timeout: 10_000 });
  const result = invoke();
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const report = JSON.parse(result.stdout.trim().split("\n").at(-1));
  assert.equal(report.passed, 1);
  assert.match(report.executedIdentities[0], /example.test.mjs:1:.*runner case/);
  assert.equal(existsSync(join(directory, "build-calls")), false);
  const unmatched = invoke("never-matches");
  assert.notEqual(unmatched.status, 0);
  assert.equal(JSON.parse(unmatched.stdout.trim().split("\n").at(-1)).passed, 0);
});
test("M02 explicit-only runner ownership stays strict without ordinary execution", t => {
  const directory = laneFixture(t);
  const inventoryPath = join(directory, "tools/component-build/test-inventory.json");
  const inventory = JSON.parse(readFileSync(inventoryPath, "utf8"));
  const runner = join(directory, "tools/component-build/test-lane.mjs");
  const smoke = "tests/runner/manual-smoke.test.mjs";
  writeFileSync(join(directory, smoke), 'throw new Error("Explicit smoke ran automatically");');
  const save = () => writeFileSync(inventoryPath, JSON.stringify(inventory));
  const list = () => spawnIsolatedNode([runner, "runners", "--list"], { cwd: directory, encoding: "utf8" });
  assert.notEqual(list().status, 0); // Present but unregistered.
  inventory.explicitRunnerFiles = [smoke]; save();
  const valid = list();
  assert.equal(valid.status, 0, valid.stdout + valid.stderr);
  assert.deepEqual(JSON.parse(valid.stdout).selectedFiles, ["tests/runner/example.test.mjs"]);
  assert.equal(JSON.parse(valid.stdout).inventory.runnerFiles, 2);
  const ordinary = spawnIsolatedNode([runner, "runners"], { cwd: directory, encoding: "utf8" });
  assert.equal(ordinary.status, 0, ordinary.stdout + ordinary.stderr);
  assert.equal(JSON.parse(ordinary.stdout.trim().split("\n").at(-1)).passed, 1);
  for (const invalid of [[smoke, smoke], [smoke, "tests/runner/example.test.mjs"], ["tests/runner/absent.test.mjs"]]) {
    inventory.explicitRunnerFiles = invalid; save();
    assert.notEqual(list().status, 0);
  }
  inventory.explicitRunnerFiles = [smoke]; save();
  writeFileSync(join(directory, "tests/runner/unowned.test.mjs"), "");
  assert.notEqual(list().status, 0); // Explicit declarations cannot hide unknown files.
  assert.equal(existsSync(join(directory, "build-calls")), false);
});
test("M02 unrelated lanes execute without controlled browser modules", t => {
  for (const lane of ["frontend", "integration", "runners"]) {
    const directory = laneFixture(t);
    assert.equal(existsSync(join(directory, "tests/controlled-browser")), false);
    assert.equal(existsSync(join(directory, "scripts/controlled-browser-host.mjs")), false);
    const result = spawnIsolatedNode([join(directory, "tools/component-build/test-lane.mjs"), lane], { cwd: directory, encoding: "utf8" });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.equal(existsSync(join(directory, "build-calls")), lane !== "runners");
  }
});
test("M02 controlled browser inventory distinguishes absent lanes from broken ownership", t => {
  const directory = laneFixture(t);
  const inventoryPath = join(directory, "tools/component-build/test-inventory.json");
  const inventory = JSON.parse(readFileSync(inventoryPath, "utf8"));
  const unitDirectory = join(directory, "tests/controlled-browser/unit");
  const file = "tests/controlled-browser/unit/owned.test.ts";
  const invoke = () => spawnIsolatedNode([join(directory, "tools/component-build/test-lane.mjs"), "runners", "--list"], { cwd: directory, encoding: "utf8" });
  assert.equal(invoke().status, 0);
  inventory.lanes["controlled-browser"] = [file];
  writeFileSync(inventoryPath, JSON.stringify(inventory));
  assert.notEqual(invoke().status, 0); // Registered lane without its owning directory.
  mkdirSync(unitDirectory, { recursive: true });
  assert.notEqual(invoke().status, 0); // Empty owning directory.
  writeFileSync(join(directory, file), "");
  for (const registered of [undefined, [], [file, file], [file, "tests/controlled-browser/unit/stale.test.ts"]]) {
    if (registered === undefined) delete inventory.lanes["controlled-browser"];
    else inventory.lanes["controlled-browser"] = registered;
    writeFileSync(inventoryPath, JSON.stringify(inventory));
    assert.notEqual(invoke().status, 0);
  }
  inventory.lanes["controlled-browser"] = [file];
  writeFileSync(inventoryPath, JSON.stringify(inventory));
  assert.equal(invoke().status, 0); // Validation needs no browser execution module.
  writeFileSync(join(unitDirectory, "new.test.ts"), "");
  assert.notEqual(invoke().status, 0);
  assert.equal(existsSync(join(directory, "build-calls")), false);
});
test("M02 controlled browser dispatch awaits and catches asynchronous failures", t => {
  const directory = laneFixture(t);
  const inventoryPath = join(directory, "tools/component-build/test-inventory.json");
  const inventory = JSON.parse(readFileSync(inventoryPath, "utf8"));
  const file = "tests/controlled-browser/unit/owned.test.ts";
  mkdirSync(join(directory, "tests/controlled-browser/unit"), { recursive: true });
  writeFileSync(join(directory, file), "");
  inventory.lanes["controlled-browser"] = [file];
  writeFileSync(inventoryPath, JSON.stringify(inventory));
  const runner = join(directory, "tools/component-build/test-lane.mjs");
  const invoke = () => spawnIsolatedNode([runner, "controlled-browser"], { cwd: directory, encoding: "utf8" });
  const missing = invoke();
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /Cannot find module/);
  writeFileSync(join(directory, "tests/controlled-browser/test-unit.mjs"),
    'export async function runControlledUnitSuite() { await new Promise(resolve => setTimeout(resolve, 10)); throw new Error("owned async failure"); }');
  const failed = invoke();
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /owned async failure/);
  assert.doesNotMatch(failed.stderr, /UnhandledPromiseRejection/);
  writeFileSync(join(directory, "tests/controlled-browser/test-unit.mjs"),
    'export async function runControlledUnitSuite() { await new Promise(resolve => setTimeout(resolve, 10)); console.log("owned async completion"); }');
  const completed = invoke();
  assert.equal(completed.status, 0, completed.stderr);
  assert.match(completed.stdout, /owned async completion/);
  assert.equal(existsSync(join(directory, "build-calls")), false);
});
test("M02 development and package test hooks prepare initial artifacts", () => {
  const scripts = JSON.parse(readFileSync(resolve(repository, "package.json"))).scripts;
  for (const hook of ["predev", "predev:local"]) assert.equal(scripts[hook], "npm run build:packages");
  assert.equal(scripts.dev, "vinext dev");
  assert.equal(scripts["dev:local"], "TEMPO_TARGET=local vite --config vite.static.config.ts");
  assert.equal(scripts["pretest:frontend"], undefined);
  assert.equal(scripts["pretest:component-integration"], undefined);
  assert.equal(JSON.parse(readFileSync(resolve(repository, "packages/chess-line/package.json"))).scripts.pretest, "npm run build");
});
for (const runnerKind of ["generic", "package-local"]) {
  for (const [scenario, output] of [["missing summary", ""], ["malformed JSON", "broken\n"], ["invalid summary", JSON.stringify({type:"test:summary",data:{success:true}})+"\n"]]) {
    test(`M02 ${runnerKind} rejects ${scenario} reporter output`, t => {
      const directory = mkdtempSync(join(tmpdir(), "tempo-m02-reporter-"));
      t.after(() => rmSync(directory, { recursive: true, force: true }));
      mkdirSync(join(directory, "tests"));
      writeFileSync(join(directory, "tests/chess-line.test.mjs"), 'import {test} from "node:test";test("one",()=>{});');
      const destination = runnerKind === "generic" ? join(directory, "tools/component-build") : join(directory, "tests");
      const runner = runnerKind === "generic" ? join(destination, "test-node.mjs") : join(directory, "test.mjs");
      if (runnerKind === "generic") copyNodeRunner(directory);
      else cpSync(resolve(repository, "packages/chess-line/test.mjs"), join(directory, "test.mjs"));
      writeFileSync(join(destination, "node-test-reporter.mjs"), `export default async function* (events) { for await (const event of events) { void event; } yield ${JSON.stringify(output)}; }`);
      const result = spawnIsolatedNode([runner, ...(runnerKind === "generic" ? [join(directory, "tests")] : [])], { cwd: directory, encoding: "utf8", timeout: 10_000 });
      assert.notEqual(result.status, 0, result.stdout);
      assert.match(result.stderr, /Malformed|Missing final/);
    });
  }
}

test("Python normalized distribution dependencies select their real component producers", async () => {
  const { selectComponents } = await import("../selection.mjs");
  const selected = selectComponents(manifest, ["learning"]);
  for (const owner of ["learning", "learning-review-application", "scheduling", "chess-core", "content-publication-contracts", "game-evidence-contracts"])
    assert.ok(selected.some(component => component.id === owner), "Missing declared producer: " + owner);
});

test("root Board UI public stylesheet resolves in the real Vite assembly without admitting private subpaths", async () => {
  const { createServer } = await import("vite");
  const server = await createServer({ configFile: false, root: repository,
    resolve: { tsconfigPaths: true }, server: { watch: null },
    optimizeDeps: { noDiscovery: true, include: [] } });
  try {
    const metadata = JSON.parse(readFileSync(resolve(repository, "packages/board-ui/package.json")));
    const expected = resolve(repository, "packages/board-ui", metadata.exports["./styles.css"]);
    for (const consumer of ["app/features/game-review/GameReviewView.tsx", "app/features/repertoire-review/PositionViewer.tsx"]) {
      const importer = resolve(repository, consumer);
      const result = await server.environments.client.pluginContainer.resolveId("@tempo/board-ui/styles.css", importer);
      assert.equal(result?.id, expected);
      assert.ok(readFileSync(importer, "utf8").includes('import "@tempo/board-ui/styles.css"'));
      assert.equal(await server.environments.client.pluginContainer.resolveId("@tempo/board-ui/private.css", importer), null);
    }
  } finally { await server.close(); }
});
