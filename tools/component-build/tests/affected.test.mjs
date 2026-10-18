import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { planAffected } from "../affected.mjs";

const file = path => ({ path, kind: "file" });
const directory = path => ({ path, kind: "directory" });
const component = (id, dependencies = []) => ({ id, status: "implemented", root: "packages/" + id,
  artifacts: [{ ecosystem: "npm", name: "@fixture/" + id }],
  dependencies: dependencies.map(name => ({ kind: "internal", ecosystem: "component", name })),
  ownerProofs: [{ on: ["artifact", "interface", "security", "runtime"], proofIds: ["component/" + id + "/unit"] }] });
const suite = id => ({ id: "component/" + id + "/unit", owner: id, kind: "node", cwd: "packages/" + id,
  command: "node --test tests/unit.test.mjs", inputs: [directory("packages/" + id + "/tests")],
  prerequisites: [], resources: ["node"], expectedScope: { mode: "whole-suite", selector: null, inventoryInputs: [file("packages/" + id + "/tests/unit.test.mjs")] } });
const rule = (id, owner, selector, role, effects = [], proofIds = []) => ({ id, owner, selector, role, effects, proofIds });
const boundary = (producer, consumer, on = ["artifact", "interface", "security", "runtime"]) => ({ id: producer + "-" + consumer, producer, consumer, on, proofIds: ["component/" + consumer + "/unit"] });
function fixture() {
  const snapshot = { identity: { revision: "base", tree: "base-tree", manifestDigest: "manifest", catalogDigest: null, selectionSemantics: "tempo-affected/1" },
    components: [component("logic"), component("view", ["logic"]), component("shell", ["view"])],
    suites: [suite("logic"), suite("view"), suite("shell")], rules: [], boundaries: [boundary("logic", "view"), boundary("view", "shell")], entries: [] };
  snapshot.suites[1].prerequisites = ["logic"];
  snapshot.suites[2].prerequisites = ["view"];
  for (const id of ["logic", "view", "shell"]) {
    snapshot.rules.push(rule(id + "-source", id, directory("packages/" + id + "/src"), "production", ["artifact"]));
    snapshot.entries.push({ path: "packages/" + id + "/src/value.mjs", kind: "file", objectId: id + "-source" }, { path: "packages/" + id + "/tests/unit.test.mjs", kind: "file", objectId: id + "-tests" });
  }
  snapshot.rules.push(rule("shared-fixture", null, file("fixtures/shared.json"), "fixture", [], ["component/logic/unit", "component/view/unit"]), rule("docs", null, file("docs/notes.md"), "documentation"));
  snapshot.entries.push({ path: "fixtures/shared.json", kind: "file", objectId: "shared-fixture" }, { path: "docs/notes.md", kind: "file", objectId: "docs" });
  return { comparison: { mode: "endpoints", requestedBase: "base", requestedHead: "head", resolvedBase: "base", resolvedHead: "head", mergeBase: null },
    base: snapshot, head: { ...structuredClone(snapshot), identity: { ...snapshot.identity, revision: "head", tree: "head-tree" } }, changes: [] };
}
const edit = path => ({ status: "M", oldPath: path, newPath: path });
const owners = plan => plan.affected.map(item => item.id).sort();
const proofs = plan => plan.selectedSuites.map(item => item.id).sort();
function blocked(input, code, path) {
  const plan = planAffected(input);
  assert.equal(plan.disposition, "blocked", JSON.stringify(plan.diagnostics));
  const diagnostic = plan.diagnostics.find(item => item.code === code && (path === undefined || item.path === path));
  assert.ok(diagnostic, JSON.stringify(plan.diagnostics));
  assert.ok(diagnostic.fix.length);
  assert.equal(plan.qualification, "not-evaluated");
  return plan;
}
function both(input, change) { change(input.base); change(input.head); }
function native(input) {
  both(input, snapshot => {
    const store = component("store"); store.ownerProofs[0].on.push("persistence"); store.ownerProofs[0].proofIds = ["component/store/native"];
    const proof = suite("store"); proof.id = "component/store/native"; proof.kind = "postgres"; proof.resources = ["python", "docker", "postgres"];
    snapshot.components.push(store); snapshot.suites.push(proof);
    snapshot.rules.push(rule("store-source", "store", directory("packages/store/src"), "production", ["artifact", "persistence"]));
    snapshot.entries.push({ path: "packages/store/src/transaction.py", kind: "file", objectId: "store-source" }, { path: "packages/store/tests/unit.test.mjs", kind: "file", objectId: "store-tests" });
  });
}

test("A202-production-chain selects owner, consumers and separate prerequisites", () => {
  const input = fixture(); input.changes = [edit("packages/logic/src/value.mjs")];
  const original = structuredClone(input), plan = planAffected(input);
  assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(owners(plan), ["logic", "shell", "view"]);
  assert.deepEqual(proofs(plan), ["component/logic/unit", "component/shell/unit", "component/view/unit"]);
  assert.deepEqual(plan.prerequisites, ["logic", "view"]);
  assert.deepEqual(plan.requiredResources, ["node"]);
  assert.ok(plan.reasons.some(reason => reason.code === "REVERSE_DEPENDENCY"));
  assert.deepEqual(input, original);
});
test("A202-interface-boundary expands an explicit native interaction", () => {
  const input = fixture(); native(input);
  both(input, snapshot => {
    snapshot.rules.find(item => item.id === "logic-source").role = "interface";
    snapshot.boundaries.push(boundary("logic", "store", ["interface"]));
    snapshot.boundaries.at(-1).proofIds = ["component/store/native"];
  });
  input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = planAffected(input);
  assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.ok(proofs(plan).includes("component/store/native"));
  assert.ok(owners(plan).includes("store"));
  assert.deepEqual(plan.requiredResources, ["docker", "node", "postgres", "python"]);
});
test("A202-persistence-native requires real owning PostgreSQL metadata", () => {
  const input = fixture(); native(input); input.changes = [edit("packages/store/src/transaction.py")];
  const plan = planAffected(input);
  assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(owners(plan), ["store"]); assert.deepEqual(proofs(plan), ["component/store/native"]);
  both(input, snapshot => { snapshot.suites.at(-1).resources = ["python"]; snapshot.suites.at(-1).kind = "python"; });
  blocked(input, "PROOF_SCOPE_GAP");
});
test("A202-test-only does not cascade production impact", () => {
  const input = fixture(); input.changes = [edit("packages/logic/tests/unit.test.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(owners(plan), []); assert.deepEqual(proofs(plan), ["component/logic/unit"]);
});
test("A202-shared-fixture selects each actual reader once", () => {
  const input = fixture(); input.changes = [edit("fixtures/shared.json")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(owners(plan), []); assert.deepEqual(proofs(plan), ["component/logic/unit", "component/view/unit"]);
});
for (const [name, destination, expected] of [
  ["within-owner-rename", "packages/logic/src/renamed.mjs", ["logic", "shell", "view"]],
  ["cross-owner-move", "packages/view/src/moved.mjs", ["logic", "shell", "view"]],
]) test("A202-" + name + " retains old and new endpoint ownership", () => {
  const input = fixture(), source = "packages/logic/src/value.mjs";
  input.head.entries.find(entry => entry.path === source).path = destination;
  input.changes = [{ status: "R", oldPath: source, newPath: destination }];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(owners(plan), expected); assert.ok(plan.paths.some(path => path.snapshot === "base" && path.path === source));
});
test("A202-copy-destination does not mark the unchanged producer changed", () => {
  const input = fixture(); input.head.entries.push({ path: "packages/view/src/copy.mjs", kind: "file", objectId: "logic-source" });
  input.changes = [{ status: "C", oldPath: "packages/logic/src/value.mjs", newPath: "packages/view/src/copy.mjs" }];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(owners(plan), ["shell", "view"]);
});
test("A202-deleted-source resolves production ownership in base", () => {
  const input = fixture(); input.head.entries = input.head.entries.filter(entry => entry.path !== "packages/logic/src/value.mjs");
  input.changes = [{ status: "D", oldPath: "packages/logic/src/value.mjs" }];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics)); assert.deepEqual(owners(plan), ["logic", "shell", "view"]);
});
test("A202-deleted-required-test cannot erase selected evidence", () => {
  const input = fixture(), path = "packages/logic/tests/unit.test.mjs";
  input.head.entries = input.head.entries.filter(entry => entry.path !== path); input.changes = [{ status: "D", oldPath: path }];
  blocked(input, "MISSING_REQUIRED_PROOF", path);
});
test("A202-renamed-test-replacement keeps durable identity and changes scope", () => {
  const input = fixture(), path = "packages/logic/tests/unit.test.mjs", replacement = "packages/logic/tests/renamed.test.mjs";
  input.head.entries.find(entry => entry.path === path).path = replacement;
  input.head.suites[0].expectedScope.inventoryInputs = [file(replacement)];
  input.head.suites[0].command = "node --test tests/renamed.test.mjs";
  input.changes = [{ status: "R", oldPath: path, newPath: replacement }];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(proofs(plan), ["component/logic/unit"]); assert.deepEqual(plan.selectedSuites[0].baseObligations, ["component/logic/unit"]);
});
test("A202-removed-edge preserves base consumer obligation", () => {
  const input = fixture(); input.head.components[1].dependencies = []; input.head.boundaries = input.head.boundaries.filter(item => item.producer !== "logic");
  input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = planAffected(input); assert.ok(proofs(plan).includes("component/view/unit"));
  assert.ok(plan.reasons.some(reason => reason.code === "REVERSE_DEPENDENCY" && reason.snapshot === "base" && reason.to === "view"));
});
test("A202-removed-component and ownership cannot disappear from the plan", () => {
  const input = fixture(); input.head.components = input.head.components.filter(item => item.id !== "logic");
  input.head.components[0].dependencies = []; input.head.rules = input.head.rules.filter(item => item.owner !== "logic");
  input.head.boundaries = input.head.boundaries.filter(item => item.producer !== "logic");
  blocked(input, "REMOVED_PROOF_UNREPLACED");
});
test("A202-removed-ownership retains source obligations", () => {
  const input = fixture(); input.head.rules = input.head.rules.filter(item => item.id !== "logic-source"); input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = blocked(input, "UNKNOWN_OWNER", "packages/logic/src/value.mjs"); assert.ok(owners(plan).includes("logic"));
});
test("A202-unowned-source reports candidate root without inventing an owner", () => {
  const input = fixture(), path = "packages/logic/new.mjs"; input.head.entries.push({ path, kind: "file", objectId: "new" }); input.changes = [{ status: "A", newPath: path }];
  const plan = blocked(input, "UNKNOWN_OWNER", path); assert.deepEqual(plan.diagnostics.find(item => item.code === "UNKNOWN_OWNER").candidates, ["logic"]);
});
test("A202-overlap rejects conflicting production owners", () => {
  const input = fixture(); both(input, snapshot => snapshot.rules.push(rule("conflict", "view", directory("packages/logic/src"), "production", ["artifact"])));
  blocked(input, "OVERLAPPING_PRODUCTION", "packages/logic/src/value.mjs");
});
test("A202-nested-roots honor explicit parent exclusions", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules[0].selector = { path: "packages", kind: "directory", exclude: [directory("packages/view"), directory("packages/shell")] };
  });
  input.changes = [edit("packages/view/src/value.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(owners(plan), ["shell", "view"]);
});
test("A202-missing-internal is not mistaken for an external package", () => {
  const input = fixture(); both(input, snapshot => snapshot.components[1].dependencies.push({ kind: "internal", ecosystem: "component", name: "missing" }));
  blocked(input, "MISSING_INTERNAL_TARGET", "missing");
});
test("A202-planned-owner cannot produce a successful empty selection", () => {
  const input = fixture(); both(input, snapshot => { snapshot.components[0].status = "planned"; snapshot.components[0].ownerProofs = []; }); input.changes = [edit("packages/logic/src/value.mjs")];
  blocked(input, "PLANNED_TARGET");
});
test("A202-false-implemented selects its named planned fixture, not the first component", () => {
  const input = fixture(); both(input, snapshot => snapshot.components.push({ ...component("future"), status: "implemented", artifacts: [], ownerProofs: [] }));
  blocked(input, "INVALID_MANIFEST", "packages/future");
});
for (const path of ["/tmp/x", "../x", "a/../b", "a//b", "a\\b", "a\0b", "C:/escape", "a/./b", ""]) test("A202-path-validation rejects " + JSON.stringify(path), () => {
  const input = fixture(); input.changes = [{ status: "A", newPath: path }]; blocked(input, path ? "INVALID_PATH" : "INVALID_CHANGE");
});
for (const changes of [[{ status: "R", newPath: "x" }], [{ status: "X", oldPath: "x" }], [edit("x"), { status: "D", oldPath: "x" }], [{ status: "M", oldPath: "a", newPath: "b" }]]) test("A202-record-validation " + JSON.stringify(changes), () => {
  const input = fixture(); input.changes = changes; blocked(input, "INVALID_CHANGE");
});
test("A202-unavailable-base and omitted comparisons block without fallback", () => {
  const input = fixture(); delete input.base; blocked(input, "BASE_UNAVAILABLE");
  delete input.comparison; blocked(input, "INVALID_COMPARISON");
  blocked({}, "INVALID_COMPARISON");
});
test("A202-explicit-merge-base must match its effective snapshot", () => {
  const input = fixture(); input.comparison.mode = "merge-base"; input.comparison.mergeBase = "base";
  assert.equal(planAffected(input).disposition, "no-runtime"); input.comparison.mergeBase = "different"; blocked(input, "INVALID_COMPARISON");
});
test("A202-diamond retains both causal edges and selects each suite once", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.components[2].dependencies.push({ kind: "internal", ecosystem: "component", name: "logic" }); snapshot.boundaries.push(boundary("logic", "shell"));
  }); input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.equal(proofs(plan).length, 3); assert.deepEqual(plan.cycles, []);
  assert.ok(plan.reasons.some(reason => reason.from === "logic" && reason.to === "shell")); assert.ok(plan.reasons.some(reason => reason.from === "view" && reason.to === "shell"));
});
test("A202-cycle has finite reasons and explicit membership", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.components[0].dependencies.push({ kind: "internal", ecosystem: "component", name: "view" }); snapshot.boundaries.push(boundary("view", "logic"));
  }); input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.equal(plan.cycles.length, 2); assert.deepEqual(plan.cycles[0].components, ["logic", "view"]); assert.ok(plan.reasons.length < 40);
});
test("A202-self-edge is a mapping error", () => {
  const input = fixture(); both(input, snapshot => snapshot.components[0].dependencies.push({ kind: "internal", ecosystem: "component", name: "logic" })); blocked(input, "SELF_DEPENDENCY");
});
test("A202-python-alias preserves typed distribution normalization", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.components[0].artifacts = [{ ecosystem: "python", name: "tempo-logic" }]; snapshot.components[1].dependencies = [{ kind: "internal", ecosystem: "python", name: "Tempo_Logic" }];
  }); input.changes = [edit("packages/logic/src/value.mjs")]; assert.equal(planAffected(input).disposition, "executable");
});
test("A202-ambiguous-alias rejects duplicate normalized producers", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.components[0].artifacts = [{ ecosystem: "python", name: "tempo-logic" }]; snapshot.components[1].artifacts = [{ ecosystem: "python", name: "Tempo_Logic" }];
  }); blocked(input, "AMBIGUOUS_ALIAS", "python:tempo-logic");
});
test("A202-external-dependency never fabricates an internal owner", () => {
  const input = fixture(); both(input, snapshot => snapshot.components[0].dependencies.push({ kind: "external", ecosystem: "npm", name: "zod" })); input.changes = [edit("packages/logic/src/value.mjs")]; assert.equal(planAffected(input).disposition, "executable");
});
test("A202-order-dedup output survives shuffled manifests, changes and matches", () => {
  const input = fixture(); input.changes = [edit("fixtures/shared.json"), edit("packages/logic/src/value.mjs")];
  const expected = planAffected(input), shuffled = structuredClone(input);
  shuffled.changes.reverse(); shuffled.changes.push(structuredClone(shuffled.changes[0]));
  both(shuffled, snapshot => { for (const field of ["components", "suites", "rules", "boundaries", "entries"]) snapshot[field].reverse(); for (const item of snapshot.components) item.ownerProofs[0].on.reverse(); });
  assert.deepEqual(planAffected(shuffled), expected);
});
test("A202-doc-only requires explicit unconsumed classification", () => {
  const input = fixture(); input.changes = [edit("docs/notes.md")]; assert.equal(planAffected(input).disposition, "no-runtime");
  both(input, snapshot => { snapshot.suites[0].inputs.push(file("docs/notes.md")); });
  const consumed = planAffected(input); assert.equal(consumed.disposition, "executable"); assert.deepEqual(proofs(consumed), ["component/logic/unit"]);
});
test("A202-consumed-markdown cannot hide missing build proof", () => {
  const input = fixture(); both(input, snapshot => snapshot.rules.push(rule("build-doc", "logic", file("docs/notes.md"), "artifact"))); input.changes = [edit("docs/notes.md")]; blocked(input, "UNCOVERED_REQUIRED_INPUT");
});
test("A202-shared-toolchain affects only its declared readers", () => {
  const input = fixture(); both(input, snapshot => { snapshot.rules.push(rule("compiler", null, file("config/compiler.json"), "toolchain", [], ["component/logic/unit", "component/view/unit"])); snapshot.entries.push({ path: "config/compiler.json", kind: "file", objectId: "compiler" }); });
  input.changes = [edit("config/compiler.json")]; const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(proofs(plan), ["component/logic/unit", "component/view/unit"]);
});
test("A202-metadata-only command change selects proof and evidence material", () => {
  const input = fixture(); input.head.suites[1].command = "node --test tests/unit.test.mjs --changed";
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(proofs(plan), ["component/view/unit"]);
  assert.equal(plan.evidenceRequirements[0].command, input.head.suites[1].command); assert.equal(plan.evidenceRequirements[0].reuse, "not-evaluated");
});
test("A202-removed-proof requires a unique reviewed durable replacement", () => {
  const input = fixture();
  // This pure proof replacement covers artifacts; native persistence is tested separately.
  both(input, snapshot => { snapshot.components[0].ownerProofs[0].on = ["artifact"]; });
  input.head.suites[0].id = "component/logic/replacement"; input.head.components[0].ownerProofs[0].proofIds = ["component/logic/replacement"];
  input.head.rules.find(item => item.id === "shared-fixture").proofIds[0] = "component/logic/replacement";
  blocked(input, "REMOVED_PROOF_UNREPLACED"); input.head.suites[0].replaces = ["component/logic/unit"];
  assert.equal(planAffected(input).disposition, "executable");
});
test("A202-unmapped-boundary is a non-success, never blanket FULL", () => {
  const input = fixture(); both(input, snapshot => { snapshot.boundaries = []; }); input.changes = [edit("packages/logic/src/value.mjs")]; blocked(input, "UNMAPPED_BOUNDARY");
});
test("A202-symlink and gitlink inputs are not followed", () => {
  for (const kind of ["symlink", "gitlink"]) {
    const input = fixture(); input.head.entries[0].kind = kind; input.changes = [edit(input.head.entries[0].path)]; blocked(input, "UNSUPPORTED_INPUT_KIND");
  }
});
test("A202-segment-case-unicode paths use exact repository strings", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.entries.push({ path: "packages/logic/src/space ünicode.mjs", kind: "file", objectId: "unicode" });
  }); input.changes = [edit("packages/logic/src/space ünicode.mjs")]; assert.equal(planAffected(input).disposition, "executable");
  input.changes = [{ status: "A", newPath: "packages/logic/src-other/x.mjs" }]; input.head.entries.push({ path: input.changes[0].newPath, kind: "file", objectId: "prefix" }); blocked(input, "UNKNOWN_OWNER");
});
test("A202-no-environment module has no execution, filesystem or product imports", () => {
  const source = readFileSync(new URL("../affected.mjs", import.meta.url), "utf8");
  assert.deepEqual([...source.matchAll(/^import .* from "([^"]+)"/gm)].map(match => match[1]), ["node:crypto"]);
  assert.doesNotMatch(source, /\b(?:process|spawnSync|execFile|readFileSync|fetch)\s*[.(]/);
  assert.equal(planAffected(fixture()).disposition, "no-runtime");
});
test("A202-linear-work indexes once and keeps chain reasons bounded", () => {
  for (const size of [20, 100, 300]) {
    const input = fixture(); both(input, snapshot => {
      snapshot.components = []; snapshot.suites = []; snapshot.rules = []; snapshot.boundaries = []; snapshot.entries = [];
      for (let index = 0; index < size; index++) {
        const id = "c" + index; snapshot.components.push(component(id, index ? ["c" + (index - 1)] : [])); snapshot.suites.push(suite(id));
        snapshot.rules.push(rule(id, id, directory("packages/" + id + "/src"), "production", ["artifact"]));
        snapshot.entries.push({ path: "packages/" + id + "/src/value.mjs", kind: "file", objectId: id }, { path: "packages/" + id + "/tests/unit.test.mjs", kind: "file", objectId: id + "-test" });
        if (index) snapshot.boundaries.push(boundary("c" + (index - 1), id));
      }
    }); input.changes = [edit("packages/c0/src/value.mjs")];
    const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.equal(plan.selectedSuites.length, size);
    assert.equal(plan.statistics.indexedEntries, size * 4); assert.ok(plan.statistics.traversedEdges <= size * 2); assert.ok(plan.reasons.length <= size * 10);
  }
});

test("A202-sensitivity-is-not-ownership rejects production hidden by a broad suite input", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules = snapshot.rules.filter(item => item.id !== "logic-source");
    snapshot.suites[0].inputs.push(directory("packages/logic/src"));
  }); input.changes = [edit("packages/logic/src/value.mjs")]; blocked(input, "UNKNOWN_OWNER", "packages/logic/src/value.mjs");
});
test("A202-empty-root-overlap rejects conflicting declarations before files exist", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules.push(rule("empty-one", "logic", directory("uncreated/source"), "production", ["artifact"]), rule("empty-two", "view", directory("uncreated/source"), "production", ["artifact"]));
  }); blocked(input, "OVERLAPPING_PRODUCTION", "uncreated/source");
});
test("A202-root-rename retains component and suite identities", () => {
  const input = fixture();
  input.head.components[0].root = "packages/logic-v2";
  input.head.rules[0].selector.path = "packages/logic-v2/src";
  input.head.suites[0].cwd = "packages/logic-v2";
  input.head.suites[0].inputs[0].path = "packages/logic-v2/tests";
  input.head.suites[0].expectedScope.inventoryInputs[0].path = "packages/logic-v2/tests/unit.test.mjs";
  for (const entry of input.head.entries.filter(entry => entry.path.startsWith("packages/logic/"))) {
    const oldPath = entry.path; entry.path = entry.path.replace("packages/logic/", "packages/logic-v2/"); input.changes.push({ status: "R", oldPath, newPath: entry.path });
  }
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics)); assert.deepEqual(owners(plan), ["logic", "shell", "view"]);
});
test("A202-legacy-suite-identity retains its full normalized tuple", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.suites[0].id = undefined;
    const tuple = { command: "node --test tests/unit.test.mjs", cwd: "packages/logic", inputs: [{ kind: "directory", path: "packages/logic/tests" }], kind: "node", owner: "logic" };
    snapshot.components[0].ownerProofs[0].proofIds = ["legacy/logic/" + createHash("sha256").update(JSON.stringify(tuple)).digest("hex")];
    snapshot.rules.find(item => item.id === "shared-fixture").proofIds[0] = snapshot.components[0].ownerProofs[0].proofIds[0];
  }); input.changes = [edit("packages/logic/tests/unit.test.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.match(plan.selectedSuites[0].id, /^legacy\/logic\/[a-f0-9]{64}$/);
  assert.equal(plan.selectedSuites[0].legacyIdentity.owner, "logic");
  const changed = structuredClone(input); changed.head.suites[0].command += " --changed";
  const tuple = { command: changed.head.suites[0].command, cwd: "packages/logic", inputs: [{ kind: "directory", path: "packages/logic/tests" }], kind: "node", owner: "logic" };
  changed.head.components[0].ownerProofs[0].proofIds = ["legacy/logic/" + createHash("sha256").update(JSON.stringify(tuple)).digest("hex")];
  changed.head.rules.find(item => item.id === "shared-fixture").proofIds[0] = changed.head.components[0].ownerProofs[0].proofIds[0];
  blocked(changed, "REMOVED_PROOF_UNREPLACED");
});
test("A202-malformed-projections return diagnostics rather than interpreter errors", () => {
  for (const mutate of [
    input => { input.head.components[0].ownerProofs = [null]; },
    input => { input.head.suites = [null]; },
    input => { input.head.rules = [null]; },
    input => { input.head.boundaries = [null]; },
    input => { input.head.entries = [null]; },
    input => { input.head.components[0].dependencies = ["@fixture/logic"]; },
  ]) {
    const input = fixture(); mutate(input); const plan = planAffected(input); assert.equal(plan.disposition, "blocked"); assert.ok(plan.diagnostics[0].fix);
  }
});
test("A202-unreported-entry-change cannot become an empty passing plan", () => {
  const input = fixture(); input.head.entries[0].objectId = "changed"; blocked(input, "UNCOVERED_REQUIRED_INPUT", input.head.entries[0].path);
});
test("A202-selected-cwd must exist in the head snapshot", () => {
  const input = fixture(); input.head.suites[0].cwd = "missing"; blocked(input, "MISSING_REQUIRED_PROOF", "missing");
});
test("A202-resource-mapping is explicit even when suite kind is node", () => {
  const input = fixture(); input.head.suites[0].resources = []; blocked(input, "RESOURCE_MAPPING_REQUIRED");
});
test("A202-evidence-material includes dependency source, command, fixture and exact scope", () => {
  const input = fixture(); input.changes = [edit("packages/view/src/value.mjs")];
  const plan = planAffected(input), requirement = plan.evidenceRequirements.find(item => item.suiteId === "component/view/unit");
  assert.deepEqual(requirement.dependencyComponents, ["logic"]);
  assert.ok(plan.dependencyInputs.find(item => item.component === "logic").sourceInputs.some(entry => entry.path === "packages/logic/src/value.mjs"));
  assert.equal(requirement.expectedScope.mode, "whole-suite"); assert.equal(requirement.observedResult, null);
  const json = JSON.stringify(plan); assert.deepEqual(JSON.parse(json), plan);
  const unrelated = structuredClone(input); unrelated.head.identity.revision = unrelated.comparison.resolvedHead = "unrelated-main";
  assert.deepEqual(planAffected(unrelated).evidenceRequirements, plan.evidenceRequirements);
});
test("A202-directory-selectors do not match a literal file at the same path", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules.push(rule("literal-directory", null, directory("literal"), "fixture", [], ["component/logic/unit"]));
    snapshot.entries.push({ path: "literal", kind: "file", objectId: "literal" });
  }); input.changes = [edit("literal")]; blocked(input, "UNKNOWN_OWNER", "literal");
});

test("A202-ui-rendering stays within declared local browser proofs", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.components = snapshot.components.slice(0, 1); snapshot.suites = snapshot.suites.slice(0, 1); snapshot.boundaries = [];
    snapshot.rules = snapshot.rules.filter(item => item.owner === "logic");
    const browser = structuredClone(snapshot.suites[0]); browser.id = "component/logic/browser"; browser.command = "node browser-fixture.mjs"; browser.resources = ["node", "browser"];
    snapshot.suites.push(browser); snapshot.components[0].ownerProofs[0].proofIds.push(browser.id);
  }); input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(plan.requiredResources, ["browser", "node"]); assert.deepEqual(owners(plan), ["logic"]);
});
test("A202-worker-private inputs do not acquire unrelated model or Study proofs", () => {
  const input = fixture(); native(input); both(input, snapshot => {
    // Independent worker fixture: neither store nor shell consumes its artifact.
    snapshot.components[1].dependencies = []; snapshot.boundaries = snapshot.boundaries.filter(item => item.producer !== "logic");
    snapshot.rules.push(rule("engine-descriptor", "logic", file("workers/engine/descriptor.json"), "artifact", ["artifact"]));
    snapshot.entries.push({ path: "workers/engine/descriptor.json", kind: "file", objectId: "engine" });
  }); input.changes = [edit("workers/engine/descriptor.json")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(owners(plan), ["logic"]); assert.deepEqual(plan.requiredResources, ["node"]);
});
test("A202-worker-protocol change expands to the actual contract consumer", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules.push(rule("worker-wire", "logic", file("workers/engine/wire.mjs"), "interface", ["interface"]));
    snapshot.entries.push({ path: "workers/engine/wire.mjs", kind: "file", objectId: "wire" });
  }); input.changes = [edit("workers/engine/wire.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(owners(plan), ["logic", "shell", "view"]); assert.equal(plan.selectedBoundaries.length, 2);
});
test("A202-local-lockfile follows only adopting artifact consumers", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules.push(rule("view-lock", "view", file("packages/view/package-lock.json"), "toolchain", ["artifact"]));
    snapshot.entries.push({ path: "packages/view/package-lock.json", kind: "file", objectId: "lock" });
  }); input.changes = [edit("packages/view/package-lock.json")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(owners(plan), ["shell", "view"]); assert.deepEqual(plan.requiredResources, ["node"]);
});
test("A202-root-lockfile selects declared Node users without changing Python owners", () => {
  const input = fixture(); native(input); both(input, snapshot => {
    snapshot.rules.push(rule("root-node-lock", null, file("package-lock.json"), "toolchain", [], ["component/view/unit"]));
    snapshot.entries.push({ path: "package-lock.json", kind: "file", objectId: "root-lock" });
  }); input.changes = [edit("package-lock.json")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(proofs(plan), ["component/view/unit"]); assert.deepEqual(owners(plan), []); assert.deepEqual(plan.requiredResources, ["node"]);
});
test("A202-manifest-only requires its explicitly declared tooling reader", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.rules.push(rule("manifest-reader", null, file("components.json"), "runner", [], ["component/logic/unit"]));
    snapshot.entries.push({ path: "components.json", kind: "file", objectId: "manifest" });
  }); input.changes = [edit("components.json")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.deepEqual(proofs(plan), ["component/logic/unit"]);
});
test("A202-duplicate-suite-matches collapse without losing independent causes", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.suites.push(structuredClone(snapshot.suites[0])); snapshot.suites[0].inputs.push(directory("packages/logic/tests"));
    snapshot.rules.push(rule("extra-fixture-reader", null, file("fixtures/shared.json"), "fixture", [], ["component/logic/unit"]));
  }); input.changes = [edit("fixtures/shared.json")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable"); assert.equal(proofs(plan).length, 2);
  assert.ok(plan.reasons.some(reason => reason.ruleId === "extra-fixture-reader")); assert.ok(plan.reasons.some(reason => reason.ruleId === "shared-fixture"));
});
test("A202-evidence-scope does not promote a browser subset to a whole-suite claim", () => {
  const input = fixture(); both(input, snapshot => {
    snapshot.suites[0].expectedScope.mode = "subset"; snapshot.suites[0].expectedScope.selector = "desktop";
  }); input.changes = [edit("packages/logic/tests/unit.test.mjs")];
  const requirement = planAffected(input).evidenceRequirements[0];
  assert.equal(requirement.expectedScope.mode, "subset"); assert.equal(requirement.expectedScope.selector, "desktop");
  assert.equal(requirement.observedResult, null); assert.equal(requirement.requiresNonzeroPassingCases, true);
});

test("A202-overlap-diagnostics are stable when conflicting declarations are shuffled", () => {
  const input = fixture(); both(input, snapshot => snapshot.rules.push(rule("nested-conflict", "view", directory("packages/logic/src/nested"), "production", ["artifact"])));
  const expected = planAffected(input), reversed = structuredClone(input); both(reversed, snapshot => snapshot.rules.reverse());
  assert.equal(expected.disposition, "blocked"); assert.deepEqual(planAffected(reversed), expected);
});
test("A202-required-dependency-symlink blocks even when the changed proof input is ordinary", () => {
  const input = fixture(); both(input, snapshot => { snapshot.entries.find(entry => entry.path === "packages/logic/src/value.mjs").kind = "symlink"; });
  input.changes = [edit("packages/view/tests/unit.test.mjs")]; blocked(input, "UNSUPPORTED_INPUT_KIND", "packages/logic/src/value.mjs");
});

test("A202-boundary-evidence names endpoint sources without inventing execution prerequisites", () => {
  const input = fixture(); both(input, snapshot => {
    const exchange = { ...suite("view"), id: "shared/exchange", owner: "contracts", prerequisites: [] };
    snapshot.suites.push(exchange); snapshot.boundaries[0].proofIds = [exchange.id];
  }); input.changes = [edit("packages/logic/src/value.mjs")];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  const requirement = plan.evidenceRequirements.find(item => item.suiteId === "shared/exchange");
  assert.deepEqual(requirement.dependencyComponents, ["logic", "view"]);
  assert.ok(plan.dependencyInputs.find(item => item.component === "logic").sourceInputs.length);
  assert.deepEqual(plan.selectedSuites.find(item => item.id === "shared/exchange").prerequisites, []);
});
test("A202-runtime-input changes remain explicit and require fresh scope evidence", () => {
  const input = fixture(); both(input, snapshot => { snapshot.suites[0].runtime = { node: "26.10.0", platform: "darwin-arm64" }; });
  input.changes = [edit("packages/logic/tests/unit.test.mjs")];
  const original = planAffected(input), changed = structuredClone(input); changed.head.suites[0].runtime.node = "next-runtime";
  const result = planAffected(changed); assert.equal(result.disposition, "executable");
  assert.notDeepEqual(result.evidenceRequirements, original.evidenceRequirements);
  assert.equal(result.evidenceRequirements[0].runtimeRequirements.node, "next-runtime");
  assert.equal(result.evidenceRequirements[0].requiresRuntimeFingerprint, true); assert.equal(result.evidenceRequirements[0].observedResult, null);
});

function splitNativeProofs(input) {
  native(input);
  both(input, snapshot => {
    const unit = { ...suite("store"), id: "component/store/unit" };
    snapshot.suites.push(unit);
    snapshot.components.find(item => item.id === "store").ownerProofs = [
      { on: ["artifact"], proofIds: [unit.id] },
      { on: ["persistence"], proofIds: ["component/store/native"] },
    ];
  });
}
test("A202-removed-effect-trigger retains native base obligation and blocks incomplete head coverage", () => {
  const input = fixture(); splitNativeProofs(input);
  input.head.components.find(item => item.id === "store").ownerProofs.pop();
  const plan = blocked(input, "PROOF_SCOPE_GAP");
  assert.ok(plan.diagnostics.some(item => item.code === "MISSING_REQUIRED_PROOF" && item.snapshot === "head" && item.reason.includes("persistence")));
  assert.deepEqual(proofs(plan), ["component/store/native", "component/store/unit"]);
  assert.deepEqual(plan.selectedSuites.find(item => item.id === "component/store/native").baseObligations, ["component/store/native"]);
  assert.deepEqual(plan.affected.find(item => item.id === "store").effects, ["artifact", "persistence"]);
});
test("A202-effect-trigger-replacement retains base proof and accepts explicit native head replacement", () => {
  const input = fixture(); splitNativeProofs(input);
  const proof = input.head.suites.find(item => item.id === "component/store/native");
  proof.id = "component/store/native-v2"; proof.replaces = ["component/store/native"];
  input.head.components.find(item => item.id === "store").ownerProofs[1].proofIds = [proof.id];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(plan.selectedSuites.find(item => item.id === proof.id).baseObligations, ["component/store/native"]);
  assert.deepEqual(plan.affected.find(item => item.id === "store").effects, ["artifact", "persistence"]);
  proof.kind = "node"; proof.resources = ["node"]; blocked(input, "PROOF_SCOPE_GAP");
});
test("A202-changed-interface-trigger retains effect-specific consumer boundary", () => {
  const input = fixture(); native(input);
  both(input, snapshot => {
    snapshot.components[0].ownerProofs = [{ on: ["artifact"], proofIds: ["component/logic/unit"] }, { on: ["interface"], proofIds: ["component/logic/unit"] }];
    snapshot.boundaries.push({ ...boundary("logic", "store", ["interface"]), proofIds: ["component/store/native"] });
  });
  input.head.components[0].ownerProofs[1].proofIds = ["component/view/unit"];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.ok(proofs(plan).includes("component/store/native"));
  assert.ok(plan.selectedBoundaries.some(item => item.id === "boundary/logic-store"));
  assert.deepEqual(plan.affected.find(item => item.id === "logic").effects, ["artifact", "interface"]);
});
test("A202-unchanged-effect-triggers do not turn unrelated component metadata into persistence changes", () => {
  const input = fixture(); splitNativeProofs(input);
  input.head.components.find(item => item.id === "store").root = "packages/store-v2";
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.ok(plan.affected.every(item => JSON.stringify(item.effects) === '["artifact"]'));
  assert.deepEqual(proofs(plan), ["component/store/unit"]); assert.deepEqual(plan.requiredResources, ["node"]);
});

test("A202-inventory-only-edit selects the collection owner outside suite sensitivity", () => {
  const input = fixture(), path = "inventories/logic.json";
  both(input, snapshot => {
    snapshot.suites[0].expectedScope.inventoryInputs = [file(path)];
    snapshot.entries.push({ path, kind: "file", objectId: "inventory" });
  });
  input.head.entries.find(item => item.path === path).objectId = "inventory-v2"; input.changes = [edit(path)];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(proofs(plan), ["component/logic/unit"]); assert.deepEqual(owners(plan), []);
  assert.deepEqual(plan.reasons.filter(item => item.code === "INVENTORY_INPUT").map(item => item.snapshot).sort(), ["base", "head"]);
  assert.ok(plan.evidenceRequirements[0].sourceInputs.some(item => item.path === path && item.objectId === "inventory-v2"));
});
test("A202-deleted-inventory-only-test reaches head required-input validation", () => {
  const input = fixture(), path = "packages/logic/tests/unit.test.mjs";
  both(input, snapshot => { snapshot.suites[0].inputs = [file("packages/logic/src/value.mjs")]; });
  input.head.entries = input.head.entries.filter(item => item.path !== path); input.changes = [{ status: "D", oldPath: path }];
  const plan = blocked(input, "MISSING_REQUIRED_PROOF", path);
  assert.deepEqual(proofs(plan), ["component/logic/unit"]);
  assert.deepEqual(plan.selectedSuites[0].baseObligations, ["component/logic/unit"]);
});
test("A202-renamed-inventory-only-test accepts explicit current collection input", () => {
  const input = fixture(), path = "packages/logic/tests/unit.test.mjs", replacement = "inventories/logic.test.mjs";
  both(input, snapshot => { snapshot.suites[0].inputs = [file("packages/logic/src/value.mjs")]; });
  input.head.entries.find(item => item.path === path).path = replacement;
  input.head.suites[0].expectedScope.inventoryInputs = [file(replacement)];
  input.changes = [{ status: "R", oldPath: path, newPath: replacement }];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(proofs(plan), ["component/logic/unit"]);
  assert.ok(plan.reasons.some(item => item.code === "INVENTORY_INPUT" && item.snapshot === "base" && item.path === path));
});
test("A202-shared-inventory selects all readers even under a test rule without proof IDs", () => {
  const input = fixture(), path = "inventories/shared.json";
  both(input, snapshot => {
    for (const proof of snapshot.suites.slice(0, 2)) proof.expectedScope.inventoryInputs = [file(path)];
    snapshot.rules.push(rule("inventory-test", null, file(path), "test"));
    snapshot.entries.push({ path, kind: "file", objectId: "inventory" });
  }); input.changes = [edit(path)];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(proofs(plan), ["component/logic/unit", "component/view/unit"]); assert.deepEqual(owners(plan), []);
});
test("A202-inventory-exclusions do not select collection owners for documented siblings", () => {
  const input = fixture(), path = "inventories/notes.md";
  both(input, snapshot => {
    snapshot.suites[0].expectedScope.inventoryInputs = [{ ...directory("inventories"), exclude: [file(path)] }];
    snapshot.rules.push(rule("inventory-notes", null, file(path), "documentation"));
    snapshot.entries.push({ path, kind: "file", objectId: "notes" }, { path: "inventories/logic.json", kind: "file", objectId: "inventory" });
  }); input.changes = [edit(path)];
  const plan = planAffected(input); assert.equal(plan.disposition, "no-runtime"); assert.deepEqual(proofs(plan), []);
});
test("A202-inventory-copy-source remains provenance without selecting the unchanged collection", () => {
  const input = fixture(), path = "packages/logic/tests/unit.test.mjs";
  both(input, snapshot => {
    snapshot.suites[0].inputs = [file("packages/logic/src/value.mjs")];
    snapshot.rules.push(rule("copied-notes", null, file("docs/copy.md"), "documentation"));
  });
  input.head.entries.push({ path: "docs/copy.md", kind: "file", objectId: "logic-tests" });
  input.changes = [{ status: "C", oldPath: path, newPath: "docs/copy.md" }];
  const plan = planAffected(input); assert.equal(plan.disposition, "no-runtime"); assert.deepEqual(proofs(plan), []);
  assert.ok(plan.paths.some(item => item.path === path && item.provenanceOnly));
});

for (const operation of ["reassignment", "removal"]) test("A202-implicit-production-metadata " + operation + " retains both endpoint owner and consumer obligations", () => {
  const input = fixture(); both(input, snapshot => { snapshot.rules[0].effects = []; });
  if (operation === "reassignment") input.head.rules[0].owner = "view";
  else input.head.rules.shift();
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(owners(plan), ["logic", "shell", "view"]);
  assert.deepEqual(proofs(plan), ["component/logic/unit", "component/shell/unit", "component/view/unit"]);
  assert.ok(plan.affected.every(item => item.effects.includes("artifact")));
});
test("A202-implicit-interface-metadata retains interface-only boundaries", () => {
  const input = fixture(); native(input);
  both(input, snapshot => {
    snapshot.rules[0].role = "interface"; snapshot.rules[0].effects = [];
    snapshot.boundaries.push({ ...boundary("logic", "store", ["interface"]), proofIds: ["component/store/native"] });
  }); input.head.rules[0].owner = "view";
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.ok(proofs(plan).includes("component/store/native"));
  for (const id of ["logic", "view"]) assert.deepEqual(plan.affected.find(item => item.id === id).effects, ["artifact", "interface"]);
});
test("A202-explicit-rule-metadata retains security alongside implicit production effects", () => {
  const input = fixture(); both(input, snapshot => { snapshot.rules[0].effects = ["security"]; });
  input.head.rules[0].owner = "view";
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  for (const id of ["logic", "view"]) assert.deepEqual(plan.affected.find(item => item.id === id).effects, ["artifact", "security"]);
});
test("A202-documentation-metadata does not acquire implicit production effects", () => {
  const input = fixture(); input.head.rules.find(item => item.id === "docs").owner = "view";
  const plan = planAffected(input); assert.equal(plan.disposition, "no-runtime"); assert.deepEqual(owners(plan), []);
});

function sharedOwnerProof(input, owner = "view") {
  both(input, snapshot => {
    const proof = snapshot.suites.find(item => item.id === "component/" + owner + "/unit");
    proof.owner = "tooling"; proof.prerequisites = [];
    for (const edge of snapshot.boundaries.filter(item => item.consumer === owner)) edge.proofIds = ["component/" + edge.producer + "/unit"];
  });
  input.changes = [edit("packages/" + owner + "/tests/unit.test.mjs")];
}
test("A202-shared-owner-proof-evidence retains trigger dependencies without execution prerequisites", () => {
  const input = fixture(); sharedOwnerProof(input);
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(plan.evidenceRequirements[0].dependencyComponents, ["logic"]);
  assert.deepEqual(plan.prerequisites, []); assert.deepEqual(plan.selectedSuites[0].prerequisites, []);
  assert.deepEqual(proofs(plan), ["component/view/unit"]); assert.deepEqual(owners(plan), []);
  const changed = structuredClone(input);
  both(changed, snapshot => { snapshot.entries.find(item => item.path === "packages/logic/src/value.mjs").objectId = "other-logic"; });
  const changedPlan = planAffected(changed); assert.equal(changedPlan.disposition, "executable");
  assert.notDeepEqual(changedPlan.dependencyInputs, plan.dependencyInputs);
  assert.equal(changedPlan.dependencyInputs.find(item => item.component === "logic").sourceInputs[0].objectId, "other-logic");
});
test("A202-shared-owner-proof-closure retains transitive dependencies and deduplicates multiple owners", () => {
  const input = fixture(); sharedOwnerProof(input, "shell");
  both(input, snapshot => { snapshot.components[1].ownerProofs[0].proofIds.push("component/shell/unit"); });
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(plan.evidenceRequirements[0].dependencyComponents, ["logic", "view"]);
  assert.deepEqual(plan.dependencyInputs.map(item => item.component).sort(), ["logic", "view"]);
  assert.deepEqual(plan.prerequisites, []);
  const shuffled = structuredClone(input); both(shuffled, snapshot => { snapshot.components.reverse(); snapshot.components.forEach(item => item.ownerProofs.reverse()); });
  assert.deepEqual(planAffected(shuffled), plan);
});
test("A202-explicit-rule-proof-evidence retains the associated component dependency roots", () => {
  const input = fixture(); sharedOwnerProof(input);
  both(input, snapshot => {
    snapshot.components[1].ownerProofs[0].proofIds = ["component/logic/unit"];
    snapshot.rules.find(item => item.id === "view-source").proofIds = ["component/view/unit"];
  });
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(plan.evidenceRequirements[0].dependencyComponents, ["logic"]); assert.deepEqual(plan.prerequisites, []);
});
test("A202-shared-owner-proof-dependency symlinks cannot evade required evidence validation", () => {
  const input = fixture(); sharedOwnerProof(input);
  both(input, snapshot => { snapshot.entries.find(item => item.path === "packages/logic/src/value.mjs").kind = "symlink"; });
  blocked(input, "UNSUPPORTED_INPUT_KIND", "packages/logic/src/value.mjs");
});
test("A202-shared-owner-proof-evidence excludes unrelated components", () => {
  const input = fixture(); sharedOwnerProof(input);
  const original = planAffected(input), changed = structuredClone(input);
  both(changed, snapshot => { snapshot.entries.find(item => item.path === "packages/shell/src/value.mjs").objectId = "unrelated-shell"; });
  const plan = planAffected(changed); assert.equal(plan.disposition, "executable");
  assert.deepEqual(plan.evidenceRequirements, original.evidenceRequirements); assert.deepEqual(plan.dependencyInputs, original.dependencyInputs);
});

for (const variant of ["kind", "resources", "owner", "kind-and-resources"]) test("A202-retained-native-suite-metadata rejects in-place " + variant + " downgrades", () => {
  const input = fixture(); native(input);
  const proof = input.head.suites.find(item => item.id === "component/store/native");
  if (variant.includes("kind")) proof.kind = "node";
  if (variant.includes("resources")) proof.resources = ["node"];
  if (variant === "owner") proof.owner = "tooling";
  const plan = blocked(input, "PROOF_SCOPE_GAP");
  assert.ok(plan.diagnostics.some(item => item.code === "PROOF_SCOPE_GAP" && item.snapshot === "head" && item.candidates.includes("store")));
  assert.deepEqual(proofs(plan), ["component/store/native"]);
  assert.deepEqual(plan.selectedSuites[0].baseObligations, ["component/store/native"]);
  assert.deepEqual(owners(plan), []);
});
test("A202-native-suite-metadata retains valid contracts without production cascades", () => {
  for (const variant of ["command", "runtime", "scope"]) {
    const input = fixture(); native(input);
    const proof = input.head.suites.find(item => item.id === "component/store/native");
    if (variant === "command") proof.command += " --updated";
    if (variant === "runtime") proof.runtime = { postgres: "18" };
    if (variant === "scope") proof.expectedScope = { ...proof.expectedScope, mode: "subset", selector: "transactions" };
    const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
    assert.deepEqual(proofs(plan), ["component/store/native"]); assert.deepEqual(owners(plan), []);
    assert.deepEqual(plan.selectedBoundaries, []); assert.deepEqual(plan.prerequisites, []);
    assert.ok(plan.reasons.some(item => item.code === "RETAINED_OWNER_CONTRACT" && item.effect === "persistence" && item.to === "store"));
  }
});
test("A202-unit-suite-metadata does not activate separate persistence triggers", () => {
  const input = fixture(); splitNativeProofs(input);
  input.head.suites.find(item => item.id === "component/store/unit").command += " --updated";
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(proofs(plan), ["component/store/unit"]); assert.deepEqual(plan.requiredResources, ["node"]);
  assert.deepEqual(owners(plan), []);
});
test("A202-suite-metadata-contract retains grouped companion native proofs", () => {
  const input = fixture(); splitNativeProofs(input);
  both(input, snapshot => { snapshot.components.find(item => item.id === "store").ownerProofs[1].proofIds.push("component/store/unit"); });
  input.head.suites.find(item => item.id === "component/store/unit").command += " --updated";
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.deepEqual(proofs(plan), ["component/store/native", "component/store/unit"]);
  assert.deepEqual(plan.selectedSuites.find(item => item.id === "component/store/native").baseObligations, ["component/store/native"]);
  assert.deepEqual(owners(plan), []);
});

for (const ecosystem of ["npm", "python"]) test("A202-shared-owner-declaration retains external " + ecosystem + " dependency metadata", () => {
  const input = fixture(); sharedOwnerProof(input);
  both(input, snapshot => { snapshot.components[1].dependencies.push({ kind: "external", ecosystem, name: "zod" }); });
  const initial = planAffected(input); assert.equal(initial.disposition, "executable");
  const changed = structuredClone(input);
  both(changed, snapshot => { snapshot.components[1].dependencies.find(item => item.kind === "external").name = "lodash"; });
  const updated = planAffected(changed); assert.equal(updated.disposition, "executable");
  assert.notDeepEqual(updated.evidenceRequirements, initial.evidenceRequirements);
  const declarations = updated.evidenceRequirements[0].manifestInputs.components;
  assert.deepEqual(declarations.map(item => item.id), ["view"]);
  assert.ok(declarations[0].dependencies.some(item => item.kind === "external" && item.ecosystem === ecosystem && item.name === "lodash"));
  assert.deepEqual(updated.prerequisites, []); assert.deepEqual(updated.evidenceRequirements[0].dependencyComponents, ["logic"]);
});
for (const field of ["root", "artifacts"]) test("A202-shared-owner-declaration retains associated " + field + " metadata", () => {
  const input = fixture(); sharedOwnerProof(input);
  const initial = planAffected(input), changed = structuredClone(input);
  both(changed, snapshot => {
    if (field === "root") snapshot.components[1].root = "packages";
    else snapshot.components[1].artifacts[0].name = "@fixture/renamed-view";
  });
  const updated = planAffected(changed); assert.equal(updated.disposition, "executable");
  assert.notDeepEqual(updated.evidenceRequirements, initial.evidenceRequirements);
  assert.deepEqual(updated.evidenceRequirements[0].manifestInputs.components[0][field], changed.head.components[1][field]);
  assert.deepEqual(updated.prerequisites, []);
});
test("A202-shared-owner-declarations retain multiple normalized owners deterministically", () => {
  const input = fixture(); sharedOwnerProof(input, "shell");
  both(input, snapshot => {
    snapshot.components[1].ownerProofs[0].proofIds.push("component/shell/unit");
    snapshot.components[1].dependencies.push({ kind: "external", ecosystem: "npm", name: "zod" });
    snapshot.components[2].dependencies.push({ kind: "external", ecosystem: "python", name: "schema-lib" });
  });
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(plan.evidenceRequirements[0].manifestInputs.components.map(item => item.id).sort(), ["shell", "view"]);
  assert.deepEqual(plan.prerequisites, []);
  const shuffled = structuredClone(input);
  both(shuffled, snapshot => { snapshot.components.reverse(); snapshot.components.forEach(item => { item.dependencies.reverse(); item.ownerProofs.forEach(trigger => trigger.on.reverse()); }); });
  assert.deepEqual(planAffected(shuffled), plan);
});
test("A202-explicit-rule-owner-declaration retains dependency metadata outside owner triggers", () => {
  const input = fixture(); sharedOwnerProof(input);
  both(input, snapshot => {
    snapshot.components[1].ownerProofs[0].proofIds = ["component/logic/unit"];
    snapshot.components[1].dependencies.push({ kind: "external", ecosystem: "npm", name: "zod" });
    snapshot.rules.find(item => item.id === "view-source").proofIds = ["component/view/unit"];
  });
  const plan = planAffected(input); assert.equal(plan.disposition, "executable");
  assert.deepEqual(plan.evidenceRequirements[0].manifestInputs.components.map(item => item.id), ["view"]);
  assert.ok(plan.evidenceRequirements[0].manifestInputs.components[0].dependencies.some(item => item.name === "zod"));
  assert.deepEqual(plan.prerequisites, []);
});
test("A202-shared-owner-declarations exclude unrelated component metadata", () => {
  const input = fixture(); sharedOwnerProof(input);
  const initial = planAffected(input), changed = structuredClone(input);
  both(changed, snapshot => { snapshot.components[2].dependencies.push({ kind: "external", ecosystem: "npm", name: "unrelated" }); });
  const updated = planAffected(changed); assert.equal(updated.disposition, "executable");
  assert.deepEqual(updated.evidenceRequirements, initial.evidenceRequirements); assert.deepEqual(updated.dependencyInputs, initial.dependencyInputs);
});

const lifecycleOwner = snapshot => snapshot.components.find(item => item.id === "lifecycle");
const lifecycleProofId = effect => "component/lifecycle/" + effect;
const lifecycleSource = "packages/lifecycle/src/value.mjs";
const lifecycleTest = "packages/lifecycle/tests/unit.test.mjs";
function lifecycleFixture(effect) {
  const input = fixture();
  both(input, snapshot => {
    const owner = component("lifecycle");
    owner.ownerProofs = [{ on: ["artifact"], proofIds: ["component/lifecycle/unit"] }, { on: [effect], proofIds: [lifecycleProofId(effect)] }];
    const proof = { ...suite("lifecycle"), id: lifecycleProofId(effect) };
    if (effect === "persistence") { proof.kind = "postgres"; proof.resources = ["python", "postgres"]; }
    snapshot.components.push(owner); snapshot.suites.push(suite("lifecycle"), proof);
    snapshot.rules.push(rule("lifecycle-source", owner.id, directory("packages/lifecycle/src"), "production", ["artifact", effect]));
    snapshot.entries.push({ path: lifecycleSource, kind: "file", objectId: "lifecycle-source" }, { path: lifecycleTest, kind: "file", objectId: "lifecycle-test" });
  });
  return input;
}
function removeLifecycleOwner(snapshot) {
  snapshot.components = snapshot.components.filter(item => item.id !== "lifecycle");
  snapshot.suites = snapshot.suites.filter(item => item.owner !== "lifecycle");
  snapshot.rules = snapshot.rules.filter(item => item.owner !== "lifecycle");
  snapshot.entries = snapshot.entries.filter(item => !item.path.startsWith("packages/lifecycle/"));
}
function removeLifecycleEffect(snapshot, effect, removeProof = false) {
  lifecycleOwner(snapshot).ownerProofs = lifecycleOwner(snapshot).ownerProofs.filter(item => !item.on.includes(effect));
  snapshot.rules.find(item => item.id === "lifecycle-source").effects = ["artifact"];
  if (removeProof) snapshot.suites = snapshot.suites.filter(item => item.id !== lifecycleProofId(effect));
}
const lifecycleCases = [
  { entity: "owner", stage: "added", change(input) {
    removeLifecycleOwner(input.base); input.changes = [lifecycleSource, lifecycleTest].map(newPath => ({ status: "A", newPath }));
  } },
  { entity: "owner", stage: "retained", change(input) { input.changes = [edit(lifecycleSource)]; } },
  { entity: "owner", stage: "modified", change(input) {
    lifecycleOwner(input.head).dependencies.push({ kind: "external", ecosystem: "npm", name: "new-tool" });
  } },
  { entity: "owner", stage: "replaced", blocked: "REMOVED_PROOF_UNREPLACED", change(input) {
    lifecycleOwner(input.head).id = "lifecycle-v2";
    for (const proof of input.head.suites.filter(item => item.owner === "lifecycle")) proof.owner = "lifecycle-v2";
    input.head.rules.find(item => item.id === "lifecycle-source").owner = "lifecycle-v2";
  } },
  { entity: "owner", stage: "removed", blocked: "REMOVED_PROOF_UNREPLACED", change(input) {
    removeLifecycleOwner(input.head); input.changes = [lifecycleSource, lifecycleTest].map(oldPath => ({ status: "D", oldPath }));
  } },
  { entity: "suite", stage: "added", change(input, effect) {
    const proof = { ...input.head.suites.find(item => item.id === lifecycleProofId(effect)), id: lifecycleProofId(effect) + "-extra" };
    input.head.suites.push(proof); lifecycleOwner(input.head).ownerProofs[1].proofIds.push(proof.id);
  } },
  { entity: "suite", stage: "retained", change(input) { input.changes = [edit(lifecycleTest)]; } },
  { entity: "suite", stage: "modified", change(input, effect) {
    input.head.suites.find(item => item.id === lifecycleProofId(effect)).command += " --updated";
  } },
  { entity: "suite", stage: "replaced", change(input, effect) {
    const proof = input.head.suites.find(item => item.id === lifecycleProofId(effect));
    proof.replaces = [proof.id]; proof.id += "-v2"; lifecycleOwner(input.head).ownerProofs[1].proofIds = [proof.id];
  } },
  { entity: "suite", stage: "removed", blocked: "REMOVED_PROOF_UNREPLACED", change(input, effect) { removeLifecycleEffect(input.head, effect, true); } },
  { entity: "effect", stage: "added", change(input, effect) { removeLifecycleEffect(input.base, effect, true); } },
  { entity: "effect", stage: "retained", change(input) { input.changes = [edit(lifecycleSource)]; } },
  { entity: "effect", stage: "modified", change(input, effect) {
    lifecycleOwner(input.head).ownerProofs = [{ on: ["artifact", effect], proofIds: [lifecycleProofId(effect)] }];
  } },
  { entity: "effect", stage: "replaced", blocked: "MISSING_REQUIRED_PROOF", change(input) {
    lifecycleOwner(input.head).ownerProofs[1].on = ["runtime"];
    input.head.rules.find(item => item.id === "lifecycle-source").effects = ["artifact", "runtime"];
  } },
  { entity: "effect", stage: "removed", blocked: "MISSING_REQUIRED_PROOF", change(input, effect) { removeLifecycleEffect(input.head, effect); } },
];
for (const effect of ["security", "persistence"]) for (const row of lifecycleCases) test("A202-lifecycle-" + row.entity + "-" + row.stage + " " + effect + " keeps endpoint obligations directional", () => {
  const input = lifecycleFixture(effect); row.change(input, effect);
  const plan = row.blocked ? blocked(input, row.blocked) : planAffected(input);
  if (!row.blocked) assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  const selection = id => plan.selectedSuites.find(item => item.id === id);
  if (row.entity === "owner" && row.stage === "added") {
    assert.deepEqual(owners(plan), ["lifecycle"]);
    assert.ok(plan.selectedSuites.every(item => item.baseObligations.length === 0));
    assert.ok(!plan.reasons.some(item => item.snapshot === "base" && item.to === "lifecycle"));
  }
  if (row.entity === "owner" && row.stage === "modified") {
    assert.deepEqual(proofs(plan), ["component/lifecycle/unit"]); assert.deepEqual(plan.requiredResources, ["node"]);
  }
  if (row.entity === "suite" && row.stage === "added") assert.deepEqual(selection(lifecycleProofId(effect) + "-extra").baseObligations, []);
  if (row.entity === "suite" && row.stage === "replaced") assert.deepEqual(selection(lifecycleProofId(effect) + "-v2").baseObligations, [lifecycleProofId(effect)]);
  if (row.entity === "effect" && row.stage === "added") {
    assert.deepEqual(selection(lifecycleProofId(effect)).baseObligations, []);
    assert.ok(!plan.reasons.some(item => item.code === "RETAINED_OWNER_CONTRACT" && item.snapshot === "base" && item.to === "lifecycle" && item.effect === effect));
  }
  if (row.stage === "retained" || row.entity === "suite" && row.stage === "modified") assert.deepEqual(selection(lifecycleProofId(effect)).baseObligations, [lifecycleProofId(effect)]);
  if (row.entity === "effect" && row.stage === "replaced") assert.ok(!plan.diagnostics.some(item => item.snapshot === "base" && item.reason.includes("runtime")));
  if (effect === "persistence" && row.entity === "effect" && ["replaced", "removed"].includes(row.stage)) assert.ok(plan.diagnostics.some(item => item.code === "PROOF_SCOPE_GAP" && item.snapshot === "head"));
  assert.equal(plan.qualification, "not-evaluated");
});
for (const entity of ["owner", "effect"]) for (const variant of ["kind", "resources", "owner"]) test("A202-lifecycle-native-" + entity + "-added rejects head " + variant + " gaps without historical obligations", () => {
  const input = lifecycleFixture("persistence"); lifecycleCases.find(row => row.entity === entity && row.stage === "added").change(input, "persistence");
  const proof = input.head.suites.find(item => item.id === lifecycleProofId("persistence"));
  if (variant === "kind") proof.kind = "node";
  if (variant === "resources") proof.resources = ["node"];
  if (variant === "owner") proof.owner = "tooling";
  const plan = blocked(input, "PROOF_SCOPE_GAP");
  assert.ok(plan.diagnostics.every(item => item.snapshot !== "base"));
  assert.ok(plan.diagnostics.some(item => item.code === "PROOF_SCOPE_GAP" && item.snapshot === "head"));
});
for (const effect of ["security", "persistence"]) test("A202-head-only-source-effect " + effect + " does not invent base proof obligations", () => {
  const input = lifecycleFixture(effect), path = "packages/lifecycle/src/added.mjs";
  input.head.entries.push({ path, kind: "file", objectId: "added" }); input.changes = [{ status: "A", newPath: path }];
  const plan = planAffected(input); assert.equal(plan.disposition, "executable", JSON.stringify(plan.diagnostics));
  assert.ok(plan.selectedSuites.every(item => item.baseObligations.length === 0));
  assert.ok(!plan.reasons.some(item => item.code === "AFFECTED_COMPONENT" && item.snapshot === "base"));
});
for (const effect of ["security", "persistence"]) test("A202-lifecycle-added-owner " + effect + " still rejects unknown production mapping", () => {
  const input = lifecycleFixture(effect); lifecycleCases.find(row => row.entity === "owner" && row.stage === "added").change(input, effect);
  input.head.rules = input.head.rules.filter(item => item.id !== "lifecycle-source");
  blocked(input, "UNKNOWN_OWNER", lifecycleSource);
});

function componentStateFixture(before, after, effect) {
  const input = lifecycleFixture(effect === "artifact" ? "security" : effect);
  if (effect === "artifact") both(input, snapshot => removeLifecycleEffect(snapshot, "security", true));
  for (const [snapshot, state] of [[input.base, before], [input.head, after]]) if (state !== "implemented") {
    removeLifecycleOwner(snapshot);
    if (state === "planned") snapshot.components.push({ id: "lifecycle", status: "planned", root: "packages/lifecycle", artifacts: [], dependencies: [], ownerProofs: [] });
  }
  if (before === after && after === "planned") {
    const owner = lifecycleOwner(input.head); owner.root += "-future";
    owner.artifacts = [{ ecosystem: "npm", name: "@fixture/future-lifecycle" }];
    owner.dependencies = [{ kind: "internal", ecosystem: "component", name: "logic" }];
  }
  if (before === after && after === "implemented") lifecycleOwner(input.head).dependencies.push({ kind: "external", ecosystem: "npm", name: "updated-tool" });
  for (const path of [lifecycleSource, lifecycleTest]) {
    const oldEntry = input.base.entries.find(item => item.path === path), newEntry = input.head.entries.find(item => item.path === path);
    if (!oldEntry && newEntry) input.changes.push({ status: "A", newPath: path });
    if (oldEntry && !newEntry) input.changes.push({ status: "D", oldPath: path });
  }
  return input;
}
const componentStateCases = [
  ["absent", "absent", "no-runtime"], ["absent", "planned", "no-runtime"], ["absent", "implemented", "executable"],
  ["planned", "absent", "no-runtime"], ["planned", "planned", "no-runtime"], ["planned", "implemented", "executable"],
  ["implemented", "absent", "blocked"], ["implemented", "planned", "blocked"], ["implemented", "implemented", "executable"],
];
for (const [before, after, expected] of componentStateCases) for (const effect of [before, after].includes("implemented") ? ["artifact", "persistence"] : ["artifact"]) test("A202-component-state-" + before + "-to-" + after + " " + effect + " separates metadata from executable obligations", () => {
  const input = componentStateFixture(before, after, effect), plan = planAffected(input);
  assert.equal(plan.disposition, expected, JSON.stringify(plan.diagnostics));
  if (expected === "no-runtime") {
    assert.deepEqual(plan.selectedSuites, []); assert.deepEqual(plan.affected, []); assert.deepEqual(plan.requiredResources, []);
  } else if (expected === "blocked") {
    assert.ok(plan.diagnostics.some(item => item.code === "REMOVED_PROOF_UNREPLACED"));
    if (after === "planned") assert.ok(plan.diagnostics.some(item => item.code === "PLANNED_TARGET" && item.snapshot === "head"));
    if (after === "planned" && effect === "persistence") assert.ok(plan.diagnostics.some(item => item.code === "PROOF_SCOPE_GAP" && item.snapshot === "head"));
  } else if (before !== "implemented") {
    assert.ok(plan.selectedSuites.every(item => item.baseObligations.length === 0));
    assert.ok(!plan.reasons.some(item => ["AFFECTED_COMPONENT", "CHANGED_OWNER", "RETAINED_OWNER_CONTRACT"].includes(item.code) && item.snapshot === "base" && item.to === "lifecycle"));
    if (effect === "persistence") assert.ok(proofs(plan).includes(lifecycleProofId(effect)));
    if (before === "planned") {
      const absent = structuredClone(input); removeLifecycleOwner(absent.base);
      const absentPlan = planAffected(absent); assert.equal(absentPlan.disposition, "executable");
      assert.deepEqual(plan.evidenceRequirements, absentPlan.evidenceRequirements); assert.deepEqual(plan.selectedSuites, absentPlan.selectedSuites);
    }
  } else {
    assert.deepEqual(proofs(plan), ["component/lifecycle/unit"]); assert.deepEqual(plan.requiredResources, ["node"]);
    assert.deepEqual(plan.selectedSuites[0].baseObligations, ["component/lifecycle/unit"]);
  }
  assert.equal(plan.qualification, "not-evaluated");
});
for (const effect of ["artifact", "persistence"]) test("A202-planned-promotion-base-source " + effect + " retains genuine changed ownership obligations", () => {
  const input = componentStateFixture("planned", "implemented", effect);
  input.base.entries.push({ path: lifecycleSource, kind: "file", objectId: "prior-source" });
  input.base.rules.push(structuredClone(input.head.rules.find(item => item.id === "lifecycle-source")));
  input.changes = [edit(lifecycleSource), { status: "A", newPath: lifecycleTest }];
  const plan = blocked(input, "PLANNED_TARGET");
  assert.ok(plan.diagnostics.some(item => item.code === "PLANNED_TARGET" && item.snapshot === "base"));
  assert.ok(plan.diagnostics.some(item => item.code === "MISSING_REQUIRED_PROOF" && item.snapshot === "base"));
});
for (const effect of ["artifact", "persistence"]) test("A202-planned-removal-base-source " + effect + " cannot erase genuine deleted inputs", () => {
  const input = componentStateFixture("planned", "absent", effect);
  input.base.entries.push({ path: lifecycleSource, kind: "file", objectId: "prior-source" });
  input.base.rules.push(rule("lifecycle-source", "lifecycle", directory("packages/lifecycle/src"), "production", effect === "artifact" ? ["artifact"] : ["artifact", effect]));
  input.changes = [{ status: "D", oldPath: lifecycleSource }];
  const plan = blocked(input, "PLANNED_TARGET");
  assert.ok(plan.diagnostics.some(item => item.code === "PLANNED_TARGET" && item.snapshot === "base"));
  assert.ok(plan.diagnostics.some(item => item.code === "REMOVED_PROOF_UNREPLACED"));
});
for (const effect of ["artifact", "persistence"]) test("A202-planned-promotion-base-unknown " + effect + " cannot adopt unowned historical inputs", () => {
  const input = componentStateFixture("planned", "implemented", effect);
  input.base.entries.push({ path: lifecycleSource, kind: "file", objectId: "prior-source" });
  input.changes = [edit(lifecycleSource), { status: "A", newPath: lifecycleTest }];
  const plan = blocked(input, "UNKNOWN_OWNER", lifecycleSource);
  assert.ok(plan.diagnostics.some(item => item.code === "UNKNOWN_OWNER" && item.snapshot === "base"));
});
for (const effect of ["artifact", "persistence"]) test("A202-planned-promotion-base-proof " + effect + " cannot conceal explicit executable contracts", () => {
  const input = lifecycleFixture(effect === "artifact" ? "security" : effect);
  if (effect === "artifact") both(input, snapshot => removeLifecycleEffect(snapshot, "security", true));
  lifecycleOwner(input.base).status = "planned"; lifecycleOwner(input.base).artifacts = [];
  input.changes = [edit(lifecycleTest)];
  const plan = blocked(input, "PLANNED_TARGET"), id = effect === "artifact" ? "component/lifecycle/unit" : lifecycleProofId(effect);
  assert.ok(plan.diagnostics.some(item => item.code === "PLANNED_TARGET" && item.snapshot === "base"));
  assert.ok(plan.selectedSuites.find(item => item.id === id).baseObligations.includes(id));
});
for (const variant of ["kind", "resources", "owner"]) test("A202-planned-native-promotion rejects head " + variant + " gaps", () => {
  const input = componentStateFixture("planned", "implemented", "persistence"), proof = input.head.suites.find(item => item.id === lifecycleProofId("persistence"));
  if (variant === "kind") proof.kind = "node";
  if (variant === "resources") proof.resources = ["node"];
  if (variant === "owner") proof.owner = "tooling";
  const plan = blocked(input, "PROOF_SCOPE_GAP");
  assert.ok(plan.diagnostics.every(item => item.snapshot !== "base"));
  assert.ok(plan.diagnostics.some(item => item.code === "PROOF_SCOPE_GAP" && item.snapshot === "head"));
});
for (const effect of ["artifact", "persistence"]) test("A202-planned-promotion-head-unknown " + effect + " still rejects missing ownership", () => {
  const input = componentStateFixture("planned", "implemented", effect);
  input.head.rules = input.head.rules.filter(item => item.id !== "lifecycle-source");
  const plan = blocked(input, "UNKNOWN_OWNER", lifecycleSource);
  assert.ok(plan.diagnostics.every(item => item.snapshot !== "base"));
});
for (const effect of ["artifact", "persistence"]) test("A202-planned-head-source " + effect + " remains non-executable when actual inputs are selected", () => {
  const input = componentStateFixture("absent", "planned", effect);
  input.head.entries.push({ path: lifecycleSource, kind: "file", objectId: "new-source" });
  input.head.rules.push(rule("lifecycle-source", "lifecycle", directory("packages/lifecycle/src"), "production", effect === "artifact" ? ["artifact"] : ["artifact", effect]));
  input.changes = [{ status: "A", newPath: lifecycleSource }];
  const plan = blocked(input, "PLANNED_TARGET");
  assert.ok(plan.diagnostics.some(item => item.code === "PLANNED_TARGET" && item.snapshot === "head"));
});
