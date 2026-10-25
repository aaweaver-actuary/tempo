import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { checkPythonSources, checkJavaScriptSource, checkGraphPreparation, graphPreparationSources } from "../check-graph-preparation.mjs";

const planner = "scripts/tempo-cutover-plan.mjs";
const lifecycle = "scripts/tempo-cutover-transition.mjs";
const readiness = "scripts/tempo-cutover-readiness.mjs";
const transfer = "database/tooling/opening_graph_transfer.py";
const fresh = "database/tooling/opening_graph_fresh_learning.py";

for (const [label, source] of [
  ["sibling policy", 'import { run } from "./tempo-cutover-transition.mjs";'],
  ["private database", 'import "../database/browser/adapters/workbench/private.mjs";'],
  ["conditional optional service", 'if (false) import("../services/game-evidence/private.mjs");'],
  ["unresolved loader", 'export async function load(path) { return import(path); }'],
  ["private deployment operation", 'import { spawnCompose } from "./tempo-deployment.mjs";'],
  ["namespace", 'import * as storage from "./tempo-deployment.mjs";'],
  ["default", 'import storage from "./tempo-deployment.mjs";'],
  ["dynamic namespace", 'const storage = await import("./tempo-deployment.mjs");'],
  ["require namespace", 'const storage = require("./tempo-deployment.mjs");'],
  ["private re-export", 'export { spawnCompose } from "./tempo-deployment.mjs";'],
  ["policy filesystem", 'import { readFileSync } from "node:fs";'],
]) test(`Graph preparation guard rejects ${label}`, () => {
  assert.throws(() => checkJavaScriptSource(planner, source), /import|export|loader|operations/i);
});

test("Graph preparation guard accepts named public operations and injected composition", () => {
  checkJavaScriptSource(planner, 'import { targetKey as identity } from "./tempo-deployment.mjs"; export const plan = input => identity(input);');
  checkJavaScriptSource(lifecycle, 'import { acquireTargetLock, atomicJson } from "./tempo-deployment.mjs"; export const run = ({effects}) => effects.importGraph();');
  checkJavaScriptSource(readiness, 'import { createHash } from "node:crypto"; export const ready = ({graphVerifier}) => graphVerifier();');
  checkJavaScriptSource(readiness, '// import private from "services/private"\nexport const facts = {source: "import(services/private)"};');
  checkPythonSources({
    [transfer]: "import re\nfrom hashlib import sha256\npattern = re.compile('graph')\ndef verify(bundle, verifier):\n    return verifier(bundle)\n",
    [fresh]: "from copy import deepcopy\nfrom urllib.parse import quote\nfrom zoneinfo import ZoneInfo\ndef plan(bundle, initialize):\n    return initialize(deepcopy(bundle))\n",
  });
});

for (const [label, source] of [
  ["concrete Learning storage", "from tempo_learning_database.adapters import publications"],
  ["sibling implementation", "def plan():\n    import opening_graph_transfer"],
  ["private Content implementation", "from tempo_content.publication import application"],
  ["private bridge implementation", "from tempo_a_bridge.cli import execution_bundle"],
  ["conditional optional service", "if False:\n    import services.game_evidence"],
  ["relative escape", "from ...backend import app"],
  ["dynamic loader", "def convert():\n    return __import__('opening_graph_transfer')"],
  ["aliased importlib", "from importlib import import_module as load\nload('opening_graph_transfer')"],
  ["SQLite storage", "import sqlite3"],
  ["network client", "from urllib.request import urlopen"],
  ["process client", "import subprocess"],
  ["ambient aliased path", "import sys as system\nsystem.path.append('services')"],
  ["ambient path import", "from sys import path as search"],
]) test(`Graph preparation Python guard rejects ${label}`, () => {
  assert.throws(() => checkPythonSources({ [fresh]: source }), /import|loader|path/i);
});

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "tempo-graph-preparation-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const write = (path, source) => {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), source);
  };
  write("scripts/tempo-deployment.mjs", "export const targetKey = value => value;");
  return { root, write };
}

test("Graph preparation selection reports absence and ignores unrelated broken optional source", t => {
  const { root, write } = fixture(t);
  write(planner, 'import { targetKey } from "./tempo-deployment.mjs";');
  write("services/game-evidence/broken.mjs", "this is not valid JavaScript");
  assert.deepEqual(checkGraphPreparation(root).checked, [planner]);
  assert.ok(checkGraphPreparation(root).absent.includes(fresh));
  assert.throws(() => checkGraphPreparation(root, [fresh]), /Missing/);
  assert.throws(() => checkGraphPreparation(root, []), /Empty/);
  assert.throws(() => checkGraphPreparation(root, ["unknown.py"]), /Unknown/);
});

test("Graph preparation guard rejects direct and transitive backward wiring", t => {
  const { root, write } = fixture(t);
  write(planner, 'import { targetKey } from "./tempo-deployment.mjs";');
  write("scripts/tempo-deployment.mjs", 'import "./tempo-cutover-plan.mjs";');
  assert.throws(() => checkGraphPreparation(root), /backward/);
  write("scripts/tempo-deployment.mjs", 'import "./bridge.mjs";');
  write("scripts/bridge.mjs", 'import "./tempo-cutover-plan.mjs";');
  assert.throws(() => checkGraphPreparation(root), /backward/);
  write("scripts/bridge.mjs", 'import "./tempo-deployment.mjs";');
  assert.throws(() => checkGraphPreparation(root), /cycle/);
  write("scripts/bridge.mjs", "export const value = 'public';");
  assert.deepEqual(checkGraphPreparation(root).checked, [planner]);
});

test("Graph preparation guard rejects source symlinks and unavailable public dependencies", t => {
  const { root, write } = fixture(t);
  symlinkSync(join(root, "scripts/tempo-deployment.mjs"), join(root, planner));
  assert.throws(() => checkGraphPreparation(root), /symlink/);
  rmSync(join(root, planner));
  write(planner, 'import { targetKey } from "./tempo-deployment.mjs";');
  rmSync(join(root, "scripts/tempo-deployment.mjs"));
  assert.throws(() => checkGraphPreparation(root), /Missing/);
});

test("Graph preparation present sources retain their exact import contracts", () => {
  const result = checkGraphPreparation();
  assert.deepEqual([...result.checked, ...result.absent].sort(), [...graphPreparationSources].sort());
  // The fixtures always execute. Absent forthcoming sources are explicitly
  // reported; this test does not claim that an absent component was qualified.
});
