import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { checkInterfaceBoundaries, writtenTables } from "../check-interface-boundaries.mjs";
import { repository } from "../check-components.mjs";
import { testFiles } from "../test-node.mjs";

const python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3";
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
