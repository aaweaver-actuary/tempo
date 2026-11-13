import assert from "node:assert/strict";
import { test } from "node:test";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { checkComponents, repository, validateManifest, compositionPaths, validateCompositionOwnership, retainedBackendPaths, validateRetainedBackendOwnership, validateRetainedBackendInterfaces, reviewCommandOwnerIds, reviewCommandPaths, validateReviewCommandOwnership } from "../check-components.mjs";
import { validateOwnedPaths, selectComponents } from "../selection.mjs";

const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3.12";
const original = JSON.parse(readFileSync(join(repository, "components.json")));
test("T02 pure package tracked sources have exactly one declared owner", () => {
  const result = spawnSync("git", ["ls-files", "packages/board-ui/src", "packages/evidence-math/src"], { cwd: repository, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const paths = result.stdout.trim().split("\n").filter(Boolean);
  assert.ok(paths.length);
  validateOwnedPaths(original, paths, ["board-ui", "evidence-math"]);
});
test("T02 mapped browser and CLI sources have exactly one explicit owner", () => {
  const paths = compositionPaths(repository);
  assert.ok(["tempo", "tempo-restart.sh", "public/sw.js", "app/page.tsx", "static/src/main.tsx", "vite.static.config.ts", "tools/component-build/check-components.mjs"].every(path => paths.includes(path)));
  validateCompositionOwnership(original, paths);
  for (const id of ["study-web", "tempo-cli", "integration"]) {
    const component = original.components.find(item => item.id === id);
    assert.equal(component.status, "mapped");
    assert.equal(component.artifact, null);
    assert.throws(() => selectComponents(original, [id]), /mapped, not implemented/);
  }
  assert.ok(original.components.find(item => item.id === "tempo-cli").dependencies.includes("integration"));
});
test("T02 discovers a newly tracked unowned browser source", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-composition-map-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  assert.equal(spawnSync("git", ["init", "-q", root]).status, 0);
  mkdirSync(join(root, "app"));
  writeFileSync(join(root, "app", "new-view.tsx"), "export default null;\n");
  assert.equal(spawnSync("git", ["-C", root, "add", "app/new-view.tsx"]).status, 0);
  const paths = compositionPaths(root);
  assert.deepEqual(paths, ["app/new-view.tsx"]);
  assert.throws(() => validateCompositionOwnership(original, paths), /found 0/);
});
test("T02 mapped composition rejects unowned, duplicate and untracked files", () => {
  const paths = compositionPaths(repository);
  assert.throws(() => validateCompositionOwnership(original, [...paths, "app/new-tracked-view.tsx"]), /found 0/);
  const duplicate = structuredClone(original);
  duplicate.components.find(item => item.id === "integration").ownedFiles.push("app/page.tsx");
  assert.throws(() => validateCompositionOwnership(duplicate, paths), /found 2/);
  const stale = structuredClone(original);
  stale.components.find(item => item.id === "study-web").ownedFiles.push("app/not-tracked.tsx");
  assert.throws(() => validateCompositionOwnership(stale, paths), /untracked source/);
});
test("T02 selected backend seams have exact owners and source-backed entrypoints", () => {
  const paths = retainedBackendPaths(repository);
  assert.equal(paths.length, 37);
  assert.ok(paths.includes("backend/app/study/next_exercise.py"));
  assert.ok(paths.includes("database/tempo_storage/study/next_exercise_reads.py"));
  validateRetainedBackendOwnership(original, paths);
  validateRetainedBackendInterfaces(original, repository, python);
  for (const id of ["study-next-core", "study-next-storage", "study-position-issuance", "study-position-storage", "game-review-http"]) {
    const owner = original.components.find(item => item.id === id);
    assert.equal(owner.status, "mapped"); assert.equal(owner.artifact, null);
    assert.throws(() => selectComponents(original, [id]), /mapped, not implemented/);
  }
});
test("T02 discovers a newly tracked unowned backend contract", context => {
  const root = mkdtempSync(join(tmpdir(), "tempo-backend-map-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  assert.equal(spawnSync("git", ["init", "-q", root]).status, 0);
  mkdirSync(join(root, "backend/app/contracts"), { recursive: true });
  writeFileSync(join(root, "backend/app/contracts/new_contract.py"), "class NewContract: pass\n");
  assert.equal(spawnSync("git", ["-C", root, "add", "backend/app/contracts/new_contract.py"]).status, 0);
  const paths = retainedBackendPaths(root);
  assert.deepEqual(paths, ["backend/app/contracts/new_contract.py"]);
  assert.throws(() => validateRetainedBackendOwnership(original, paths), /found 0/);
});
test("T02 backend seam guards reject unowned, overlap, stale and false entrypoint claims", () => {
  const paths = retainedBackendPaths(repository);
  assert.throws(() => validateRetainedBackendOwnership(original, [...paths, "backend/app/contracts/new_tracked_contract.py"]), /found 0/);
  const overlap = structuredClone(original);
  overlap.components.find(item => item.id === "study-next-storage").ownedFiles.push("backend/app/study/next_exercise.py");
  assert.throws(() => validateRetainedBackendOwnership(overlap, paths), /found 2/);
  const stale = structuredClone(original);
  stale.components.find(item => item.id === "study-next-storage").ownedFiles.push("database/tempo_storage/study/not_tracked.py");
  assert.throws(() => validateRetainedBackendOwnership(stale, paths), /untracked source/);
  const empty = structuredClone(original);
  empty.components.find(item => item.id === "study-next-core").publicExports = [];
  assert.throws(() => validateRetainedBackendInterfaces(empty, repository, python), /lacks named interfaces/);
  const falseInterface = structuredClone(original);
  falseInterface.components.find(item => item.id === "study-next-core").publicExports.push("backend/app/study/next_exercise.py#not_an_entrypoint");
  assert.throws(() => validateRetainedBackendInterfaces(falseInterface, repository, python), /Missing retained interface/);
  const escaping = structuredClone(original);
  const escapedOwner = escaping.components.find(item => item.id === "study-next-core");
  escapedOwner.ownedFiles.push("../outside.py");
  escapedOwner.publicExports.push("../outside.py#read");
  assert.throws(() => validateRetainedBackendInterfaces(escaping, repository, python), /Invalid retained interface/);
});
test("T02 selected review command seams retain exact owners and named interfaces", () => {
  const paths = reviewCommandPaths(repository);
  assert.equal(paths.length, 12);
  validateReviewCommandOwnership(original, paths);
  validateRetainedBackendInterfaces(original, repository, python, reviewCommandOwnerIds);
  for (const id of reviewCommandOwnerIds) assert.throws(() => selectComponents(original, [id]), /mapped, not implemented/);
});
test("T02 review command scope rejects unowned, overlapping, stale and false interfaces", () => {
  const paths = reviewCommandPaths(repository);
  assert.throws(() => validateReviewCommandOwnership(original, [...paths, "backend/app/unowned-review.py"]), /found 0/);
  const overlap = structuredClone(original);
  overlap.components.find(item => item.id === "backend-contracts").ownedFiles.push(paths[0]);
  assert.throws(() => validateReviewCommandOwnership(overlap, paths), /found 2/);
  const stale = structuredClone(original);
  stale.components.find(item => item.id === "study-review-command").ownedFiles.push("backend/app/untracked-review.py");
  assert.throws(() => validateReviewCommandOwnership(stale, paths), /untracked source/);
  const wrong = structuredClone(original);
  wrong.components.find(item => item.id === "study-review-command").publicExports.push("backend/app/review_commands.py#missing");
  assert.throws(() => validateRetainedBackendInterfaces(wrong, repository, python, reviewCommandOwnerIds), /Missing retained interface/);
});
test("Learning review registers its actual application, tooling and native suites", () => {
  const componentIds = ["learning-review-application"];
  validateManifest(original, repository, { componentIds });
  for (const id of ["browser-intents", "study-session"]) {
    assert.equal(original.components.find(component => component.id === id)?.status, "implemented");
  }
  const environment = { ...process.env, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"),
    "--component", "learning-review-application", "--list"], { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const { selected } = JSON.parse(result.stdout);
  const nativeOwner = original.components.find(component => component.id === "learning-review-application").tests;
  assert.deepEqual(selected.filter(suite => suite.owner === "learning-review-application").map(({id, ...suite}) => suite), nativeOwner);
  assert.equal(selected.filter(suite => suite.owner === "learning-review-application").length, 3);
  assert.ok(selected.some(suite => suite.owner === "learning-review-application" && suite.kind === "postgres"));
});
test("#200 registered pure packages validate and list their actual owning suites without setup", () => {
  const componentIds = ["scheduling", "chess-core"];
  const checked = checkComponents(repository, python, { componentIds });
  assert.deepEqual(checked.map(component => component.id), componentIds);
  const environment = { ...process.env, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" };
  delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"),
    "--component", "scheduling", "--component", "chess-core", "--list"],
  { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const { selected } = JSON.parse(result.stdout);
  assert.deepEqual(selected.map(suite => [suite.id, suite.owner, suite.cwd]),
    componentIds.map(id => [id, id, `packages/${id}`]));
});

function contractFixture(context) {
  const root = mkdtempSync(join(tmpdir(), "tempo-d-contract-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "contracts"));
  for (const name of ["catalog.json", "check_dependencies.py"]) cpSync(join(repository, "contracts", name), join(root, "contracts", name));
  cpSync(join(repository, "contracts/game-evidence"), join(root, "contracts/game-evidence"), { recursive: true });
  const component = structuredClone(original.components.find(value => value.id === "game-evidence-contracts"));
  Object.assign(component, {
    status: "implemented", catalog: "contracts/catalog.json", testInventory: "REGRESSIONS.md",
    buildInputs: ["pyproject.toml", "src", "schemas", "fixtures", "examples", "tests", "README.md", "REGRESSIONS.md"],
    buildCommand: "python -m build --no-isolation",
    tests: [{ owner: "game-evidence-contracts", kind: "python", cwd: "contracts/game-evidence", command: "python -m pytest tests", inputs: ["tests"] }],
  });
  const manifest = { schemaVersion: 1, components: [component] };
  const save = () => writeFileSync(join(root, "components.json"), JSON.stringify(manifest));
  save();
  return { root, manifest, component, save };
}
function check(fixture, options = {}) {
  fixture.save();
  return checkComponents(fixture.root, python, { componentIds: ["game-evidence-contracts"], ...options });
}
test("D contract registration validates versioned namespaces without application source", context => {
  const fixture = contractFixture(context);
  assert.equal(check(fixture)[0].id, "game-evidence-contracts");
});
test("D implemented contract rejects missing source", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/game-evidence/src"), { recursive: true });
  assert.throws(() => check(fixture), /input|source/i);
});
for (const field of ["name", "version"]) test(`D contract rejects wrong artifact ${field}`, context => {
  const fixture = contractFixture(context);
  fixture.component.artifact[field] = "wrong";
  assert.throws(() => check(fixture), /identity|artifact/i);
});
test("D contract rejects missing public namespace", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/game-evidence/src/tempo_game_evidence_contracts/v1.py"));
  assert.throws(() => check(fixture), /namespace|module/i);
});
test("D contract rejects a missing schema rather than accepting an empty glob", context => {
  const fixture = contractFixture(context);
  rmSync(join(fixture.root, "contracts/game-evidence/schemas/v1/ImportedGame.schema.json"));
  assert.throws(() => check(fixture), /schema/i);
});
test("D contract cannot declare schemas omitted from its wheel", context => {
  const fixture = contractFixture(context);
  const project = join(fixture.root, "contracts/game-evidence/pyproject.toml");
  writeFileSync(project, readFileSync(project, "utf8").replace('"schemas" = "tempo_game_evidence_contracts/schemas"', '"fixtures" = "tempo_game_evidence_contracts/fixtures"'));
  assert.throws(() => check(fixture), /schema|export/i);
});
test("D contract reuses the owning checker for undeclared lazy imports", context => {
  const fixture = contractFixture(context);
  writeFileSync(join(fixture.root, "contracts/game-evidence/src/tempo_game_evidence_contracts/hidden.py"), "def deferred():\n    import services.content.internal\n");
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
  const result = spawnSync(process.execPath, [runner, "--component", "game-evidence-contracts", "--list"], { env: environment, encoding: "utf8" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /game-evidence-contracts/);
  assert.equal(existsSync(join(fixture.root, "contracts/game-evidence/dist")), false);
});

function invokeLane(fixture, argumentsList, environment = {}) {
  fixture.save();
  const env = { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: python, ...environment };
  delete env.NODE_TEST_CONTEXT;
  return spawnSync(process.execPath, [join(repository, "tools/component-build/test-lane.mjs"), ...argumentsList], { env, encoding: "utf8", timeout: 15_000 });
}
function nodeSuite(fixture) {
  const tests = join(fixture.root, "contracts/game-evidence/tests");
  writeFileSync(join(tests, "dispatch.test.mjs"), `import {test} from "node:test"; import assert from "node:assert/strict"; import {appendFileSync} from "node:fs";
test("actual selected contract fixture",()=>{ assert.equal(process.cwd(), ${JSON.stringify(realpathSync(join(fixture.root, "contracts/game-evidence")))}); appendFileSync("executions", "case\\n"); });`);
  return { owner: "game-evidence-contracts", kind: "node", cwd: "contracts/game-evidence", inputs: ["tests"], command: `node '${join(repository, "tools/component-build/test-node.mjs")}' tests` };
}
test("D selected contract execution runs owning cases without frontend, database or model setup", context => {
  const fixture = contractFixture(context);
  fixture.component.tests = [nodeSuite(fixture)];
  const result = invokeLane(fixture, ["--component", "game-evidence-contracts"], { TEMPO_COMPONENT_PYTHON: "/python-must-not-run" });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /actual selected contract fixture/);
  assert.equal(readFileSync(join(fixture.root, "contracts/game-evidence/executions"), "utf8"), "case\n");
  assert.equal(existsSync(join(fixture.root, "app")), false);
});
test("D shared suites dispatch once when selected repeatedly", context => {
  const fixture = contractFixture(context);
  fixture.manifest.suites = [{ id: "shared", ...nodeSuite(fixture) }];
  const result = invokeLane(fixture, ["--suite", "shared", "--suite", "shared"]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(readFileSync(join(fixture.root, "contracts/game-evidence/executions"), "utf8"), "case\n");
});
test("D zero-case execution cannot succeed through an empty owning runner", context => {
  const fixture = contractFixture(context);
  fixture.component.tests[0].command = "node -e 'process.exit(0)' --";
  const result = invokeLane(fixture, ["--component", "game-evidence-contracts"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /zero executed|suite failed/i);
});
test("D checker and restricted builder listing never invoke a toolchain", context => {
  const fixture = contractFixture(context);
  fixture.save();
  for (const entrypoint of ["check-components.mjs", "restricted-build.mjs"]) {
    const result = spawnSync(process.execPath, [join(repository, "tools/component-build", entrypoint), "--component", "game-evidence-contracts", "--list"], { encoding: "utf8", env: { ...process.env, TEMPO_COMPONENT_ROOT: fixture.root, TEMPO_COMPONENT_PYTHON: "/python-must-not-run" } });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /game-evidence-contracts/);
  }
  assert.equal(existsSync(join(fixture.root, "test-results")), false);
});

test('retired standalone Learning owner is rejected instead of silently selecting another suite', async () => {
  const {selectComponents, selectSuites} = await import('../selection.mjs');
  assert.throws(() => selectComponents(original, ['learning']), /Unknown component: learning/);
  assert.throws(() => selectSuites(original, ['learning']), /Unknown suite: learning/);
});
