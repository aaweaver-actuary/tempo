import { createHash } from "node:crypto";

/**
 * Pure planning projection, not a production manifest or an executor.
 * All paths/selectors are repository-relative; suite cwd may also be ".".
 * Node built-ins only: importing this module does not inspect the environment.
 *
 * @typedef {"artifact"|"interface"|"persistence"|"security"|"runtime"} Effect
 * @typedef {{path:string,kind:"file"|"directory",exclude?:Array<{path:string,kind:"file"|"directory"}>}} Selector
 * @typedef {{id:string,owner:string|null,selector:Selector,role:"production"|"artifact"|"interface"|"test"|"fixture"|"toolchain"|"runner"|"documentation",effects:Effect[],proofIds:string[]}} InputRule
 * @typedef {{kind:"internal"|"external",ecosystem:"component"|"npm"|"python",name:string}} Dependency
 * @typedef {{id:string,status:"implemented"|"planned",root:string,artifacts:Array<{ecosystem:"npm"|"python",name:string}>,dependencies:Dependency[],ownerProofs:Array<{on:Effect[],proofIds:string[]}>}} Component
 * @typedef {{id:string,owner:string,kind:string,cwd:string,command:string,inputs:Selector[],prerequisites:string[],resources:string[],expectedScope:{mode:"whole-suite"|"subset",selector:string|null,inventoryInputs:Selector[]},replaces?:string[],runtime?:Record<string,string>}} Suite
 * @typedef {{id:string,producer:string,consumer:string,on:Effect[],proofIds:string[]}} Boundary
 * @typedef {{identity:{revision:string,tree:string,manifestDigest:string,catalogDigest:string|null,selectionSemantics:"tempo-affected/1"},components:Component[],suites:Suite[],rules:InputRule[],boundaries:Boundary[],entries:Array<{path:string,kind:"file"|"symlink"|"gitlink",objectId:string}>}} Snapshot
 * @typedef {{status:"A"|"D"|"M"|"T"|"R"|"C",oldPath?:string,newPath?:string}} Change
 * @typedef {{comparison:{mode:"endpoints"|"merge-base",requestedBase:string,requestedHead:string,resolvedBase:string,resolvedHead:string,mergeBase:string|null},base:Snapshot,head:Snapshot,changes:Change[]}} AffectedInput
 * @typedef {{schemaVersion:1,selectionSemantics:string,disposition:"blocked"|"executable"|"no-runtime",qualification:"not-evaluated",comparison:object|null,snapshots:object,changes:Change[],paths:object[],unownedPaths:string[],affected:object[],selectedSuites:object[],selectedBoundaries:object[],prerequisites:string[],requiredResources:string[],reasons:object[],diagnostics:object[],evidenceRequirements:object[],dependencyInputs:object[],cycles:object[],statistics:object}} AffectedPlan
 */
const semantics = "tempo-affected/1";
const effects = new Set(["artifact", "interface", "persistence", "security", "runtime"]);
const roles = new Set(["production", "artifact", "interface", "test", "fixture", "toolchain", "runner", "documentation"]);
const compare = (left, right) => left < right ? -1 : left > right ? 1 : 0;
const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort(compare).map(key => [key, item[key]])) : item);
const sorted = values => [...values].sort((left, right) => compare(canonical(left), canonical(right)));
const ordered = values => [...new Map([...values].map(value => [canonical(value), value])).values()].sort((left, right) => compare(canonical(left), canonical(right)));
const unique = values => [...new Set(values)].sort(compare);
const digest = value => createHash("sha256").update(canonical(value)).digest("hex");
const pythonName = name => name.toLowerCase().replace(/[-_.]+/g, "-");
const beneath = (directory, path) => path === directory || path.startsWith(directory + "/");
const matches = (selector, path) => (selector.kind === "file" ? path === selector.path : path.startsWith(selector.path + "/"))
  && !(selector.exclude ?? []).some(exclusion => matches(exclusion, path));
const textValue = value => typeof value === "string" && value.length > 0;

class InvalidInput extends Error {
  constructor(code, reason, context = {}) { super(reason); this.diagnostic = { code, reason, ...context }; }
}
function requireInput(condition, reason, context = {}, code = "INVALID_MANIFEST") {
  if (!condition) throw new InvalidInput(code, reason, context);
}
function pathValue(path, cwd = false) {
  requireInput(typeof path === "string" && ((cwd && path === ".") || (path.length > 0 && !/^[A-Za-z]:/.test(path)
    && !/[\\\0]/.test(path) && path.split("/").every(segment => segment && segment !== "." && segment !== ".."))),
  "Use a nonescaping, case-sensitive repository-relative POSIX path", { path }, "INVALID_PATH");
  return path;
}
function strings(values, label, nonempty = false) {
  requireInput(Array.isArray(values) && values.every(textValue) && (!nonempty || values.length > 0), "Declare " + label);
  return unique(values);
}
function effectValues(values) {
  const result = strings(values, "effect classifications");
  requireInput(result.every(effect => effects.has(effect)), "Unknown effect classification");
  return result;
}
function selectorValue(selector, exclusions = true) {
  requireInput(selector && ["file", "directory"].includes(selector.kind), "Declare a file or directory selector");
  const result = { path: pathValue(selector.path), kind: selector.kind };
  if (selector.exclude !== undefined) {
    requireInput(exclusions && Array.isArray(selector.exclude), "Invalid selector exclusions");
    result.exclude = ordered(selector.exclude.map(exclusion => selectorValue(exclusion, false)));
    requireInput(result.exclude.every(exclusion => beneath(result.path, exclusion.path)), "Exclusions must be contained by their selector", { path: result.path });
  }
  return result;
}

// Segment trie: index once; each lookup visits path segments and actual matches.
class PathIndex {
  root = { children: new Map(), declarations: [] };
  add(selector, value) {
    let node = this.root;
    for (const segment of selector.path.split("/")) {
      if (!node.children.has(segment)) node.children.set(segment, { children: new Map(), declarations: [] });
      node = node.children.get(segment);
    }
    node.declarations.push({ selector, value });
  }
  overlapping(selector) {
    const found = [];
    let node = this.root;
    for (const segment of selector.path.split("/")) {
      node = node.children.get(segment);
      if (!node) return found;
      found.push(...node.declarations);
    }
    const pending = [...node.children.values()];
    for (let position = 0; position < pending.length; position++) {
      const child = pending[position]; found.push(...child.declarations); pending.push(...child.children.values());
    }
    return found;
  }
  lookup(path) {
    const found = [];
    let node = this.root;
    for (const segment of path.split("/")) {
      node = node.children.get(segment);
      if (!node) break;
      for (const declaration of node.declarations) if (matches(declaration.selector, path)) found.push(declaration.value);
    }
    return found;
  }
}

function selectorsOverlap(left, right) {
  if (left.kind === "file") return matches(left, left.path) && matches(right, left.path);
  if (right.kind === "file") return matches(right, right.path) && matches(left, right.path);
  if (!beneath(left.path, right.path) && !beneath(right.path, left.path)) return false;
  const common = left.path.length > right.path.length ? left.path : right.path;
  return ![...(left.exclude ?? []), ...(right.exclude ?? [])].some(exclusion => exclusion.kind === "directory" && beneath(exclusion.path, common));
}

function snapshotIndex(snapshot, label, diagnose, statistics) {
  requireInput(snapshot?.identity, "Supply both committed snapshot projections", { snapshot: label }, "BASE_UNAVAILABLE");
  for (const field of ["revision", "tree", "manifestDigest"]) requireInput(textValue(snapshot.identity[field]), "Missing snapshot " + field, { snapshot: label });
  requireInput(snapshot.identity.selectionSemantics === semantics && (snapshot.identity.catalogDigest === null || textValue(snapshot.identity.catalogDigest)), "Unsupported snapshot selection semantics/catalog identity", { snapshot: label });
  for (const field of ["components", "suites", "rules", "boundaries", "entries"]) requireInput(Array.isArray(snapshot[field]), "Supply normalized " + field + "; schema v1 is not a complete planning projection", { snapshot: label });
  const components = new Map(), suites = new Map(), entries = new Map(), rules = new Map(), boundaries = new Map();
  const aliases = new Map(), dependencies = new Map(), consumers = new Map(), boundaryPairs = new Map();
  const inputs = new PathIndex(), inventory = new PathIndex(), roots = new PathIndex(), production = new PathIndex(), tree = new PathIndex(), directories = new Set();
  const rulesByOwner = new Map(), rulesByProof = new Map(), entryMatches = new Map();
  const addIdentity = (map, value) => {
    requireInput(textValue(value?.id), "Missing stable declaration identity", { snapshot: label });
    requireInput(!map.has(value.id) || canonical(map.get(value.id)) === canonical(value), "Conflicting duplicate identity: " + value.id, { snapshot: label });
    map.set(value.id, value);
  };
  for (const raw of sorted(snapshot.components)) {
    requireInput(raw && ["implemented", "planned"].includes(raw.status), "Declare component status", { snapshot: label });
    requireInput(Array.isArray(raw.artifacts) && Array.isArray(raw.dependencies) && Array.isArray(raw.ownerProofs), "Declare artifacts, typed dependencies and owner proofs", { snapshot: label });
    const component = { id: raw.id, status: raw.status, root: pathValue(raw.root),
      artifacts: ordered(raw.artifacts.map(artifact => {
        requireInput(artifact && ["npm", "python"].includes(artifact.ecosystem) && textValue(artifact.name), "Declare typed artifact aliases");
        return { ecosystem: artifact.ecosystem, name: artifact.name };
      })), dependencies: ordered(raw.dependencies.map(dependency => {
        requireInput(dependency && ["internal", "external"].includes(dependency.kind) && ["component", "npm", "python"].includes(dependency.ecosystem) && textValue(dependency.name), "Classify dependency as internal or external with its ecosystem", { snapshot: label, candidates: [raw.id] }, "DEPENDENCY_CLASSIFICATION_REQUIRED");
        return { kind: dependency.kind, ecosystem: dependency.ecosystem, name: dependency.name };
      })), ownerProofs: ordered(raw.ownerProofs.map(trigger => {
        requireInput(trigger && typeof trigger === "object", "Declare owning proof triggers", { snapshot: label });
        return { on: effectValues(trigger.on), proofIds: strings(trigger.proofIds, "owner proof IDs", true) };
      })) };
    requireInput(!components.has(component.id), "Duplicate component identity", { snapshot: label, candidates: [component.id] });
    addIdentity(components, component);
  }
  for (const component of components.values()) {
    dependencies.set(component.id, new Set()); consumers.set(component.id, new Set());
    roots.add({ path: component.root, kind: "directory" }, component.id);
    for (const artifact of component.artifacts) {
      const key = artifact.ecosystem + ":" + (artifact.ecosystem === "python" ? pythonName(artifact.name) : artifact.name);
      if (!aliases.has(key)) aliases.set(key, new Set());
      aliases.get(key).add(component.id);
    }
    if (component.status === "implemented" && (!component.artifacts.length || !component.ownerProofs.length)) diagnose("INVALID_MANIFEST", "Implemented component needs an artifact and owning proof contract", { snapshot: label, candidates: [component.id], path: component.root });
    if (component.status === "planned" && component.ownerProofs.length) diagnose("PLANNED_TARGET", "Planned component cannot declare executable owning proofs", { snapshot: label, candidates: [component.id] });
  }
  for (const [alias, owners] of aliases) if (owners.size > 1) diagnose("AMBIGUOUS_ALIAS", "Assign this artifact alias to one component", { snapshot: label, path: alias, candidates: unique(owners) });
  for (const component of components.values()) for (const dependency of component.dependencies) {
    if (dependency.kind === "external") continue;
    const candidates = dependency.ecosystem === "component" ? new Set(components.has(dependency.name) ? [dependency.name] : [])
      : aliases.get(dependency.ecosystem + ":" + (dependency.ecosystem === "python" ? pythonName(dependency.name) : dependency.name)) ?? new Set();
    if (candidates.size !== 1) { diagnose(candidates.size ? "AMBIGUOUS_ALIAS" : "MISSING_INTERNAL_TARGET", "Declare one internal producer or explicitly classify this dependency as external", { snapshot: label, path: dependency.name, candidates: unique([component.id, ...candidates]) }); continue; }
    const producer = [...candidates][0];
    if (producer === component.id) { diagnose("SELF_DEPENDENCY", "Remove the component self-dependency", { snapshot: label, candidates: [producer] }); continue; }
    dependencies.get(component.id).add(producer); consumers.get(producer).add(component.id);
  }
  for (const raw of sorted(snapshot.suites)) {
    requireInput(raw && textValue(raw.owner) && textValue(raw.kind) && textValue(raw.command) && Array.isArray(raw.inputs), "Declare suite owner, kind, command and inputs", { snapshot: label });
    const scope = raw.expectedScope;
    requireInput(scope && ["whole-suite", "subset"].includes(scope.mode) && (scope.mode === "subset" ? textValue(scope.selector) : scope.selector === null) && Array.isArray(scope.inventoryInputs) && scope.inventoryInputs.length, "Declare nonempty expected collection inputs and an exact subset selector when applicable", { snapshot: label });
    requireInput(Array.isArray(raw.resources) && raw.resources.length && raw.resources.every(textValue), "Declare the actual resources used by this proof", { snapshot: label, boundary: raw.id }, "RESOURCE_MAPPING_REQUIRED");
    requireInput(raw.runtime === undefined || (raw.runtime && typeof raw.runtime === "object" && !Array.isArray(raw.runtime) && Object.entries(raw.runtime).every(([key, value]) => textValue(key) && textValue(value))), "Declare runtime/toolchain requirements as string-valued metadata", { snapshot: label, boundary: raw.id });
    const suite = { id: raw.id, owner: raw.owner, kind: raw.kind, cwd: pathValue(raw.cwd, true), command: raw.command,
      inputs: ordered(raw.inputs.map(selector => selectorValue(selector))), prerequisites: strings(raw.prerequisites, "suite prerequisites"), resources: strings(raw.resources, "suite resource classes", true),
      expectedScope: { mode: scope.mode, selector: scope.selector, inventoryInputs: ordered(scope.inventoryInputs.map(selector => selectorValue(selector))) }, replaces: strings(raw.replaces ?? [], "replaced suite IDs"), runtime: raw.runtime === undefined ? null : Object.fromEntries(Object.entries(raw.runtime).sort(([left], [right]) => compare(left, right))) };
    requireInput(suite.inputs.length, "Suite sensitivity inputs cannot be empty", { snapshot: label });
    if (suite.id === undefined) {
      const tuple = { owner: suite.owner, kind: suite.kind, cwd: suite.cwd, command: suite.command, inputs: suite.inputs };
      suite.id = "legacy/" + suite.owner + "/" + digest(tuple);
      suite.legacyIdentity = tuple;
    }
    addIdentity(suites, suite);
  }
  for (const suite of suites.values()) {
    for (const selector of suite.inputs) inputs.add(selector, { type: "suite", id: suite.id });
    for (const selector of suite.expectedScope.inventoryInputs) inventory.add(selector, suite.id);
    if (components.get(suite.owner)?.status === "planned") diagnose("PLANNED_TARGET", "A planned owner cannot execute a suite", { snapshot: label, candidates: [suite.owner], boundary: suite.id });
    for (const prerequisite of suite.prerequisites) if (!components.has(prerequisite)) diagnose("MISSING_INTERNAL_TARGET", "Map the suite prerequisite to a component", { snapshot: label, candidates: [prerequisite], boundary: suite.id });
  }
  for (const raw of sorted(snapshot.rules)) {
    requireInput(raw && roles.has(raw.role) && (raw.owner === null || components.has(raw.owner)), "Declare an existing input owner and input role", { snapshot: label });
    const rule = { id: raw.id, owner: raw.owner, selector: selectorValue(raw.selector), role: raw.role, effects: effectValues(raw.effects), proofIds: strings(raw.proofIds, "input proof IDs") };
    requireInput(!["test", "fixture", "documentation"].includes(rule.role) || !rule.effects.length, "Test, fixture and documentation inputs cannot propagate production effects", { snapshot: label, path: rule.selector.path });
    requireInput(!rule.effects.length || rule.owner !== null, "Production effects require an explicit component owner", { snapshot: label, path: rule.selector.path });
    requireInput(!["production", "interface"].includes(rule.role) || rule.owner !== null, "Production ownership cannot be null", { snapshot: label, path: rule.selector.path });
    addIdentity(rules, rule);
  }
  for (const rule of rules.values()) {
    inputs.add(rule.selector, { type: "rule", id: rule.id });
    if (!rulesByOwner.has(rule.owner)) rulesByOwner.set(rule.owner, []);
    rulesByOwner.get(rule.owner).push(rule);
    for (const proofId of rule.proofIds) {
      if (!rulesByProof.has(proofId)) rulesByProof.set(proofId, []);
      rulesByProof.get(proofId).push(rule);
    }
    if (["production", "interface"].includes(rule.role)) {
      for (const prior of production.overlapping(rule.selector)) if (prior.value.owner !== rule.owner && selectorsOverlap(prior.selector, rule.selector)) diagnose("OVERLAPPING_PRODUCTION", "Exclude the separately owned subtree or assign one production owner", { snapshot: label, path: rule.selector.path.length > prior.selector.path.length ? rule.selector.path : prior.selector.path, candidates: [prior.value.owner, rule.owner] });
      production.add(rule.selector, rule);
    }
  }
  for (const raw of sorted(snapshot.boundaries)) {
    requireInput(raw && components.has(raw.producer) && components.has(raw.consumer), "Boundary endpoints must be existing components", { snapshot: label });
    const boundary = { id: raw.id, producer: raw.producer, consumer: raw.consumer, on: effectValues(raw.on), proofIds: strings(raw.proofIds, "boundary proofs", true) };
    requireInput(boundary.on.length && boundary.producer !== boundary.consumer, "Boundary must connect distinct owners with explicit effects", { snapshot: label });
    addIdentity(boundaries, boundary);
  }
  for (const boundary of boundaries.values()) {
    const pair = canonical([boundary.producer, boundary.consumer]);
    if (!boundaryPairs.has(pair)) boundaryPairs.set(pair, []);
    boundaryPairs.get(pair).push(boundary);
  }
  for (const entry of sorted(snapshot.entries)) {
    requireInput(entry && ["file", "symlink", "gitlink"].includes(entry.kind) && textValue(entry.objectId), "Declare committed tree entry kinds and object IDs", { snapshot: label });
    const path = pathValue(entry.path);
    requireInput(!entries.has(path), "Duplicate tree entry", { snapshot: label, path });
    entries.set(path, { path, kind: entry.kind, objectId: entry.objectId });
    tree.add({ path, kind: "file" }, entries.get(path));
    const segments = path.split("/");
    for (let depth = 1; depth < segments.length; depth++) directories.add(segments.slice(0, depth).join("/"));
  }
  const matchingEntries = selector => {
    const key = canonical(selector);
    if (!entryMatches.has(key)) {
      const found = tree.overlapping(selector).filter(declaration => matches(selector, declaration.value.path)).map(declaration => declaration.value);
      entryMatches.set(key, ordered(found));
    }
    return entryMatches.get(key);
  };
  const reader = selector => matchingEntries(selector).length > 0;
  for (const component of components.values()) for (const trigger of component.ownerProofs) for (const proofId of trigger.proofIds) if (!suites.has(proofId)) diagnose("MISSING_REQUIRED_PROOF", "Declare the referenced owning proof instead of a false implemented claim", { snapshot: label, candidates: [component.id], boundary: proofId });
  for (const declaration of [...rules.values(), ...boundaries.values()]) for (const proofId of declaration.proofIds) if (!suites.has(proofId)) diagnose("MISSING_REQUIRED_PROOF", "Declare the referenced input/boundary proof", { snapshot: label, boundary: proofId });
  for (const [path] of entries) {
    statistics.indexedEntries++;
    const owners = unique(inputs.lookup(path).filter(match => match.type === "rule").map(match => rules.get(match.id)).filter(rule => ["production", "interface"].includes(rule.role)).map(rule => rule.owner));
    if (owners.length > 1) diagnose("OVERLAPPING_PRODUCTION", "Exclude the separately owned subtree or assign one production owner", { snapshot: label, path, candidates: owners });
    if (directories.has(path)) diagnose("INVALID_MANIFEST", "A committed file cannot also be a parent directory", { snapshot: label, path });
  }
  return { label, identity: Object.fromEntries(["revision", "tree", "manifestDigest", "catalogDigest", "selectionSemantics"].map(key => [key, snapshot.identity[key]])), components, suites, entries, rules, boundaries, inputs, inventory, roots, aliases, dependencies, consumers, boundaryPairs, reader, matchingEntries, rulesByOwner, rulesByProof, directories };
}

function normalizedChanges(changes) {
  requireInput(Array.isArray(changes), "Supply an explicit normalized change list", {}, "INVALID_CHANGE");
  const records = new Map(), usedOld = new Map(), usedNew = new Map();
  for (const change of sorted(changes)) {
    requireInput(change && ["A", "D", "M", "T", "R", "C"].includes(change.status), "Unsupported change status", {}, "INVALID_CHANGE");
    const oldRequired = change.status !== "A", newRequired = change.status !== "D";
    requireInput(Object.keys(change).every(key => ["status", "oldPath", "newPath"].includes(key)) && (oldRequired ? textValue(change.oldPath) : change.oldPath === undefined) && (newRequired ? textValue(change.newPath) : change.newPath === undefined), "Supply exactly the old/new paths required by the status", {}, "INVALID_CHANGE");
    const record = { status: change.status, ...(oldRequired ? { oldPath: pathValue(change.oldPath) } : {}), ...(newRequired ? { newPath: pathValue(change.newPath) } : {}) };
    requireInput(!["M", "T"].includes(record.status) || record.oldPath === record.newPath, "Modification/type changes require the same old/new path", { path: record.newPath }, "INVALID_CHANGE");
    requireInput(!["R", "C"].includes(record.status) || record.oldPath !== record.newPath, "Rename/copy needs distinct paths", { path: record.newPath }, "INVALID_CHANGE");
    const key = canonical(record);
    for (const [map, path] of [[usedOld, record.status === "C" ? undefined : record.oldPath], [usedNew, record.newPath]]) if (path !== undefined) {
      requireInput(!map.has(path) || map.get(path) === key, "Conflicting changes for one endpoint path", { path }, "INVALID_CHANGE"); map.set(path, key);
    }
    records.set(key, record);
  }
  return ordered(records.values());
}

// Iterative Kosaraju avoids call-stack limits and enumerating cyclic paths.
function graphCycles(index) {
  const visited = new Set(), finish = [];
  for (const start of unique(index.components.keys())) {
    if (visited.has(start)) continue;
    visited.add(start);
    const stack = [{ node: start, children: unique(index.consumers.get(start))[Symbol.iterator]() }];
    while (stack.length) {
      const current = stack.at(-1), next = current.children.next();
      if (next.done) { finish.push(current.node); stack.pop(); }
      else if (!visited.has(next.value)) {
        visited.add(next.value);
        stack.push({ node: next.value, children: unique(index.consumers.get(next.value))[Symbol.iterator]() });
      }
    }
  }
  const assigned = new Set(), cycles = [];
  for (const start of finish.reverse()) {
    if (assigned.has(start)) continue;
    const members = [], stack = [start]; assigned.add(start);
    while (stack.length) {
      const node = stack.pop(); members.push(node);
      for (const next of index.dependencies.get(node)) if (!assigned.has(next)) { assigned.add(next); stack.push(next); }
    }
    if (members.length > 1) cycles.push({ snapshot: index.label, components: unique(members) });
  }
  return ordered(cycles);
}

/**
 * Return an explainable selection; executable means mapping-complete, never tested.
 * Invalid caller data returns blocked diagnostics. No input is mutated.
 * Reasons are a bounded node/edge graph (possibly cyclic), not expanded traces.
 * The cycles field lists dependency SCCs; readers must visit each reason ID once.
 * Dependency input nodes are shared; evidence requirements reference their roots.
 * Cost is indexing + traversed graph + actual selector/output matches, then sorting.
 * @param {AffectedInput} input
 * @returns {AffectedPlan}
 */
export function planAffected(input) {
  const plan = { schemaVersion: 1, selectionSemantics: semantics, disposition: "blocked", qualification: "not-evaluated", comparison: null, snapshots: {}, changes: [], paths: [], unownedPaths: [], affected: [], selectedSuites: [], selectedBoundaries: [], prerequisites: [], requiredResources: [], reasons: [], diagnostics: [], evidenceRequirements: [], dependencyInputs: [], cycles: [], statistics: { indexedEntries: 0, changedPathLookups: 0, traversedEdges: 0, reasonNodes: 0 } };
  const diagnostics = new Map(), reasons = new Map(), selected = new Map(), selectedBoundaries = new Map(), affected = new Map(), seeds = new Map();
  function diagnose(code, reason, context = {}) {
    const diagnostic = { code, severity: "error", snapshot: context.snapshot ?? null, path: context.path ?? null, candidates: unique(context.candidates ?? []), boundary: context.boundary ?? null, reason, fix: context.fix ?? reason };
    diagnostics.set(canonical(diagnostic), diagnostic);
  }
  function reason(code, details = {}, parents = []) {
    const id = "reason/" + digest({ code, ...details });
    if (!reasons.has(id)) reasons.set(id, { id, code, ...details, parentReasonIds: new Set() });
    for (const parent of parents) reasons.get(id).parentReasonIds.add(parent);
    return id;
  }
  function addProof(index, id, cause) {
    const key = canonical([index.label, id]);
    if (!selected.has(key)) selected.set(key, { snapshot: index.label, id, reasonIds: new Set() });
    selected.get(key).reasonIds.add(cause);
    if (!index.suites.has(id)) diagnose("MISSING_REQUIRED_PROOF", "Declare the required suite and its collection contract", { snapshot: index.label, boundary: id });
  }
  function seed(owner, additions, cause) {
    if (!owner || !additions.length) return;
    if (!seeds.has(owner)) seeds.set(owner, { effects: new Set(), reasons: new Set() });
    for (const effect of additions) seeds.get(owner).effects.add(effect);
    seeds.get(owner).reasons.add(cause);
  }
  try {
    const comparison = input?.comparison;
    requireInput(comparison && ["endpoints", "merge-base"].includes(comparison.mode) && ["requestedBase", "requestedHead", "resolvedBase", "resolvedHead"].every(field => textValue(comparison[field])) && (comparison.mode === "endpoints" ? comparison.mergeBase === null : comparison.mergeBase === comparison.resolvedBase), "Supply explicit available endpoints, or an explicitly resolved unique merge base", {}, "INVALID_COMPARISON");
    plan.comparison = Object.fromEntries(["mode", "requestedBase", "requestedHead", "resolvedBase", "resolvedHead", "mergeBase"].map(key => [key, comparison[key]]));
    const buildSnapshot = (snapshot, label) => {
      try { return snapshotIndex(snapshot, label, diagnose, plan.statistics); }
      catch (error) {
        if (error instanceof InvalidInput) error.diagnostic.snapshot ??= label;
        throw error;
      }
    };
    const base = buildSnapshot(input.base, "base"), head = buildSnapshot(input.head, "head");
    requireInput(base.identity.revision === comparison.resolvedBase && head.identity.revision === comparison.resolvedHead, "Snapshot revisions must match the effective comparison endpoints", {}, "INVALID_COMPARISON");
    plan.snapshots = { base: base.identity, head: head.identity };
    plan.changes = normalizedChanges(input.changes);
    const indexes = [base, head];
    const describedOld = new Set(plan.changes.filter(change => change.status !== "C").map(change => change.oldPath));
    const describedNew = new Set(plan.changes.map(change => change.newPath));
    for (const path of unique([...base.entries.keys(), ...head.entries.keys()])) if (canonical(base.entries.get(path)) !== canonical(head.entries.get(path)) && !(describedOld.has(path) || describedNew.has(path))) diagnose("UNCOVERED_REQUIRED_INPUT", "Include this changed committed entry in the normalized change records", { path, snapshot: head.entries.has(path) ? "head" : "base" });
    const classify = (index, path, provenanceOnly = false) => {
      plan.statistics.changedPathLookups++;
      const entry = index.entries.get(path), declarations = index.inputs.lookup(path);
      if (!entry) diagnose("INVALID_CHANGE", "Changed path is absent from its declared committed snapshot", { snapshot: index.label, path });
      else if (entry.kind !== "file") diagnose("UNSUPPORTED_INPUT_KIND", "Map symlink/gitlink inputs explicitly; the planner never follows them", { snapshot: index.label, path });
      const matchedRules = declarations.filter(match => match.type === "rule").map(match => index.rules.get(match.id));
      const owners = unique(matchedRules.filter(rule => ["production", "interface"].includes(rule.role)).map(rule => rule.owner));
      const candidates = unique(index.roots.lookup(path));
      plan.paths.push({ path, snapshot: index.label, provenanceOnly, owners, candidateOwners: owners.length ? [] : candidates, roles: unique([...matchedRules.map(rule => rule.role), ...(index.inventory.lookup(path).length ? ["test"] : [])]), ruleIds: unique(matchedRules.map(rule => rule.id)) });
      if (provenanceOnly) return;
      if (owners.length > 1) diagnose("OVERLAPPING_PRODUCTION", "Assign one production owner or exclude the nested component subtree", { snapshot: index.label, path, candidates: owners });
      if (!matchedRules.length && !index.inventory.lookup(path).length) { plan.unownedPaths.push(path); diagnose("UNKNOWN_OWNER", "Declare production ownership or the actual test/build/documentation readers", { snapshot: index.label, path, candidates }); }
      const runtimeRules = matchedRules.filter(rule => rule.role !== "documentation");
      for (const rule of matchedRules) {
        if (rule.role === "documentation" && (runtimeRules.length || declarations.some(match => match.type === "suite"))) continue;
        const cause = reason(rule.role === "documentation" ? "DOCUMENTATION_ONLY" : rule.role === "production" ? "OWNED_PRODUCTION" : "DECLARED_INPUT", { snapshot: index.label, path, ruleId: rule.id, to: rule.owner });
        const additions = unique([...rule.effects, ...(["production", "interface"].includes(rule.role) ? ["artifact"] : []), ...(rule.role === "interface" ? ["interface"] : [])]);
        seed(rule.owner, additions, cause);
        for (const id of rule.proofIds) addProof(index, id, cause);
        if (rule.role !== "documentation" && !additions.length && !rule.proofIds.length && !declarations.some(match => match.type === "suite")) diagnose("UNCOVERED_REQUIRED_INPUT", "Map this selected build/test/runner input to a required proof", { snapshot: index.label, path, candidates: rule.owner ? [rule.owner] : [] });
      }
      for (const declaration of declarations.filter(match => match.type === "suite")) addProof(index, declaration.id, reason("SUITE_INPUT", { snapshot: index.label, path, to: declaration.id }));
    };
    for (const change of plan.changes) {
      if (change.oldPath) classify(base, change.oldPath, change.status === "C");
      if (change.newPath) classify(head, change.newPath);
      if (change.status === "A" && base.entries.has(change.newPath) || change.status === "D" && head.entries.has(change.oldPath)) diagnose("INVALID_CHANGE", "Addition/deletion contradicts the endpoint tree entries", { path: change.newPath ?? change.oldPath });
    }
    // Metadata deltas are first-class inputs; disappearing declarations retain proofs.
    for (const field of ["components", "rules", "boundaries", "suites"]) {
      const identities = unique([...base[field].keys(), ...head[field].keys()]);
      for (const id of identities) {
        const oldDeclaration = base[field].get(id), newDeclaration = head[field].get(id);
        if (canonical(oldDeclaration) === canonical(newDeclaration)) continue;
        for (const index of indexes) {
          const declaration = index[field].get(id);
          if (!declaration) continue;
          const cause = reason("DECLARATION_CHANGED", { snapshot: index.label, declaration: field, to: id });
          if (field === "components") seed(id, ["artifact"], cause);
          else if (field === "suites") addProof(index, id, cause);
          else {
            seed(declaration.owner ?? declaration.consumer, declaration.effects ?? ["artifact"], cause);
            for (const proofId of declaration.proofIds) addProof(index, proofId, cause);
          }
        }
      }
    }
    for (const index of indexes) {
      const boundariesByProducer = new Map();
      for (const boundary of index.boundaries.values()) {
        if (!boundariesByProducer.has(boundary.producer)) boundariesByProducer.set(boundary.producer, []);
        boundariesByProducer.get(boundary.producer).push(boundary);
      }
      plan.cycles.push(...graphCycles(index));
      const reached = new Map(), queue = [];
      const reach = (id, additions, cause, role) => {
        if (!index.components.has(id)) return;
        if (!reached.has(id)) reached.set(id, { effects: new Set(), reasonIds: new Set(), role });
        const state = reached.get(id);
        state.reasonIds.add(cause);
        let changed = false;
        for (const effect of additions) if (!state.effects.has(effect)) { state.effects.add(effect); changed = true; }
        if (role === "owner") state.role = role;
        if (changed) queue.push(id);
      };
      for (const [id, source] of seeds) reach(id, source.effects, reason("CHANGED_OWNER", { snapshot: index.label, to: id }, source.reasons), "owner");
      const walked = new Set();
      for (let position = 0; position < queue.length; position++) {
        const producer = queue[position], state = reached.get(producer);
        const aggregate = reason("AFFECTED_COMPONENT", { snapshot: index.label, to: producer }, state.reasonIds);
        const stateKey = canonical([producer, unique(state.effects)]);
        if (walked.has(stateKey)) continue;
        walked.add(stateKey);
        for (const consumer of index.consumers.get(producer)) {
          plan.statistics.traversedEdges++;
          const cause = reason("REVERSE_DEPENDENCY", { snapshot: index.label, from: producer, to: consumer }, [aggregate]);
          reach(consumer, ["artifact"], cause, "consumer");
          const boundaries = index.boundaryPairs.get(canonical([producer, consumer])) ?? [];
          if (!boundaries.some(boundary => boundary.on.includes("artifact"))) diagnose("UNMAPPED_BOUNDARY", "Map this producer/consumer edge to an explicit artifact consumer proof", { snapshot: index.label, candidates: [producer, consumer], boundary: producer + "->" + consumer });
        }
        for (const boundary of boundariesByProducer.get(producer) ?? []) if (boundary.on.some(effect => state.effects.has(effect))) {
          const cause = reason("BOUNDARY_PROOF", { snapshot: index.label, boundary: boundary.id, from: producer, to: boundary.consumer }, [aggregate]);
          reach(boundary.consumer, ["artifact"], cause, "consumer");
        }
      }
      // Select each owner/boundary once after reaching the fixed point.
      for (const [id, state] of reached) {
        const component = index.components.get(id), cause = reason("AFFECTED_COMPONENT", { snapshot: index.label, to: id }, state.reasonIds);
        const summary = affected.get(id) ?? { id, role: state.role, effects: new Set(), reasonIds: new Set() };
        if (state.role === "owner") summary.role = "owner";
        for (const effect of state.effects) summary.effects.add(effect);
        summary.reasonIds.add(cause); affected.set(id, summary);
        if (component.status === "planned") diagnose("PLANNED_TARGET", "Implement this owner and map its real proofs before executable selection", { snapshot: index.label, path: component.root, candidates: [id] });
        for (const effect of state.effects) {
          const triggers = component.ownerProofs.filter(trigger => trigger.on.includes(effect));
          if (!triggers.length) diagnose("MISSING_REQUIRED_PROOF", "Declare owning proof coverage for effect " + effect, { snapshot: index.label, candidates: [id] });
          for (const trigger of triggers) for (const proofId of trigger.proofIds) addProof(index, proofId, cause);
          if (effect === "persistence" && !triggers.some(trigger => trigger.proofIds.some(proofId => index.suites.get(proofId)?.owner === id && index.suites.get(proofId).kind === "postgres" && index.suites.get(proofId).resources.includes("postgres")))) diagnose("PROOF_SCOPE_GAP", "Persistence requires the owning real native PostgreSQL proof", { snapshot: index.label, candidates: [id] });
        }
      }
      for (const boundary of index.boundaries.values()) {
        const state = reached.get(boundary.producer);
        if (!state || !boundary.on.some(effect => state.effects.has(effect))) continue;
        const cause = reason("BOUNDARY_PROOF", { snapshot: index.label, boundary: boundary.id, from: boundary.producer, to: boundary.consumer }, [reason("AFFECTED_COMPONENT", { snapshot: index.label, to: boundary.producer }, state.reasonIds)]);
        if (!selectedBoundaries.has(boundary.id)) selectedBoundaries.set(boundary.id, { id: "boundary/" + boundary.id, reasonIds: new Set() });
        selectedBoundaries.get(boundary.id).reasonIds.add(cause);
        for (const proofId of boundary.proofIds) addProof(index, proofId, cause);
      }
    }
    const replacements = new Map();
    for (const suite of head.suites.values()) for (const prior of suite.replaces) {
      if (!replacements.has(prior)) replacements.set(prior, []);
      replacements.get(prior).push(suite.id);
    }
    const finalSuites = new Map();
    for (const selection of selected.values()) {
      const source = (selection.snapshot === "base" ? base : head).suites.get(selection.id);
      if (!source) continue;
      const targets = head.suites.has(selection.id) ? [selection.id] : replacements.get(selection.id) ?? [];
      if (targets.length !== 1) { diagnose("REMOVED_PROOF_UNREPLACED", "Retain the durable proof ID or declare one reviewed replacement", { snapshot: selection.snapshot, boundary: selection.id }); continue; }
      const id = targets[0], suite = head.suites.get(id);
      if (!finalSuites.has(id)) finalSuites.set(id, { ...suite, reasonIds: new Set(), baseObligations: new Set() });
      const final = finalSuites.get(id);
      for (const cause of selection.reasonIds) final.reasonIds.add(cause);
      if (selection.snapshot === "base") {
        final.baseObligations.add(selection.id);
        for (const prerequisite of source.prerequisites) reason("BASE_PREREQUISITE", { snapshot: "base", from: selection.id, to: prerequisite }, selection.reasonIds);
      }
    }
    const prerequisiteSeeds = new Set();
    for (const suite of finalSuites.values()) {
      if (head.components.get(suite.owner)?.status === "planned") diagnose("PLANNED_TARGET", "Selected proof owner must be implemented", { snapshot: "head", boundary: suite.id, candidates: [suite.owner] });
      if (suite.cwd !== "." && !head.directories.has(suite.cwd)) diagnose("MISSING_REQUIRED_PROOF", "Restore the selected suite working directory", { snapshot: "head", path: suite.cwd, boundary: suite.id });
      for (const selector of [...suite.inputs, ...suite.expectedScope.inventoryInputs]) if (!head.reader(selector)) diagnose("MISSING_REQUIRED_PROOF", "Restore the required selected test/input or map an explicit replacement", { snapshot: "head", path: selector.path, boundary: suite.id });
      for (const prerequisite of suite.prerequisites) {
        prerequisiteSeeds.add(prerequisite);
        reason("PREREQUISITE", { snapshot: "head", from: suite.id, to: prerequisite }, suite.reasonIds);
      }
      for (const inputRule of head.rulesByProof.get(suite.id) ?? []) {
        if (!head.reader(inputRule.selector)) diagnose("UNCOVERED_REQUIRED_INPUT", "Restore the required declared input for this proof", { snapshot: "head", path: inputRule.selector.path, boundary: suite.id });
      }
    }
    for (const id of affected.keys()) {
      if (!head.components.has(id)) diagnose("REMOVED_PROOF_UNREPLACED", "Map the removed component's obligations to a reviewed head owner", { snapshot: "base", candidates: [id] });
      for (const prerequisite of head.dependencies.get(id) ?? []) {
        prerequisiteSeeds.add(prerequisite);
        reason("PREREQUISITE", { snapshot: "head", from: id, to: prerequisite }, affected.get(id).reasonIds);
      }
    }
    const prerequisites = new Set(), pending = [...prerequisiteSeeds];
    for (let position = 0; position < pending.length; position++) {
      const id = pending[position]; if (prerequisites.has(id)) continue;
      prerequisites.add(id);
      if (head.components.get(id)?.status !== "implemented") diagnose("PLANNED_TARGET", "Execution prerequisite must be implemented and available", { snapshot: "head", candidates: [id] });
      for (const dependency of head.dependencies.get(id) ?? []) {
        pending.push(dependency);
        reason("PREREQUISITE", { snapshot: "head", from: id, to: dependency });
      }
    }
    plan.prerequisites = unique(prerequisites);
    const ownerTriggersByProof = new Map();
    for (const component of head.components.values()) for (const trigger of component.ownerProofs) for (const proofId of trigger.proofIds) {
      if (!ownerTriggersByProof.has(proofId)) ownerTriggersByProof.set(proofId, new Set());
      ownerTriggersByProof.get(proofId).add(component.id);
    }
    const boundariesByProof = new Map();
    for (const boundary of head.boundaries.values()) for (const proofId of boundary.proofIds) {
      if (!boundariesByProof.has(proofId)) boundariesByProof.set(proofId, []);
      boundariesByProof.get(proofId).push(boundary);
    }
    const evidenceComponents = new Set(prerequisites);
    for (const suite of finalSuites.values()) {
      const relevantRules = [...new Map([...(head.rulesByProof.get(suite.id) ?? []), ...[...new Set([suite.owner, ...(ownerTriggersByProof.get(suite.id) ?? [])])].flatMap(owner => head.rulesByOwner.get(owner) ?? []).filter(rule => ["production", "artifact", "interface", "toolchain", "runner"].includes(rule.role))].map(rule => [rule.id, rule])).values()];
      const selectors = [...suite.inputs, ...suite.expectedScope.inventoryInputs, ...relevantRules.map(rule => rule.selector)];
      const relevantEntries = ordered(new Map(selectors.flatMap(selector => head.matchingEntries(selector)).map(entry => [entry.path, entry])).values());
      if (relevantEntries.some(entry => entry.kind !== "file")) diagnose("UNSUPPORTED_INPUT_KIND", "Required proof inputs cannot traverse symlinks or gitlinks", { snapshot: "head", boundary: suite.id });
      // References into one shared head dependency-input graph avoid a closure per suite.
      const dependencyComponents = unique([...suite.prerequisites, ...(head.dependencies.get(suite.owner) ?? []), ...(boundariesByProof.get(suite.id) ?? []).flatMap(boundary => [boundary.producer, boundary.consumer]).filter(component => component !== suite.owner)]);
      for (const component of dependencyComponents) evidenceComponents.add(component);
      plan.evidenceRequirements.push({ suiteId: suite.id, cwd: suite.cwd, command: suite.command, inputRules: unique(relevantRules.map(rule => rule.id)), sourceInputs: relevantEntries, dependencyComponents, dependencyClosure: "head", manifestInputs: { suite: head.suites.get(suite.id), component: head.components.get(suite.owner) ?? null, rules: ordered(relevantRules), boundaries: ordered(boundariesByProof.get(suite.id) ?? []) }, expectedScope: suite.expectedScope, resources: suite.resources, runtimeRequirements: suite.runtime, requiresRuntimeFingerprint: true, selectionSemantics: semantics, requiresNonzeroPassingCases: true, observedResult: null, reuse: "not-evaluated" });
    }
    const pendingEvidence = [...evidenceComponents];
    for (let position = 0; position < pendingEvidence.length; position++) for (const dependency of head.dependencies.get(pendingEvidence[position]) ?? []) if (!evidenceComponents.has(dependency)) {
      evidenceComponents.add(dependency); pendingEvidence.push(dependency);
    }
    plan.dependencyInputs = ordered([...evidenceComponents].map(id => ({
      component: id, declaration: head.components.get(id) ?? null, dependencies: unique(head.dependencies.get(id) ?? []),
      sourceInputs: ordered(new Map((head.rulesByOwner.get(id) ?? []).filter(rule => ["production", "artifact", "interface", "toolchain", "runner"].includes(rule.role)).flatMap(rule => head.matchingEntries(rule.selector)).map(entry => [entry.path, entry])).values()),
    })));
    for (const dependency of plan.dependencyInputs) for (const entry of dependency.sourceInputs) if (entry.kind !== "file") diagnose("UNSUPPORTED_INPUT_KIND", "Required dependency inputs cannot traverse symlinks or gitlinks", { snapshot: "head", path: entry.path, candidates: [dependency.component] });
    plan.selectedSuites = ordered([...finalSuites.values()].map(suite => ({ ...suite, reasonIds: unique(suite.reasonIds), baseObligations: unique(suite.baseObligations) })));
    plan.requiredResources = unique(plan.selectedSuites.flatMap(suite => suite.resources));
  } catch (error) {
    if (!(error instanceof InvalidInput)) throw error;
    const { code, reason: description, ...context } = error.diagnostic;
    diagnose(code, description, context);
  }
  plan.affected = ordered([...affected.values()].map(component => ({ ...component, effects: unique(component.effects), reasonIds: unique(component.reasonIds) })));
  plan.selectedBoundaries = ordered([...selectedBoundaries.values()].map(boundary => ({ ...boundary, reasonIds: unique(boundary.reasonIds) })));
  plan.reasons = ordered([...reasons.values()].map(cause => ({ ...cause, parentReasonIds: unique(cause.parentReasonIds) })));
  plan.paths = ordered(new Map(plan.paths.map(path => [canonical(path), path])).values());
  plan.unownedPaths = unique(plan.unownedPaths);
  plan.diagnostics = ordered(diagnostics.values());
  plan.cycles = ordered(plan.cycles);
  plan.evidenceRequirements = ordered(plan.evidenceRequirements);
  plan.statistics.reasonNodes = plan.reasons.length;
  plan.disposition = plan.diagnostics.length ? "blocked" : plan.selectedSuites.length ? "executable" : plan.affected.length ? "blocked" : "no-runtime";
  return plan;
}
