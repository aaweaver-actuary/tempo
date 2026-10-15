import assert from "node:assert/strict";
import { test } from "node:test";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { checkComponents, repository, validateManifest } from "../check-components.mjs";

const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3.12";
const original = JSON.parse(readFileSync(join(repository, "components.json")));
function contractFixture(context) {
  const root = mkdtempSync(join(tmpdir(), "tempo-d-contract-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "contracts"));
  for (const name of ["catalog.json", "check_dependencies.py"]) cpSync(join(repository, "contracts", name), join(root, "contracts", name));
  cpSync(join(repository, "contracts/learning"), join(root, "contracts/learning"), { recursive: true });
  const component = structuredClone(original.components.find(value => value.id === "learning-contracts"));
  Object.assign(component, {
    status: "implemented", catalog: "contracts/catalog.json", testInventory: "REGRESSIONS.md",
    buildInputs: ["pyproject.toml", "src", "schemas", "fixtures", "examples", "tests", "README.md", "REGRESSIONS.md"],
    buildCommand: "python -m build --no-isolation",
    tests: [{ owner: "learning-contracts", kind: "python", cwd: "contracts/learning", command: "python -m pytest tests", inputs: ["tests"] }],
  });
  const manifest = { schemaVersion: 1, components: [component] };
  const save = () => writeFileSync(join(root, "components.json"), JSON.stringify(manifest));
  save();
  return { root, manifest, component, save };
}
function check(fixture, options = {}) {
  fixture.save();
  return checkComponents(fixture.root, python, { componentIds: ["learning-contracts"], ...options });
}
test("D contract registration validates versioned namespaces without application source", context => {
  const fixture = contractFixture(context);
  assert.equal(check(fixture)[0].id, "learning-contracts");
});
test("D implemented contract rejects missing source", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/learning/src"), { recursive: true });
  assert.throws(() => check(fixture), /input|source/i);
});
for (const field of ["name", "version"]) test(`D contract rejects wrong artifact ${field}`, context => {
  const fixture = contractFixture(context);
  fixture.component.artifact[field] = "wrong";
  assert.throws(() => check(fixture), /identity|artifact/i);
});
test("D contract rejects missing public namespace", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/learning/src/tempo_learning_contracts/v1.py"));
  assert.throws(() => check(fixture), /namespace|module/i);
});
test("D contract rejects a missing schema rather than accepting an empty glob", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/learning/schemas/v1/AttemptRequest.schema.json"));
  assert.throws(() => check(fixture), /schema/i);
});
test("D contract cannot declare schemas omitted from its wheel", context => {
  const fixture = contractFixture(context);
  const project = join(fixture.root, "contracts/learning/pyproject.toml");
  writeFileSync(project, readFileSync(project, "utf8").replace('"schemas" = "tempo_learning_contracts/schemas"', '"fixtures" = "tempo_learning_contracts/fixtures"'));
  assert.throws(() => check(fixture), /schema|export/i);
});
test("D contract reuses the owning checker for undeclared lazy imports", context => {
  const fixture = contractFixture(context);
  writeFileSync(join(fixture.root, "contracts/learning/src/tempo_learning_contracts/hidden.py"), "def deferred():\n    import services.content.internal\n");
  assert.throws(() => check(fixture), /undeclared|cross-component/i);
});
test("D selectors reject unknown, empty and planned owners", context => {
  const fixture = contractFixture(context);
  assert.throws(() => check(fixture, { componentIds: ["unknown"] }), /Unknown component/);
  assert.throws(() => check(fixture, { componentIds: [] }), /[Ee]mpty|[Zz]ero/);
  fixture.component.status = "planned";
  fixture.component.buildCommand = null;
  fixture.component.tests = [];
  assert.throws(() => check(fixture), /planned|implemented/i);
});
test("D implemented suite rejects an incorrect cwd", context => {
  const fixture = contractFixture(context);
  fixture.component.tests[0].cwd = "absent";
  assert.throws(() => check(fixture), /cwd|working directory/i);
});
test("D planned component cannot claim executable tests", context => {
  const fixture = contractFixture(context);
  fixture.component.status = "planned";
  fixture.component.buildCommand = null;
  assert.throws(() => validateManifest(fixture.manifest, fixture.root), /Planned component/);
});
test("D selected TypeScript validation never invokes Python or needs backend source", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-d-typescript-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const component = original.components.find(value => value.id === "chess-line");
  mkdirSync(join(root, "packages"));
  cpSync(join(repository, component.root), join(root, component.root), { recursive: true });
  const dependencyRoot = existsSync(join(repository, "node_modules/chess.js")) ? "node_modules" : "packages/chess-line/node_modules";
  symlinkSync(join(repository, dependencyRoot), join(root, "node_modules"));
  writeFileSync(join(root, "components.json"), JSON.stringify({ schemaVersion: 1, components: [component] }));
  assert.equal(checkComponents(root, "/python-must-not-run", { componentIds: ["chess-line"] })[0].id, "chess-line");
});
test("D component listing performs no build, producer or test execution", context => {
  const fixture = contractFixture(context);
  const runner = resolve(repository, "tools/component-build/test-lane.mjs");
  const environment = { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [runner, "--component", "learning-contracts", "--list"], { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /learning-contracts/);
  assert.equal(existsSync(join(fixture.root, "contracts/learning/dist")), false);
});

function invokeLane(fixture, argumentsList, environment = {}) {
  fixture.save();
  const env = { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: python, ...environment };
  delete env.NODE_TEST_CONTEXT;
  return spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"), ...argumentsList], { env, encoding: "utf8", timeout: 15_000 });
}
function nodeSuite(fixture) {
  const tests = join(fixture.root, "contracts/learning/tests");
  writeFileSync(join(tests, "dispatch.test.mjs"), `import {test} from "node:test"; import assert from "node:assert/strict"; import {appendFileSync} from "node:fs";
test("actual selected contract fixture",()=>{ assert.equal(process.cwd(), ${JSON.stringify(realpathSync(join(fixture.root, "contracts/learning")))}); appendFileSync("executions", "case\\n"); });`);
  return { owner: "learning-contracts", kind: "node", cwd: "contracts/learning", inputs: ["tests"], command: `node '${join(repository, "tools/component-build/test-node.mjs")}' tests` };
}
test("D selected contract execution runs owning cases without frontend, database or model setup", context => {
  const fixture = contractFixture(context);
  fixture.component.tests = [nodeSuite(fixture)];
  const result = invokeLane(fixture, ["--component", "learning-contracts"], { TEMPO_COMPONENT_PYTHON: "/python-must-not-run" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /actual selected contract fixture/);
  assert.equal(readFileSync(join(fixture.root, "contracts/learning/executions"), "utf8"), "case\n");
  assert.equal(existsSync(join(fixture.root, "app")), false);
});
test("D shared suites dispatch once when selected repeatedly", context => {
  const fixture = contractFixture(context);
  fixture.manifest.suites = [{ id: "shared", ...nodeSuite(fixture) }];
  const result = invokeLane(fixture, ["--suite", "shared", "--suite", "shared"]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(readFileSync(join(fixture.root, "contracts/learning/executions"), "utf8"), "case\n");
});
test("D zero-case execution cannot succeed through an empty owning runner", context => {
  const fixture = contractFixture(context);
  fixture.component.tests[0].command = "node -e 'process.exit(0)' --";
  const result = invokeLane(fixture, ["--component", "learning-contracts"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /zero executed|suite failed/i);
});
test("D checker and restricted builder listing never invoke a toolchain", context => {
  const fixture = contractFixture(context);
  fixture.save();
  for (const entrypoint of ["check-components.mjs", "restricted-build.mjs"]) {
    const result = spawnSync(process.execPath, [join(repository, "tools/component-build", entrypoint), "--component", "learning-contracts", "--list"], { encoding: "utf8", env: { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" } });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /learning-contracts/);
  }
  assert.equal(existsSync(join(fixture.root, "test-results")), false);
});
