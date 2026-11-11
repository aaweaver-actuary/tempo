import assert from "node:assert/strict";
import { test } from "node:test";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { checkInterfaceBoundaries, checkPythonIntake, writtenTables } from "../check-interface-boundaries.mjs";
import { repository } from "../check-components.mjs";
import { testFiles } from "../test-node.mjs";

const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3";

test("Declared facade may re-export its own controller but a foreign owner cannot import it", t => {
  const f = fixture(t);
  f.write("backend/app/study/interfaces/__init__.py", "from ..private import hidden\n__all__ = ['hidden']\n");
  f.manifest.components[1].facades = [{ path: "backend/app/study/interfaces", targets: ["backend/app/study/private.py"] }];
  f.write("backend/app/games/consumer.py", "from app.study.interfaces import hidden\n");
  assert.doesNotThrow(f.check);
  f.write("backend/app/study/other.py", "hidden = True\n");
  f.write("backend/app/study/interfaces/__init__.py", "from ..other import hidden\n__all__ = ['hidden']\n");
  assert.throws(f.check, /Interface imports private implementation/);
  f.write("backend/app/study/interfaces/__init__.py", "from ..private import hidden\n__all__ = ['hidden']\n");
  f.write("backend/app/games/consumer.py", "from app.study.private import hidden\n");
  assert.throws(f.check, /Private cross-component import/);
});

test("Selected real owners pass and reject injected private, infrastructure and SQL dependencies", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-selected-boundary-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  cpSync(resolve(repository, "backend/app"), join(root, "backend/app"), { recursive: true });
  cpSync(resolve(repository, "database/tempo_storage"), join(root, "database/tempo_storage"), { recursive: true });
  cpSync(resolve(repository, "app/features"), join(root, "app/features"), { recursive: true });
  cpSync(resolve(repository, "app/views"), join(root, "app/views"), { recursive: true });
  const manifest = JSON.parse(readFileSync(resolve(repository, "tools/component-build/selected-interface-owners.json"), "utf8"));
  const check = () => checkInterfaceBoundaries(manifest, root, python);
  assert.equal(check().files, 12);
  const inject = (file, code, expected) => {
    const path = join(root, file), original = readFileSync(path, 'utf8');
    writeFileSync(path, original + '\n' + code + '\n');
    assert.throws(check, expected);
    writeFileSync(path, original);
  };
  inject('backend/app/games/review.py', 'def injected():\n    from app.study.next_exercise import hidden', /Undeclared selected import/);
  inject('backend/app/contracts/queue_issuance.py', 'from app.games.comparison import GameFacts', /Undeclared selected import/);
  inject('backend/app/games/comparison.py', 'def injected():\n    import sqlite3 as hidden', /Pure selected source imports infrastructure/);
  inject('backend/app/games/comparison.py', 'def injected(conn):\n    conn.execute("SELECT * FROM daily_queue")', /Pure selected source contains SQL execution/);
  inject('backend/app/games/comparison.py', 'def injected():\n    import fastapi as hidden', /Pure selected source imports infrastructure/);
  inject('backend/app/study/next_exercise.py', 'def injected(conn):\n    conn.execute("SELECT * FROM daily_queue")', /Pure selected source contains SQL execution/);
  inject('backend/app/games/review.py', 'import app.games.comparison as api\ndef injected():\n    return api.HiddenStore', /Unavailable selected public member/);
  const storage = join(root, 'database/tempo_storage/game_review.py');
  const storageSource = readFileSync(storage, 'utf8');
  rmSync(storage);
  assert.throws(check, /Unavailable selected source\/target|Unavailable selected target/);
  writeFileSync(storage, storageSource);
  const comparator = join(root, 'backend/app/games/comparison.py');
  const comparatorSource = readFileSync(comparator, 'utf8');
  writeFileSync(comparator, comparatorSource.replace('class GameFacts:', 'class OtherFacts:'));
  assert.throws(check, /Unavailable selected public exports/);
  writeFileSync(comparator, comparatorSource);
  assert.doesNotThrow(check);
});

test("Selected real optional UI rejects private Study imports and stages only the public controller facade", t => {
  const root = mkdtempSync(join(tmpdir(), "tempo-selected-ui-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  cpSync(resolve(repository, "backend/app"), join(root, "backend/app"), { recursive: true });
  cpSync(resolve(repository, "database/tempo_storage"), join(root, "database/tempo_storage"), { recursive: true });
  cpSync(resolve(repository, "app/features"), join(root, "app/features"), { recursive: true });
  cpSync(resolve(repository, "app/views"), join(root, "app/views"), { recursive: true });
  const manifest = JSON.parse(readFileSync(resolve(repository, "tools/component-build/selected-interface-owners.json"), "utf8"));
  const check = () => checkInterfaceBoundaries(manifest, root, python);
  assert.deepEqual(check().frontend, { files: 2, controllerConsumers: 1, stagedMissing: [] });
  const manifestPath = join(root, 'owners.json');
  writeFileSync(manifestPath, JSON.stringify(manifest));
  const cli = (extra = {}) => spawnSync(process.execPath, [resolve(repository, 'tools/component-build/check-interface-boundaries.mjs'), manifestPath, root],
    { encoding: 'utf8', env: { ...process.env, TEMPO_COMPONENT_PYTHON: python, ...extra }, timeout: 15000 });
  assert.equal(cli({ TEMPO_REQUIRE_STUDY_UI: '1' }).status, 0);
  const stagedPaths = ['app/features/study/index.ts', 'app/features/study/controller.ts'];
  const stagedSources = stagedPaths.map(path => readFileSync(join(root, path), 'utf8'));
  for (const path of stagedPaths) rmSync(join(root, path));
  assert.deepEqual(check().frontend.stagedMissing, stagedPaths);
  assert.equal(cli({ TEMPO_REQUIRE_STUDY_UI: '1' }).status, 1);
  stagedPaths.forEach((path, index) => writeFileSync(join(root, path), stagedSources[index]));
  for (const source of ['app/features/game-review/GameReviewView.tsx']) {
    const path = join(root, source), original = readFileSync(path, 'utf8');
    for (const violation of [
      'import "../../state/training-store";',
      'export * from "@/app/state/training-store.js";',
      'const hidden = () => import("../../views/fetchAndInitializeQueue.js");',
      'export * from "../../views";',
      'const hidden = () => import("../study/controller.js");',
    ]) {
      writeFileSync(path, original + '\n' + violation);
      assert.throws(check, /Private selected frontend import/);
      if (violation.startsWith('import ')) {
        const result = cli();
        assert.equal(result.status, 1);
        assert.match(result.stderr, /Private selected frontend import/);
      }
    }
    writeFileSync(path, original);
  }
  for (const [source, violation] of [
    ['app/views/legacy-workspace.tsx', 'export * from "../state/training-store.js";'],
    ['app/views/home_view.tsx', 'const hidden = () => import("../features/study/controller.js");'],
  ]) {
    const path = join(root, source), original = readFileSync(path, 'utf8');
    writeFileSync(path, original + '\n' + violation);
    assert.throws(check, /Private selected frontend import/);
    writeFileSync(path, original);
  }
  const selected = join(root, 'app/views/home_view.tsx'), original = readFileSync(selected, 'utf8');
  writeFileSync(selected, original.replace('useStudyReviewController({', 'notTheController({'));
  assert.throws(check, /must call the public Study controller/);
  for (const violation of [
    'import { createStudySession as duplicate } from "@tempo/study-session"; duplicate({});',
    'import * as sessions from "@tempo/study-session"; sessions.createStudySession({});',
    'import { reviewStudySession as duplicate } from "../lib/review-outbox"; duplicate();',
  ]) {
    writeFileSync(selected, original + '\n' + violation);
    assert.throws(check, /Duplicate selected Study session creation/);
  }
  writeFileSync(selected, original.replace('import { useStudyReviewController } from "../features/study";', 'import * as study from "../features/study";').replace('useStudyReviewController({', 'study.useStudyReviewController({'));
  assert.doesNotThrow(check);
  writeFileSync(selected, original);
  for (const source of ['app/features/study/index.ts', 'app/features/study/controller.ts']) {
    const file = join(root, source), previous = readFileSync(file, 'utf8');
    writeFileSync(file, previous + '\nimport { createStudySession as duplicate } from "@tempo/study-session"; duplicate({});');
    assert.throws(check, /Duplicate selected Study session creation/);
    writeFileSync(file, previous);
  }
  const facade = join(root, 'app/features/study/index.ts'), facadeOriginal = readFileSync(facade, 'utf8');
  writeFileSync(facade, 'export { hidden } from "./other.js";');
  assert.throws(check, /undeclared private implementation/);
  writeFileSync(facade, 'export * from "../../state/training-store.js";');
  assert.throws(check, /undeclared private implementation/);
  writeFileSync(facade, facadeOriginal);
  manifest.frontend.unexpected = true;
  assert.throws(check, /Invalid selected frontend declaration/);
});
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "tempo-interface-guard-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const write = (path, source) => { mkdirSync(dirname(join(root, path)), { recursive: true }); writeFileSync(join(root, path), source); };
  for (const owner of ["games", "study"]) {
    write(`backend/app/${owner}/__init__.py`, "");
    write(`backend/app/${owner}/interfaces/README.md`, `# ${owner} public interface\nOwner: ${owner}. Commands and data are semantic.\nAccepted is persisted, pending is not available. Only the owner refreshes Study.\n`);
  }
  write("backend/app/games/interfaces/__init__.py", "class GameFacts: pass\n__all__ = ['GameFacts']\n");
  write("backend/app/study/interfaces/__init__.py", "class PriorityIntake: pass\n__all__ = ['PriorityIntake']\n");
  write("backend/app/games/consumer.py", "from app.study.interfaces import PriorityIntake\ndef send(port, command):\n    return port.accept(command)\n");
  write("backend/app/study/private.py", "hidden = True\n");
  write("backend/app/study/intake.py", "def accept(store, command):\n    return store.record(command)\n");
  const manifest = {
    components: [
      { id: "games", sources: ["backend/app/games"], interfaces: ["backend/app/games/interfaces"], modules: { "app.games": "backend/app/games" }, dependencies: ["study"], tables: ["game_requests"] },
      { id: "study", sources: ["backend/app/study"], interfaces: ["backend/app/study/interfaces"], modules: { "app.study": "backend/app/study" }, dependencies: [], tables: ["study_requests", "daily_queue"] },
    ],
    intakes: [{ path: "backend/app/study/intake.py", functions: ["accept"], forbiddenCalls: ["eligible*", "prepare*", "promote*"] }],
  };
  return { root, write, manifest, check: () => checkInterfaceBoundaries(manifest, root, python) };
}

test("Interfaces permit typed public intake and do not mutate the owner map", t => {
  const f = fixture(t), before = JSON.stringify(f.manifest), report = f.check();
  assert.deepEqual(report.edges, { games: ["study"], study: [] });
  assert.equal(JSON.stringify(f.manifest), before);
  assert.equal(report.files, 7);
});

for (const [name, source] of [
  ["direct", "from app.study.private import hidden"],
  ["aliased", "import app.study.private as disguised"],
  ["relative", "from ..study.private import hidden"],
  ["conditional", "if False:\n    from app.study.private import hidden"],
  ["function-local", "def hidden():\n    from app.study.private import hidden"],
  ["from-package", "from app import study"],
]) test(`Interfaces reject ${name} private component imports`, t => {
  const f = fixture(t);
  f.write("backend/app/games/consumer.py", source);
  assert.throws(f.check, /Private cross-component import/);
});

test("Interfaces reject unavailable public symbols through named and module imports", t => {
  const f = fixture(t);
  f.write("backend/app/games/consumer.py", "from app.study.interfaces import HiddenStore");
  assert.throws(f.check, /Unavailable\/private interface export/);
  f.write("backend/app/games/consumer.py", "import app.study.interfaces as api\ndef send():\n    return api.HiddenStore()\n");
  assert.throws(f.check, /Unavailable\/private interface member/);
});

for (const source of [
  "from ..private import hidden\nclass PriorityIntake: pass\n__all__ = ['PriorityIntake']\n",
  "import sqlite3\n__all__ = []\n",
  "value = open('never-created')\n__all__ = ['value']\n",
  "__all__ = ['_PrivateStore']\n",
  "from typing import *\n",
]) test(`Interfaces reject implementation/lifecycle leaks: ${source.split("\n")[0]}`, t => {
  const f = fixture(t);
  f.write("backend/app/study/interfaces/__init__.py", source);
  assert.throws(f.check, /Interface imports private|imports infrastructure|lifecycle\/I\/O|explicit non-private exports/);
});

test("Interfaces require an owner README and reject exposing an entire implementation tree", t => {
  const f = fixture(t);
  rmSync(join(f.root, "backend/app/study/interfaces/README.md"));
  assert.throws(f.check, /Interface README missing/);
  f.write("backend/app/study/README.md", "Study owner");
  f.manifest.components[1].interfaces = ["backend/app/study"];
  assert.throws(f.check, /Entire implementation directory/);
});

test("Interfaces reject unowned/unavailable imports, symlink escapes and dynamic loaders", t => {
  const f = fixture(t);
  f.write("backend/app/games/consumer.py", "from app.unowned import hidden");
  assert.throws(f.check, /Unavailable local import/);
  f.write("backend/app/games/consumer.py", "__import__('app.study.private')");
  assert.throws(f.check, /dynamic loader/);
  rmSync(join(f.root, "backend/app/games/consumer.py"));
  symlinkSync(join(f.root, "backend/app/study/private.py"), join(f.root, "backend/app/games/consumer.py"));
  assert.throws(f.check, /symlink\/escape/);
});

test("Interfaces reject component cycles and domain-to-composition dependencies", t => {
  const f = fixture(t);
  f.manifest.components[1].dependencies.push("games");
  f.write("backend/app/study/private.py", "from app.games.interfaces import GameFacts\n");
  assert.throws(f.check, /dependency cycle/);
  f.write("backend/app/study/private.py", "hidden = True\n");
  f.manifest.components[1].dependencies = [];
  f.write("backend/app/composition/wiring.py", "from app.study.private import hidden\n");
  f.manifest.components.push({ id: "composition", sources: ["backend/app/composition"], interfaces: [], modules: { "app.composition": "backend/app/composition" }, dependencies: ["study"], composition: true, tables: [] });
  assert.doesNotThrow(f.check); // Only this declared wiring owner may bind implementations.
  f.manifest.components[0].dependencies.push("composition");
  f.write("backend/app/games/consumer.py", "import app.composition.wiring\n");
  assert.throws(f.check, /Domain imports composition/);
});

test("Interfaces reject unknown dependencies, missing interfaces and zero supported source", t => {
  const f = fixture(t);
  f.manifest.components[0].dependencies.push("unmapped-owner");
  assert.throws(f.check, /Unknown component dependency/);
  f.manifest.components[0].dependencies.pop();
  f.manifest.components[0].interfaces = [];
  assert.throws(f.check, /lacks a designated public interface/);
  f.manifest.components[0].interfaces = ["backend/app/games/interfaces"];
  f.write("unsupported/interfaces/README.md", "Owner compiler proof required");
  f.write("unsupported/interfaces/lib.rs", "pub struct PublicFacts;\n");
  f.manifest.components.push({ id: "native", sources: ["unsupported"], interfaces: ["unsupported/interfaces"], modules: {}, dependencies: [], tables: [] });
  assert.throws(f.check, /Zero supported source files/);
});

for (const statement of [
  "INSERT INTO daily_queue(card_id) VALUES ('fixture')",
  'UPDATE "daily_queue" SET position = 1',
  "DELETE FROM daily_queue WHERE card_id = 'fixture'",
  "TRUNCATE TABLE daily_queue",
  'INSERT INTO"daily_queue"(card_id) VALUES (1)',
  "COPY daily_queue FROM STDIN",
  "ALTER TABLE daily_queue ADD COLUMN unsafe integer",
  "DROP TABLE IF EXISTS daily_queue",
]) test(`Interfaces reject cross-owner SQL write ${statement.split(" ")[0]}`, t => {
  const f = fixture(t);
  f.write("backend/app/games/consumer.py", `def persist(db):\n    db.execute(${JSON.stringify(statement)})\n`);
  assert.throws(f.check, /Cross-owner table write: daily_queue belongs to study/);
});

test("Interfaces permit own table writes and reject unresolved SQL targets", t => {
  const f = fixture(t);
  f.write("backend/app/games/consumer.py", "def persist(db):\n    db.execute('INSERT INTO game_requests(id) VALUES (%s)', ('fixture',))\n");
  assert.doesNotThrow(f.check);
  f.write("backend/app/games/consumer.py", "def persist(db, statement):\n    db.execute(statement)\n");
  assert.throws(f.check, /Unresolved SQL target/);
  assert.deepEqual(writtenTables("SELECT * FROM daily_queue FOR UPDATE SKIP LOCKED"), []);
  assert.deepEqual(writtenTables("INSERT INTO game_requests VALUES ('UPDATE daily_queue SET x=1') ON CONFLICT DO UPDATE SET x=1"), ["game_requests"]);
});

for (const method of ["eligible_cards", "prepare_priority", "promote"]) test(`Interfaces reject inline intake ${method}`, t => {
  const f = fixture(t);
  f.write("backend/app/study/intake.py", `def accept(store, command):\n    store.${method}(command)\n    return store.record(command)\n`);
  assert.throws(f.check, /Intake references prohibited queue work/);
});

test("Interfaces reject renamed/transitive intake promotion and method references", t => {
  const f = fixture(t);
  f.write("backend/app/study/intake.py", "from .helpers import helper as persist\ndef accept(store, command):\n    return persist(store, command)\n");
  f.write("backend/app/study/helpers.py", "def helper(store, command):\n    return store.promote(command)\n");
  assert.throws(f.check, /helpers.py.*Intake references prohibited queue work/);
  f.write("backend/app/study/intake.py", "def accept(store, command):\n    callback = store.promote\n    return callback(command)\n");
  assert.throws(f.check, /Intake references prohibited queue work/);
  f.write("backend/app/study/intake.py", "def accept(store, command):\n    return getattr(store, 'promote')(command)\n");
  assert.throws(f.check, /Intake references prohibited queue work/);
  f.write("backend/app/study/intake.py", "def accept(store, command):\n    if getattr(command, 'revision') != 1:\n        return None\n    return store.record(command)\n");
  assert.doesNotThrow(f.check);
});

test("Interfaces enforce the same public/private/table rules for TypeScript", t => {
  const f = fixture(t);
  f.write("backend/app/study/interfaces/index.ts", "export interface PriorityIntake { accept(command: unknown): unknown }\n");
  f.write("backend/app/study/private.ts", "export const hidden = true;\n");
  f.write("backend/app/games/consumer.ts", 'import { PriorityIntake } from "../study/interfaces/index.js"; export const value: PriorityIntake | null = null;');
  assert.doesNotThrow(f.check);
  f.write("backend/app/games/consumer.ts", 'import { hidden } from "../study/private.js";');
  assert.throws(f.check, /Private cross-component import/);
  f.write("backend/app/games/consumer.ts", 'import * as api from "../study/interfaces/index.js"; export const value = api.HiddenStore;');
  assert.throws(f.check, /Unavailable\/private interface member/);
  f.write("backend/app/games/consumer.ts", 'export function persist(db) { db.prepare("UPDATE daily_queue SET position = 1").run(); }');
  assert.throws(f.check, /Cross-owner table write/);
});

test("Interfaces fail closed on invalid TypeScript source", t => {
  const f = fixture(t);
  f.write("backend/app/games/consumer.ts", "export function send( {");
  assert.throws(f.check, /Invalid source syntax/);
});

test("Interfaces negative-control CLI fails an intentional violation then passes after removal", t => {
  const f = fixture(t), path = join(f.root, "owners.json");
  writeFileSync(path, JSON.stringify(f.manifest));
  const env = { ...process.env, TEMPO_COMPONENT_PYTHON: python };
  delete env.NODE_TEST_CONTEXT;
  const command = [resolve(repository, "tools/component-build/check-interface-boundaries.mjs"), path, f.root];
  function run() { return spawnSync(process.execPath, command, { env, encoding: "utf8", timeout: 15000 }); }
  f.write("backend/app/study/intake.py", "def accept(store, command):\n    return store.promote(command)\n");
  const violation = run();
  assert.equal(violation.status, 1);
  assert.match(violation.stderr, /Intake references prohibited queue work/);
  f.write("backend/app/study/intake.py", "def accept(store, command):\n    return store.record(command)\n");
  const repaired = run();
  assert.equal(repaired.status, 0, repaired.stderr);
  assert.equal(JSON.parse(repaired.stdout).components, 2);
});

test("Interfaces negative controls belong to the existing required component-checker suite", () => {
  assert.ok(testFiles(resolve(repository, "tools/component-build/tests")).includes(resolve(repository, "tools/component-build/tests/interface-boundaries.test.mjs")));
  const scripts = JSON.parse(readFileSync(resolve(repository, "package.json"), "utf8")).scripts;
  assert.equal(scripts["test:components"], "node tools/component-build/test-node.mjs tools/component-build/tests");
});

for (const [kind, source, failure] of [
  ["dynamic loader", "def accept(store, command):\n    hidden = eval\n    return hidden(command)\n", /dynamic loader/],
  ["inline SQL", "def accept(store, command):\n    store.execute_native('UPDATE daily_queue SET position=0')\n", /Intake contains direct SQL/],
]) test(`Narrow actual-source intake mode rejects ${kind}`, t => {
  const f = fixture(t);
  f.write("backend/app/study/intake.py", source);
  assert.throws(() => checkPythonIntake("backend/app/study/intake.py", ["accept"], ["eligible*", "prepare*", "promote*"], f.root, python), failure);
});
