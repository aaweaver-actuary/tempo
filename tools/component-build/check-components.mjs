import ts from "typescript";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, realpathSync, existsSync } from "node:fs";
import { resolve, dirname, relative, sep } from "node:path";
import { spawnSync } from "node:child_process";
import { parseSelection, readManifest, selectComponents, selectSuites, validateOwnedPaths } from "./selection.mjs";
import { checkWorker } from "./worker-qualifier.mjs";

export const repository = resolve(new URL("../../", import.meta.url).pathname);
const readJson = path => JSON.parse(readFileSync(path, "utf8"));
const inside = (root, path) => path === root || path.startsWith(root + sep);
export function validateSuite(suite, root, defaultCwd) {
  if (suite.requiresDocker !== undefined && typeof suite.requiresDocker !== "boolean") throw new Error("Invalid suite Docker requirement");
  if (!suite.owner || !suite.kind || !suite.command || !Array.isArray(suite.inputs) || !suite.inputs.length) throw new Error("Unavailable implemented suite contract");
  const cwd = resolve(root, suite.cwd ?? defaultCwd);
  if (!inside(resolve(root), cwd) || !existsSync(cwd) || !inside(realpathSync(root), realpathSync(cwd))) throw new Error("Unavailable/escaping suite cwd: " + suite.cwd);
  for (const input of suite.inputs) {
    const path = resolve(cwd, input);
    if (!inside(cwd, path) || !existsSync(path) || !inside(realpathSync(cwd), realpathSync(path))) throw new Error("Unavailable/escaping suite input: " + input);
  }
}
export function validateManifest(manifest, root = repository, options = {}) {
  if (Object.keys(manifest).some(key => !["components", "schemaVersion", "suites"].includes(key)) || manifest.schemaVersion !== 1 || !Array.isArray(manifest.components) || !manifest.components.length || (manifest.suites !== undefined && !Array.isArray(manifest.suites))) throw new Error("Invalid manifest");
  const schema = readJson(resolve(repository, "components.schema.json"));
  const fields = schema.properties.components.items;
  const identities = new Set();
  for (const component of manifest.components) {
    if (fields.required.some(key => !(key in component)) || Object.keys(component).some(key => !(key in fields.properties))) throw new Error("Missing/unknown component fields");
    for (const [key, rule] of Object.entries(fields.properties)) {
      if (!(key in component)) continue;
      const value = component[key];
      const valueType = value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
      if (rule.type && ![rule.type].flat().includes(valueType)) throw new Error("Invalid " + key);
      if (rule.enum && !rule.enum.includes(value)) throw new Error("Invalid " + key);
      if (rule.minLength && value.length < rule.minLength) throw new Error("Empty " + key);
      if (rule.items && value.some(item => typeof item !== "string")) throw new Error("Invalid array " + key);
    }
    if (identities.has(component.id)) throw new Error("Duplicate component identity");
    identities.add(component.id);
    if (component.qualification && !["board-ui", "evidence-math"].includes(component.qualification)) throw new Error("Unknown owner qualification: " + component.qualification);
    if (!inside(resolve(root), resolve(root, component.root)) || component.root.startsWith("/")) throw new Error("Source root escape");
    if (component.status === "mapped") {
      if (!component.ownershipRole || !component.ownedFiles?.length || component.artifact !== null || component.buildCommand !== null || component.tests.length) throw new Error("Invalid mapped composition contract: " + component.id);
      if (new Set(component.ownedFiles).size !== component.ownedFiles.length) throw new Error("Duplicate mapped source: " + component.id);
      for (const file of component.ownedFiles) {
        if (file.startsWith("/") || file.split("/").some(part => !part || part === "." || part === "..") || !inside(resolve(root), resolve(root, file)) || !existsSync(resolve(root, file)) || !inside(realpathSync(root), realpathSync(resolve(root, file)))) throw new Error("Unavailable/escaping mapped source: " + file);
      }
    }
    if (component.status === "implemented") {
      if (!component.buildCommand || !component.artifact?.name || !component.artifact?.version || !component.tests.length || !component.publicExports.length) throw new Error("Implemented component lacks build/export/test contract");
      if (options.componentIds && !options.componentIds.includes(component.id)) continue;
      if (!existsSync(resolve(root, component.root)) || !component.buildInputs.length) throw new Error("Unavailable implemented source/build inputs: " + component.id);
      for (const input of component.buildInputs) {
        const inputPath = resolve(root, component.root, input);
        const inputOwner = inside(resolve(root, "database"), inputPath) ? resolve(root, "database") : resolve(root, component.root);
        if (!inside(inputOwner, inputPath) || !existsSync(inputPath) || !inside(realpathSync(inputOwner), realpathSync(inputPath))) throw new Error("Unavailable/escaping build input: " + input);
      }
      for (const module of component.publicModules ?? []) {
        if (!/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$/.test(module)) throw new Error("Invalid public module: " + module);
        const path = resolve(root, component.root, "src", ...module.split("."));
        if (!existsSync(path + ".py") && !existsSync(resolve(path, "__init__.py"))) throw new Error("Missing public module: " + module);
      }
      if (component.testInventory && !existsSync(resolve(root, component.root, component.testInventory))) throw new Error("Unavailable component-local inventory");
      if (component.type === "contract") {
        const catalog = resolve(root, component.catalog ?? "contracts/catalog.json");
        if (!inside(resolve(root), catalog) || !existsSync(catalog) || !inside(realpathSync(root), realpathSync(catalog))) throw new Error("Unavailable/escaping contract catalog");
      }
      for (const suite of component.tests) validateSuite(suite, root, component.root);
    } else if (component.buildCommand || component.tests.some(test => test.status !== "planned")) throw new Error("Planned component claims executable build/test");
  }
  const suites = new Set();
  for (const suite of manifest.suites ?? []) {
    if (!suite.id || suites.has(suite.id)) throw new Error("Missing/duplicate shared suite identity");
    suites.add(suite.id);
    if (!options.componentIds || options.suiteIds?.includes(suite.id)) validateSuite(suite, root);
  }
}
function sourceFiles(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const path = resolve(root, entry.name);
    if (entry.isSymbolicLink()) throw new Error("Source symlink: " + path);
    return entry.isDirectory() ? sourceFiles(path) : /\.[cm]?tsx?$/.test(path) ? [path] : [];
  });
}
export function imports(source, path = "input.ts", strictLoaders = true) {
  const syntax = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
  const found = [];
  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) found.push(node.moduleSpecifier.text);
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === "require"))) {
      if (node.arguments.length !== 1 || !ts.isStringLiteralLike(node.arguments[0])) {
        if (strictLoaders) throw new Error("Unresolved dynamic loader: " + path);
      } else found.push(node.arguments[0].text);
    }
    if (strictLoaders && ((ts.isIdentifier(node) && ["eval", "createRequire"].includes(node.text)) || (ts.isPropertyAccessExpression(node) && ["require", "_load"].includes(node.name.text)))) throw new Error("Unresolved dynamic loader: " + path);
    ts.forEachChild(node, visit);
  }
  visit(syntax);
  return found;
}
export function checkTypeScript(root, component) {
  const sourceRoot = resolve(root, "src");
  const sourceBoundaries = [{ path: sourceRoot, realPath: realpathSync(sourceRoot), directory: true }];
  const files = sourceFiles(sourceRoot);
  const within = (boundary, path, real = false) => boundary.directory
    ? inside(real ? boundary.realPath : boundary.path, path)
    : path === (real ? boundary.realPath : boundary.path);
  const packageMetadata = readJson(resolve(root, "package.json"));
  if (packageMetadata.name !== component.artifact.name || packageMetadata.version !== component.artifact.version) throw new Error("Artifact identity differs from manifest");
  if (Object.keys(packageMetadata.dependencies ?? {}).sort().join() !== [...component.dependencies].sort().join() || Object.keys(packageMetadata.peerDependencies ?? {}).sort().join() !== [...(component.peerDependencies ?? [])].sort().join()) throw new Error("Dependencies differ from manifest");
  if (Object.keys(packageMetadata.exports ?? {}).sort().join() !== [".", ...(component.publicSubpaths ?? [])].sort().join()) throw new Error("Private exports exposed by package map");
  for (const file of files) for (const imported of imports(readFileSync(file, "utf8"), file)) {
    if (imported.startsWith(".")) {
      const target = resolve(dirname(file), imported);
      const candidates = [target, target.replace(/\.js$/, ".ts"), target.replace(/\.js$/, ".tsx"), target.replace(/\.mjs$/, ".mts"), target.replace(/\.cjs$/, ".cts"), target + ".ts", target + ".tsx", target + ".mts", target + ".cts", resolve(target, "index.ts"), resolve(target, "index.mts"), resolve(target, "index.cts")];
      if (!candidates.some(candidate => sourceBoundaries.some(boundary => within(boundary, candidate)))) throw new Error("Source escape: " + imported);
      if (!candidates.some(candidate => existsSync(candidate) && sourceBoundaries.some(boundary => within(boundary, candidate) && within(boundary, realpathSync(candidate), true)))) throw new Error("Unavailable local dependency: " + imported);
    } else {
      const packageName = imported.startsWith("@") ? imported.split("/").slice(0, 2).join("/") : imported.split("/")[0];
      if (![...component.dependencies, ...(component.peerDependencies ?? [])].includes(packageName)) throw new Error("Undeclared dependency: " + imported);
      if (!ts.resolveModuleName(imported, file, { moduleResolution: ts.ModuleResolutionKind.NodeNext, module: ts.ModuleKind.NodeNext }, ts.sys, undefined, undefined, ts.ModuleKind.ESNext).resolvedModule)
        throw new Error("Unavailable dependency: " + imported);
    }
  }
  const program = ts.createProgram(files, { strict: true, jsx: ts.JsxEmit.ReactJSX, moduleResolution: ts.ModuleResolutionKind.NodeNext, module: ts.ModuleKind.NodeNext, target: ts.ScriptTarget.ES2022, skipLibCheck: true, noEmit: true });
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, { getCanonicalFileName: name => name, getCurrentDirectory: () => root, getNewLine: () => "\n" }));
  const checker = program.getTypeChecker();
  const exported = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(resolve(sourceRoot, "index.ts")))).map(symbol => symbol.name).sort();
  if (exported.join() !== [...component.publicExports].sort().join()) throw new Error("Public exports differ from manifest: " + exported);
  return { files: files.length, exports: exported };
}
export function checkConsumer(source, artifact, exports, publicSubpaths = []) {
  for (const imported of imports(source, "consumer.ts", false)) if (imported.startsWith(artifact + "/")) {
    const subpath = "." + imported.slice(artifact.length);
    if (subpath.includes("..") || !publicSubpaths.some(allowed => allowed.endsWith("/*") ? subpath.startsWith(allowed.slice(0, -1)) && subpath.length > allowed.length - 1 : subpath === allowed)) throw new Error("Private implementation import: " + imported);
  }
  const syntax = ts.createSourceFile("consumer.ts", source, ts.ScriptTarget.Latest, true);
  for (const statement of syntax.statements) if (ts.isImportDeclaration(statement) && statement.moduleSpecifier.text === artifact) {
    const bindings = statement.importClause?.namedBindings;
    if (bindings && ts.isNamedImports(bindings)) for (const imported of bindings.elements) if (!exports.includes((imported.propertyName ?? imported.name).text)) throw new Error("Unavailable export: " + imported.name.text);
  }
}
// Finite scope: browser source/static, root executable configs, CLI and scripts.
// Test fixtures, backend and packages are outside this composition batch.
export function compositionPaths(root = repository) {
  const result = spawnSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error("Cannot enumerate tracked composition sources");
  return result.stdout.split("\0").filter(path => path === "tempo" || (!path.includes("/") && /\.(?:sh|js|mjs|cjs|py|ts|tsx)$/.test(path)) ||
    (path.startsWith("static/") && !path.endsWith(".md")) ||
    (path.startsWith("public/") && path !== "public/README.md") ||
    (path.startsWith("app/") && /\.(?:ts|tsx|js|mjs|css)$/.test(path)) ||
    (path.startsWith("scripts/") && !path.startsWith("scripts/tests/") && /\.(?:sh|js|mjs|cjs|py|ts)$/.test(path)) ||
    (path.startsWith("tools/component-build/") && !path.startsWith("tools/component-build/tests/") && /\.(?:sh|js|mjs|cjs|py|ts)$/.test(path)));
}
export function validateCompositionOwnership(manifest, paths) {
  const ids = ["study-web", "tempo-cli", "integration"];
  validateOwnedPaths(manifest, paths, ids);
  const declared = manifest.components.filter(component => ids.includes(component.id)).flatMap(component => component.ownedFiles ?? []);
  if (new Set(declared).size !== declared.length || declared.length !== paths.length || declared.some(path => !paths.includes(path))) throw new Error("Composition inventory contains duplicate or untracked source");
}

// Scope predicates enumerate tracked inputs independently of ownership. The
// component registry is the only mapping from an input to its owner.
const censusPredicates = {
  startup_composition: path => /^backend\/app\/(?:main|tasks|celery_app)[^/]*\.py$/.test(path),
  opening_admission: path => /^backend\/app\/services\/(?:queue_admission|opening_capacity_admission)[^/]*\.py$/.test(path),
  opening_kernels: path => /^backend\/app\/study\/openings\/(?:frozen_math|capacity_frontier)[^/]*\.py$/.test(path),
  lifecycle_projection: path => /^database\/tempo_storage\/lifecycle\/refresh_requests[^/]*\.py$/.test(path),
  puzzle_helpers: path => /^backend\/app\/services\/puzzles[^/]*\.py$/.test(path),
  opening_progression: path => /^database\/tempo_storage\/opening_progression[^/]*\.py$/.test(path),
  database_infrastructure: path => /^(?:database\/tempo_storage\/(?!postgres_readiness\.py$)(?:connections|infrastructure|postgres)|backend\/app\/product_composition\/database_infrastructure)[^/]*\.py$/.test(path),
  position_issuance_storage: path => /^database\/tempo_storage\/study\/(?:issuance|position_due_candidates|position_fsrs_namespace|position_introductions|position_schedule_state)[^/]*\.py$/.test(path),
  http_composition: path => /^database\/tempo_storage\/http_composition\/(?!settings\.py$).*\.py$/.test(path),
  analysis_paste: path => /^(backend\/app\/analysis_paste_commands[^/]*|backend\/app\/services\/analysis_paste[^/]*|database\/tempo_storage\/authored_repertoires\/analysis_paste[^/]*)\.py$/.test(path),
  repertoire_move_verification: path => /^(backend\/app\/services\/repertoire_move_verification[^/]*|database\/tempo_storage\/repertoire_scope\/.*)\.py$/.test(path),
  study_control: path => /^backend\/app\/services\/(?:activity_gate|study_mode|study_priority)[^/]*\.py$/.test(path),
  redis_runtime: path => /^(backend\/app\/services\/(?:redis_admission_gate|background_wakes|background_runtime)[^/]*|database\/tempo_storage\/redis_control\/.*)\.py$/.test(path),
  background_metrics: path => /^(backend\/app\/services\/background_(?:metric_kinds|diagnostic_types|metrics)[^/]*|database\/tempo_storage\/lifecycle\/background_metrics[^/]*)\.py$/.test(path),
  background_activity: path => /^(backend\/app\/services\/background_activity[^/]*|database\/tempo_storage\/controls\/activity\/.*)\.py$/.test(path),
  repertoire_snapshot: path => /^backend\/app\/repertoire\/(?:__init__|position_index|position_schedule_occurrences|snapshot|interfaces\/.*)\.py$/.test(path),
  scheduling_review: path => /^(backend\/app\/services\/(?:scheduler|scheduling_review_application|scheduling_review_contracts)[^/]*|backend\/app\/study\/policy\/(?:opening_maturity|scheduling_review_application|scheduling_review_contracts)[^/]*|database\/tempo_storage\/scheduling_review_store[^/]*)\.py$/.test(path),
  chess_helpers: path => /^backend\/app\/chess\/.*\.py$/.test(path),
  captured_tactic: path => path === "backend/app/study/interfaces/__init__.py" || /^(backend\/app\/study\/(?:interfaces\/)?captured_tactic[^/]*|database\/tempo_storage\/study\/captured_tactic[^/]*)\.py$/.test(path),
  position_source: path => /^(backend\/app\/repertoire\/position_study_source[^/]*|backend\/app\/study\/position_learning\/source_change_policy[^/]*|database\/tempo_storage\/authored_repertoires\/(?:product_snapshot|position_study_source)[^/]*|database\/tempo_storage\/study\/position_owner_epoch[^/]*)\.py$/.test(path),
  position_plan: path => /^(backend\/app\/study\/position_learning\/(?:__init__|contracts|prefix_eligibility|route_planner|issued_plan_contracts|issued_plan_service)[^/]*)\.py$/.test(path),
  position_grade_effect: path => /^(backend\/app\/study\/position_learning\/(?:daily_credit|grade_effects)[^/]*|database\/tempo_storage\/study\/position_grade_effects[^/]*)\.py$/.test(path),
  position_first_response: path => /^(backend\/app\/study\/position_learning\/first_response_(?:policy|codec)[^/]*|database\/tempo_storage\/study\/position_first_responses[^/]*)\.py$/.test(path),
  opening_evidence: path => /^(backend\/app\/study\/(?:position_checkpoint|policy\/(?:opening_decision_evidence|observation_summary))[^/]*|backend\/app\/services\/opening_decision_evidence[^/]*|database\/tempo_storage\/opening_evidence\/.*)\.py$/.test(path),
  queue_attempt_origin: path => /^(backend\/app\/queue_attempt_origins[^/]*|database\/tempo_storage\/lifecycle\/queue_attempt_(?:origins|abandonment)[^/]*)\.py$/.test(path),
  frozen_order: path => /^(backend\/app\/study\/frozen_order\/.*|backend\/app\/services\/frozen_opening_order[^/]*|database\/tempo_storage\/frozen_order[^/]*)\.py$/.test(path),
  repertoire_conflicts: path => /^(backend\/app\/studies\/(?:__init__|repertoire_conflicts)[^/]*|backend\/app\/services\/repertoire_conflicts[^/]*|database\/tempo_storage\/repertoire_lines[^/]*)\.py$/.test(path),
  study_grading: path => /^(backend\/app\/(?:services|study\/policy)\/study_grading[^/]*)\.py$/.test(path),
  study_attempt: path => /^(backend\/app\/(?:study_attempt_commands|study_exercise_routes)[^/]*|backend\/app\/services\/(?:study_attempt_guard|study_attempts)[^/]*|database\/tempo_storage\/study_attempts\/.*)\.py$/.test(path),
  queue_refresh: path => /^(backend\/app\/services\/postgres_queue_refresh[^/]*|database\/tempo_storage\/queue_refresh\/(?:__init__|foreground|reads|eligibility|openings|admissions|capacity)[^/]*)\.py$/.test(path),
  queue_application: path => /^(backend\/app\/services\/queue_application[^/]*|database\/tempo_storage\/queue_refresh\/application\/.*)\.py$/.test(path),
  queue_order: path => /^(backend\/app\/study\/(?:queue_order|policy\/queue_ordering)[^/]*|backend\/app\/services\/(?:queue_ordering|queue_presentation|queue_priority_promotion|legacy_game_evidence_scope)[^/]*|database\/tempo_storage\/queue_refresh\/presentation[^/]*)\.py$/.test(path),
  queue_commands: path => /^(backend\/app\/queue_commands[^/]*|backend\/app\/study\/queue_placement[^/]*|database\/tempo_storage\/(?:commands\/queue|queue_position_lock)[^/]*)\.py$/.test(path),
  tactics: path => /^(backend\/app\/tactic_commands[^/]*|backend\/app\/services\/(?:tactical_catalog|tactic_capture)[^/]*|backend\/app\/product_composition\/tactic_capture[^/]*|database\/tempo_storage\/(?:tactic_admission[^/]*|tactics\/.*))\.py$/.test(path),
  repertoire_controls: path => /^(backend\/app\/(?:repertoire_commands|repertoire_settings)[^/]*|database\/tempo_storage\/foreground_controls\/repertoires[^/]*)\.py$/.test(path),
  card_branch_edits: path => /^(backend\/app\/(?:card_commands|branch_commands)[^/]*|database\/tempo_storage\/authored_repertoires\/(?:branches[^/]*|card_edits\/.*))\.py$/.test(path),
  repertoire_changes: path => /^(backend\/app\/repertoire\/changes\/.*|database\/tempo_storage\/authored_repertoires\/product_changes[^/]*)\.py$/.test(path),
  canonical_prefix: path => /^(backend\/app\/services\/canonical_prefix[^/]*|database\/tempo_storage\/authored_repertoires\/canonical_prefix\/.*)\.py$/.test(path),
  card_deletion: path => /^(backend\/app\/card_deletion[^/]*|database\/tempo_storage\/authored_repertoires\/card_deletion\/.*)\.py$/.test(path),
  pgn_import: path => /^(backend\/app\/pgn_import[^/]*|backend\/app\/services\/explicit_repertoire_import[^/]*|database\/tempo_storage\/authored_repertoires\/pgn_import\/.*)\.py$/.test(path),
  opening_graph: path => /^(backend\/app\/services\/(?:postgres_opening_graph|opening_graph)[^/]*|database\/tempo_storage\/opening_graph\/.*)\.py$/.test(path),
  integrity: path => /^(backend\/app\/integrity_repair_commands[^/]*|backend\/app\/services\/(?:postgres_integrity|repertoire_integrity)[^/]*|database\/tempo_storage\/authored_repertoires\/(?:integrity_commands[^/]*|integrity\/.*))\.py$/.test(path),
  activity: path => /^(backend\/app\/activity_commands[^/]*|database\/tempo_storage\/commands\/activity[^/]*)\.py$/.test(path),
  annotation: path => /^(backend\/app\/annotation_commands[^/]*|database\/tempo_storage\/commands\/annotations[^/]*)\.py$/.test(path),
  teaching: path => /^(backend\/app\/teaching_commands[^/]*|database\/tempo_storage\/commands\/teaching[^/]*)\.py$/.test(path),
  settings: path => /^(backend\/app\/settings_commands[^/]*|database\/tempo_storage\/(?:controls|foreground_controls|http_composition)\/settings[^/]*)\.py$/.test(path),
  readiness: path => /^(backend\/app\/operations\/.*|database\/tempo_storage\/postgres_readiness[^/]*)\.py$/.test(path),
  prefix_split: path => path === "database/tempo_storage/authored_repertoires/opening_mutations/__init__.py" || /^(backend\/app\/prefix_split[^/]*|backend\/app\/services\/(?:prefix_split|deferred_prefix_split_recovery)[^/]*|database\/tempo_storage\/(?:authored_repertoires\/opening_mutations\/prefix_splits|foreground_controls\/prefix_splits|study\/deferred_prefix_splits)[^/]*)\.py$/.test(path),
  calendar: path => /^(backend\/app\/study\/policy\/clock[^/]*|backend\/app\/services\/study_calendar[^/]*)\.py$/.test(path),
  retained: path => /^(backend\/app\/(contracts|games)\/.*|backend\/app\/study\/(next_exercise|queue_serving|position_learning\/issuance)[^/]*|backend\/app\/product_composition\/study[^/]*|backend\/app\/services\/queue_next_card[^/]*|database\/tempo_storage\/study\/(next_exercise_reads|position_issued_plans)[^/]*|database\/tempo_storage\/game_review[^/]*)\.py$/.test(path),
  review: path => path === "database/tempo_storage/learning_bridge/__init__.py" || /^(backend\/app\/(command_dispatch|command_gateway|review_commands)[^/]*|backend\/app\/services\/(learning_review_adapter|review_service|review_reconciliation)[^/]*|backend\/app\/study\/policy\/review_reconciliation[^/]*|database\/tempo_storage\/(commands\/receipts\/.*|study\/review\/.*|learning_bridge\/review[^/]*|controls\/review_reconciliation[^/]*|review_reconciliation[^/]*))\.py$/.test(path),
  durable: path => /^(backend\/app\/services\/(durable_tasks|queue_refresh_wakeup)[^/]*|database\/tempo_storage\/(durable_tasks\/.*|controls\/claim_sql[^/]*))\.py$/.test(path),
};
export function censusPaths(scope, root = repository) {
  const predicate = censusPredicates[scope];
  if (!predicate) throw new Error("Unknown census scope: " + scope);
  const result = spawnSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error("Cannot enumerate tracked backend seams");
  return result.stdout.split("\0").filter(predicate);
}
export function validateCensusOwnership(manifest, scope, paths) {
  const scoped = manifest.components.filter(component => component.censusScope === scope);
  if (!scoped.length) throw new Error("Census scope lacks owners: " + scope);
  const all = manifest.components.filter(component => component.status === "mapped" && component.ownedFiles);
  for (const path of paths) {
    const owners = all.filter(component => component.ownedFiles.includes(path));
    if (owners.length !== 1 || owners[0].censusScope !== scope) throw new Error(`Expected one scoped owner for ${path}; found ${owners.length}`);
  }
  const declared = scoped.flatMap(component => component.ownedFiles ?? []);
  if (new Set(declared).size !== declared.length || declared.length !== paths.length || declared.some(path => !paths.includes(path))) throw new Error("Census inventory contains duplicate or untracked source");
}
export function validateCensusInterfaces(manifest, scope, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const owners = manifest.components.filter(component => component.censusScope === scope);
  if (!owners.length || owners.some(owner => !owner.publicExports?.length && !(scope === "startup_composition" && owner.moduleAssignments?.length))) throw new Error("Census owner lacks named interfaces");
  const references = owners.flatMap(owner => owner.publicExports.map(value => {
    const parts = value.split("#");
    if (parts.length !== 2 || !owner.ownedFiles?.includes(parts[0]) || parts[0].split("/").some(part => !part || part === "." || part === "..") || !inside(resolve(root), resolve(root, parts[0])) || !/^[A-Za-z_][A-Za-z_0-9]*$/.test(parts[1])) throw new Error("Invalid census interface: " + value);
    return { path: parts[0], symbol: parts[1] };
  }));
  const code = `import ast,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
for ref in json.loads(sys.argv[2]):
 tree=ast.parse((root/ref["path"]).read_text(),filename=ref["path"])
 names={node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
 if ref["symbol"] not in names: raise SystemExit("Missing census interface: "+ref["path"]+"#"+ref["symbol"])`;
  const result = spawnSync(python, ["-c", code, root, JSON.stringify(references)], { encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stdout + result.stderr);
}

export function validateStartupFacts(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const graph = new Map(manifest.components.map(c => [c.id, c.dependencies]));
  const visited = new Set(), active = new Set();
  function visit(id) {
    if (active.has(id)) throw new Error("Startup import dependency cycle: " + id);
    if (visited.has(id)) return;
    active.add(id);
    for (const dependency of graph.get(id) ?? []) if (graph.has(dependency)) visit(dependency);
    active.delete(id); visited.add(id);
  }
  for (const id of graph.keys()) visit(id);
  for (const [from, to] of [["api-startup-composition", "celery-startup-composition"], ["worker-startup-composition", "celery-startup-composition"], ["worker-startup-composition", "queue-refresh-wakeup"], ["queue-refresh-wakeup", "celery-startup-composition"], ["backend-command-dispatch", "celery-startup-composition"], ["postgres-readiness-composition", "celery-startup-composition"], ["celery-startup-composition", "background-wake-application"]])
    if (!graph.get(from)?.includes(to)) throw new Error("Missing startup import dependency: " + from + " -> " + to);
  const celery = manifest.components.find(c => c.id === "celery-startup-composition");
  const worker = manifest.components.find(c => c.id === "worker-startup-composition");
  const api = manifest.components.find(c => c.id === "api-startup-composition");
  const assignment = "backend/app/celery_app.py#celery_app";
  const loader = "backend/app/celery_app.py#celery-loader#worker-startup-composition#app.tasks";
  if (!celery || !worker || !api || JSON.stringify(celery.moduleAssignments) !== JSON.stringify([assignment]) || JSON.stringify(celery.lifecycleEdges) !== JSON.stringify([loader]) || !celery.ownedFiles.includes("backend/app/celery_app.py") || !worker.ownedFiles.includes("backend/app/tasks.py") || !api.ownedFiles.includes("backend/app/main.py")) throw new Error("Invalid startup assignment or loader ownership");
  if (manifest.components.some(c => c.id !== celery.id && (c.moduleAssignments?.length || c.lifecycleEdges?.length))) throw new Error("Unexpected startup assignment or loader");
  const code = `import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])

def parse(p):
    return ast.parse((root / p).read_text(), filename=p)
c = parse('backend/app/celery_app.py')
t = parse('backend/app/tasks.py')
a = parse('backend/app/main.py')
assign = [n for n in c.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and any((isinstance(x, ast.Name) and x.id == 'celery_app' for x in (n.targets if isinstance(n, ast.Assign) else [n.target])))]
if not (len(assign) == 1 and isinstance(assign[0].value, ast.Call) and isinstance(assign[0].value.func, ast.Name) and (assign[0].value.func.id == 'CoalescingCelery')):
    raise ValueError('startup witness mismatch')
if not any((k.arg == 'include' and ast.literal_eval(k.value) == ['app.tasks'] for k in assign[0].value.keywords)):
    raise ValueError('startup witness mismatch')
conf = [n.value for n in c.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and (n.value.func.attr == 'update') and isinstance(n.value.func.value, ast.Attribute) and (n.value.func.value.attr == 'conf') and isinstance(n.value.func.value.value, ast.Name) and (n.value.func.value.value.id == 'celery_app')]
if not len(conf) == 1:
    raise ValueError('startup witness mismatch')
kw = {k.arg: k.value for k in conf[0].keywords}
routes = ast.literal_eval(kw['task_routes'])
beats = ast.literal_eval(kw['beat_schedule'])
names = {'ensure_daily_queue', 'execute_foreground_command', 'execute_background_command', 'recover_operations', 'recover_study_operations', 'poll_background_tasks', 'progress_study_queue', 'execute_background_slice'}
expected = {'app.tasks.' + n for n in names}
if not set(routes) == expected:
    raise ValueError('startup witness mismatch')
if not routes == {**{'app.tasks.' + n: {'queue': 'foreground'} for n in ('execute_foreground_command', 'ensure_daily_queue', 'progress_study_queue', 'recover_study_operations')}, **{'app.tasks.' + n: {'queue': 'background'} for n in ('execute_background_command', 'execute_background_slice', 'poll_background_tasks', 'recover_operations')}}:
    raise ValueError('startup witness mismatch')
if not ({v['task'] for v in beats.values()} == {'app.tasks.' + n for n in ('poll_background_tasks', 'recover_study_operations', 'recover_operations', 'progress_study_queue', 'ensure_daily_queue')} and len(beats) == 5):
    raise ValueError('startup witness mismatch')
registered = []
for n in t.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and isinstance(d.func.value, ast.Name) and (d.func.value.id == 'celery_app') and (d.func.attr == 'task'):
                registered.extend((ast.literal_eval(k.value) for k in d.keywords if k.arg == 'name'))
if not (len(registered) == 8 and set(registered) == expected):
    raise ValueError('startup witness mismatch')
registrars = {'study_attempt_commands', 'tactic_commands', 'queue_commands', 'review_commands', 'teaching_commands', 'repertoire_commands', 'settings_commands', 'annotation_commands', 'branch_commands', 'pgn_import_commands', 'analysis_paste_commands', 'prefix_split_commands', 'card_commands', 'activity_commands', 'integrity_repair_commands'}
imports = {x.name for n in t.body if isinstance(n, ast.ImportFrom) and n.level == 1 and (n.module is None) for x in n.names}
if not registrars <= imports:
    raise ValueError('startup witness mismatch')
if not any((isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and (n.value.func.id == 'register_command') and n.value.args and isinstance(n.value.args[0], ast.Constant) and (n.value.args[0].value == 'opening_evidence.checkpoint') for n in t.body)):
    raise ValueError('startup witness mismatch')
if not any((isinstance(n, ast.AsyncFunctionDef) and n.name == 'lifespan' for n in a.body)):
    raise ValueError('startup witness mismatch')
routers = {n.value.args[0].id for n in a.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and isinstance(n.value.func.value, ast.Name) and (n.value.func.value.id == 'app') and (n.value.func.attr == 'include_router') and n.value.args and isinstance(n.value.args[0], ast.Name)}
if not {'game_review_router', 'prefix_split_router', 'study_exercise_router'} <= routers:
    raise ValueError('startup witness mismatch')
`;
  const result = spawnSync(python, ["-c", code, resolve(root)], { encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error("Startup source witness failed: " + (result.error?.message ?? result.stderr));
}

export function validateContractTooling(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const result = spawnSync("git", ["ls-files", "-z", "contracts"], {cwd: root, encoding: "utf8"});
  if (result.error || result.status !== 0) throw new Error("Cannot enumerate contract tooling");
  const tracked = result.stdout.split("\0").filter(path => /^contracts\/(?:[^/]+\.py|tests\/[^/]+\.py|consumer-examples\/src\/.*\.(?:ts|tsx|js|mjs))$/.test(path));
  const allowedRoles = {"contract-exchange-tooling": new Set(["boundary-check", "pytest-hook", "schema-export", "artifact-qualification", "suite-runner"]), "contract-exchange-tests": new Set(["test"]), "contract-typescript-consumer": new Set(["consumer-test", "consumer-expectations", "consumer-semantics"])};
  const owners = ["contract-exchange-tooling", "contract-exchange-tests", "contract-typescript-consumer"].map(id => manifest.components.find(c => c.id === id));
  if (owners.some(c => !c || c.status !== "mapped" || !Array.isArray(c.ownedFiles) || !Array.isArray(c.resourceRoles))) throw new Error("Missing contract tooling owner");
  const listed = owners.flatMap(c => c.ownedFiles), mapped = manifest.components.filter(c => c.status === "mapped" && c.ownedFiles);
  if (new Set(listed).size !== listed.length || listed.length !== tracked.length || listed.some(path => !tracked.includes(path))) throw new Error("Contract tooling inventory missing, duplicate or stale");
  for (const owner of owners) {
    const byPath = new Map();
    for (const entry of owner.resourceRoles) {
      if (typeof entry !== "string") throw new Error("Invalid contract tooling role");
      const parts = entry.split("#");
      if (parts.length !== 2 || !owner.ownedFiles.includes(parts[0]) || byPath.has(parts[0]) || !allowedRoles[owner.id].has(parts[1])) throw new Error("Invalid contract tooling role");
      byPath.set(parts[0], parts[1]);
    }
    if (byPath.size !== owner.ownedFiles.length) throw new Error("Invalid contract tooling role");
    for (const path of owner.ownedFiles) {
      if (!existsSync(resolve(root,path)) || !inside(realpathSync(root),realpathSync(resolve(root,path))) || mapped.filter(c => c.ownedFiles.includes(path)).length !== 1) throw new Error("Invalid or overlapping contract tooling owner");
      const id = path.startsWith("contracts/tests/") ? "contract-exchange-tests" : path.startsWith("contracts/consumer-examples/") ? "contract-typescript-consumer" : "contract-exchange-tooling";
      if (owner.id !== id) throw new Error("Contract tooling owner boundary changed");
    }
  }
  const code = `import ast,pathlib,sys
root=pathlib.Path(sys.argv[1])
for path in sys.argv[2:]: ast.parse((root/path).read_text(),filename=path)`;
  const check = spawnSync(python,["-c",code,resolve(root),...tracked.filter(path => path.endsWith(".py"))],{encoding:"utf8"});
  if (check.error || check.status !== 0) throw new Error("Contract tooling syntax check failed: " + (check.error?.message ?? check.stderr));
  for (const path of tracked.filter(path => /\.(?:ts|tsx|js|mjs)$/.test(path))) {
    const file = ts.createSourceFile(path, readFileSync(resolve(root,path),"utf8"), ts.ScriptTarget.Latest, true);
    if (file.parseDiagnostics.length) throw new Error("Contract consumer syntax check failed: " + path);
  }
  return {sources: tracked.length};
}

export function validateDdlResources(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const result = spawnSync("git", ["ls-files", "-z", "database/tempo_storage/ddl", "database/tooling/apply_postgres_migrations.py", "database/tooling/check-migration-inventory.mjs"], {cwd:root, encoding:"utf8"});
  if (result.error || result.status !== 0) throw new Error("Cannot enumerate DDL resources");
  const tracked = result.stdout.split("\0").filter(p => p.endsWith(".sql") || p.endsWith(".py") || p === "database/tooling/check-migration-inventory.mjs");
  const owners = ["postgres-schema-source", "postgres-migration-tooling"].map(id => manifest.components.find(c => c.id === id));
  if (owners.some(c => !c || c.status !== "mapped" || !Array.isArray(c.resourceRoles))) throw new Error("Missing DDL resource owner");
  const mapped = manifest.components.filter(c => c.status === "mapped" && c.ownedFiles);
  const roleFor = path => {
    let match = path.match(/^database\/tempo_storage\/ddl\/migrations\/(\d{3})_[^/]+\.sql$/);
    if (match) return Number(match[1]) <= 63 ? "forward-history" : "forward-recent";
    return ({"database/tempo_storage/ddl/historical/050_remediation.sql":"checksum-history", "database/tempo_storage/ddl/rollbacks/049_study_priority_recovery.sql":"rollback-maintenance", "database/tempo_storage/ddl/study_conflicts.sql":"fixture-sql", "database/tempo_storage/ddl/schema_version.py":"source-readiness", "database/tempo_storage/ddl/__init__.py":"namespace-marker", "database/tooling/apply_postgres_migrations.py":"migration-operator", "database/tooling/check-migration-inventory.mjs":"source-inventory"})[path];
  };
  const listed = owners.flatMap(c => c.ownedFiles);
  if (new Set(listed).size !== listed.length || listed.length !== tracked.length || listed.some(p => !tracked.includes(p))) throw new Error("DDL inventory missing, duplicate or stale source");
  for (const owner of owners) {
    const expected = owner.ownedFiles.map(path => path + "#" + roleFor(path));
    if (owner.resourceRoles.length !== expected.length || new Set(owner.resourceRoles).size !== expected.length || expected.some(role => role.endsWith("#undefined") || !owner.resourceRoles.includes(role))) throw new Error("Invalid DDL resource role");
    for (const path of owner.ownedFiles) {
      if (path.split("/").some(p => !p || p === "." || p === "..") || !existsSync(resolve(root,path)) || !inside(realpathSync(root),realpathSync(resolve(root,path))) || mapped.filter(c => c.ownedFiles.includes(path)).length !== 1) throw new Error("Invalid or overlapping DDL source");
      if ((path.startsWith("database/tempo_storage/ddl/") ? "postgres-schema-source" : "postgres-migration-tooling") !== owner.id) throw new Error("DDL owner boundary changed");
    }
  }
  const migrationNames = tracked.filter(p => p.startsWith("database/tempo_storage/ddl/migrations/")).map(p => p.split("/").at(-1));
  const schema = readFileSync(resolve(root,"database/tempo_storage/ddl/schema_version.py"),"utf8");
  const inventoryModule = resolve(root, "database/tooling/check-migration-inventory.mjs");
  const inventoryCode = `import {pathToFileURL} from "node:url";
let input=""; for await (const chunk of process.stdin) input+=chunk;
const {files,schema}=JSON.parse(input);
const {validateMigrationInventory}=await import(pathToFileURL(process.argv[2]).href);
process.stdout.write(String(validateMigrationInventory(files,schema)));`;
  const inventory = spawnSync(process.execPath, ["--input-type=module", "-e", inventoryCode, "ddl-source-inventory", inventoryModule], {
    cwd: root, input: JSON.stringify({files:migrationNames,schema}), encoding:"utf8"
  });
  if (inventory.error || inventory.status !== 0 || !/^\d+$/.test(inventory.stdout)) throw new Error("Migration inventory failed: " + (inventory.error?.message ?? inventory.stderr));
  const version = Number(inventory.stdout);
  const digest = createHash("sha256").update(readFileSync(resolve(root,"database/tempo_storage/ddl/historical/050_remediation.sql"))).digest("hex");
  const expectedDigest = "96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d";
  const wrapper = readFileSync(resolve(root,"database/tempo_storage/ddl/migrations/050_learning_remediation.sql"),"utf8");
  if (digest !== expectedDigest || !wrapper.includes("-- tempo-module-schema: tempo_remediation_database/ddl/migrations/001_remediation.sql\n") || !wrapper.includes("-- sha256: " + expectedDigest + "\n")) throw new Error("Historical migration resource mismatch");
  const code = `import ast,pathlib,sys
root=pathlib.Path(sys.argv[1]); tree=ast.parse((root/'database/tempo_storage/ddl/schema_version.py').read_text())
assign=[n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='POSTGRES_SCHEMA_VERSION']
if len(assign)!=1 or not isinstance(assign[0].value,ast.Constant) or type(assign[0].value.value) is not int: raise ValueError('invalid source readiness')
tool=ast.parse((root/'database/tooling/apply_postgres_migrations.py').read_text())
names={n.name for n in tool.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
entries=[n.value for n in tool.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MODULE_MIGRATIONS' for t in n.targets)]
expected={'050_learning_remediation.sql': ('tempo_remediation_database/ddl/migrations/001_remediation.sql','96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d','050_remediation.sql')}
if len(entries)!=1 or ast.literal_eval(entries[0])!=expected: raise ValueError('unregistered historical migration')
if not {'migration_source','validate_migration_history','apply_migrations','migration_status'}<=names: raise ValueError('missing migration tool interface')
`;
  const check = spawnSync(python,["-c",code,resolve(root)],{encoding:"utf8"});
  if (check.error || check.status !== 0) throw new Error("DDL source witness failed: " + (check.error?.message ?? check.stderr));
  const checker = readFileSync(resolve(root,"database/tooling/check-migration-inventory.mjs"),"utf8");
  if (!/export function validateMigrationInventory\s*\(/.test(checker)) throw new Error("Missing migration inventory interface");
  return {resources: tracked.length, version};
}

export function validateReductionTools(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const listed=spawnSync("git",["ls-files","-z","tools/reduction"],{cwd:root,encoding:"utf8"});
  if (listed.error || listed.status!==0) throw new Error("Cannot enumerate reduction tooling");
  const tracked=listed.stdout.split("\0").filter(p=>/\.(?:py|sh|js|jsx|mjs|cjs|ts|tsx)$/.test(p));
  const owner=manifest.components.find(c=>c.id==="historical-reduction-audit-tooling");
  const mapped=manifest.components.filter(c=>c.status==="mapped" && c.ownedFiles);
  if (!owner || owner.status!=="mapped" || !Array.isArray(owner.resourceRoles)) throw new Error("Missing reduction tooling owner");
  if (new Set(owner.ownedFiles).size!==owner.ownedFiles.length || owner.ownedFiles.length!==tracked.length || owner.ownedFiles.some(p=>!tracked.includes(p))) throw new Error("Reduction tooling inventory missing, duplicate or stale");
  const allowedRoles=new Set(["source-census","source-link-audit","historical-diff-audit"]), roles=new Map();
  for (const entry of owner.resourceRoles) {
    const parts=typeof entry === "string" ? entry.split("#") : [];
    if (parts.length!==2 || !owner.ownedFiles.includes(parts[0]) || !allowedRoles.has(parts[1]) || roles.has(parts[0])) throw new Error("Invalid reduction tooling role");
    roles.set(parts[0],parts[1]);
  }
  if (roles.size!==tracked.length || tracked.some(p=>!roles.has(p) || !p.endsWith(".py"))) throw new Error("Invalid reduction tooling role");
  for (const p of tracked) if (p.split("/").some(x=>!x || x==="." || x==="..") || !existsSync(resolve(root,p)) || !inside(realpathSync(root),realpathSync(resolve(root,p))) || mapped.filter(c=>c.ownedFiles.includes(p)).length!==1) throw new Error("Invalid or overlapping reduction tooling source");
  const code=`import ast,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
for p in json.loads(sys.argv[2]): ast.parse((root/p).read_text(),filename=p)
`;
  const parsed=spawnSync(python,["-c",code,resolve(root),JSON.stringify(tracked)],{encoding:"utf8"});
  if (parsed.error || parsed.status!==0) throw new Error("Reduction tooling source parse failed: "+(parsed.error?.message ?? parsed.stderr));
  return {files:tracked.length};
}

export function validateDatabaseCliTools(manifest, root = repository) {
  const listed=spawnSync("git",["ls-files","-z","database/tooling/cli"],{cwd:root,encoding:"utf8"});
  if (listed.error || listed.status!==0) throw new Error("Cannot enumerate database CLI tools");
  const tracked=listed.stdout.split("\0").filter(p=>/\.(?:py|sh|js|jsx|mjs|cjs|ts|tsx)$/.test(p));
  const ids=["database-cli-backup-operations","database-cli-probes"], owners=ids.map(id=>manifest.components.find(c=>c.id===id));
  if (owners.some(c=>!c || c.status!=="mapped" || !Array.isArray(c.resourceRoles) || !Array.isArray(c.ownedFiles))) throw new Error("Missing database CLI owner");
  const mapped=manifest.components.filter(c=>c.status==="mapped" && c.ownedFiles), files=owners.flatMap(c=>c.ownedFiles);
  if (new Set(files).size!==files.length || files.length!==tracked.length || files.some(p=>!tracked.includes(p))) throw new Error("Database CLI inventory missing, duplicate or stale");
  const roles=new Set(["cli-backup-operations","cli-diagnostic-probes"]);
  for (const owner of owners) {
    const roleMap=new Map();
    for(const entry of owner.resourceRoles) {
      const parts=typeof entry==="string" ? entry.split("#") : [];
      if(parts.length!==2 || !owner.ownedFiles.includes(parts[0]) || !roles.has(parts[1]) || roleMap.has(parts[0])) throw new Error("Invalid database CLI role");
      roleMap.set(parts[0],parts[1]);
    }
    if(roleMap.size!==owner.ownedFiles.length) throw new Error("Invalid database CLI role");
    const found=[];
    for(const path of owner.ownedFiles) {
      if(!path.endsWith(".mjs") || path.split("/").some(x=>!x || x==="." || x==="..") || !existsSync(resolve(root,path)) || !inside(realpathSync(root),realpathSync(resolve(root,path))) || mapped.filter(c=>c.ownedFiles.includes(path)).length!==1) throw new Error("Invalid or overlapping database CLI source");
      const source=ts.createSourceFile(path,readFileSync(resolve(root,path),"utf8"),ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
      if(source.parseDiagnostics.length) throw new Error("Database CLI source syntax invalid: "+path);
      for(const statement of source.statements) {
        if(!ts.isFunctionDeclaration(statement) || !statement.name || !statement.modifiers?.some(m=>m.kind===ts.SyntaxKind.ExportKeyword)) throw new Error("Database CLI source has top-level effect or unsupported declaration: "+path);
        found.push(path+"#"+statement.name.text);
      }
    }
    if(new Set(found).size!==found.length || owner.publicExports.length!==found.length || found.some(name=>!owner.publicExports.includes(name))) throw new Error("Database CLI exported interface mismatch");
  }
  if(!manifest.components.find(c=>c.id==="tempo-cli")?.dependencies.includes("database-cli-backup-operations") || !manifest.components.find(c=>c.id==="tempo-cli")?.dependencies.includes("database-cli-probes")) throw new Error("Missing database CLI composition dependency");
  return {sources:tracked.length};
}

export function checkComponents(root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3", options = {}) {
  const manifest = readManifest(root);
  const selected = selectComponents(manifest, options.componentIds);
  if (root === repository && options.componentIds === undefined) {
    validateCompositionOwnership(manifest, compositionPaths(root));
    for (const scope of Object.keys(censusPredicates)) {
      validateCensusOwnership(manifest, scope, censusPaths(scope, root));
      validateCensusInterfaces(manifest, scope, root, python);
    }
  }
  if (root === repository && options.componentIds === undefined) {
    validateStartupFacts(manifest, root);
    validateDdlResources(manifest, root, python);
    validateReductionTools(manifest, root, python);
    validateDatabaseCliTools(manifest, root);
    validateContractTooling(manifest, root, python);
  }
  validateManifest(manifest, root, { componentIds: selected.map(component => component.id), suiteIds: options.componentIds === undefined ? manifest.suites?.map(suite => suite.id) : undefined });
  const results = [];
  for (const component of selected) {
    const componentRoot = resolve(root, component.root);
    if (component.type === "worker") results.push({ id: component.id, ...checkWorker(root, component) });
    else if (existsSync(resolve(componentRoot, "package.json"))) results.push({ id: component.id, ...checkTypeScript(componentRoot, component, root) });
    else {
      const argumentsList = component.type === "contract"
        ? [resolve(repository, "tools/component-build/check-contract.py"), componentRoot, JSON.stringify(component), resolve(root, component.catalog ?? "contracts/catalog.json")]
        : [resolve(repository, "tools/component-build/check-python.py"), componentRoot, JSON.stringify(component.dependencies), JSON.stringify(component.publicExports), JSON.stringify(component.artifact), JSON.stringify(component.buildInputs)];
      const result = spawnSync(python, argumentsList, { encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
      if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stdout + result.stderr);
      results.push({ id: component.id, ...JSON.parse(result.stdout) });
    }
  }
  if (!(options.consumers ?? options.componentIds === undefined)) return results;
  for (const file of sourceFiles(resolve(root, "app"))) {
    const source = readFileSync(file, "utf8");
    for (const component of selected.filter(candidate => candidate.artifact?.name?.startsWith("@tempo/")))
      checkConsumer(source, component.artifact.name, component.publicExports, component.publicSubpaths);
    for (const imported of imports(source, file, false)) if (imported.startsWith(".") && inside(resolve(root, "packages"), resolve(dirname(file), imported))) throw new Error("Consumer bypasses public package: " + file);
  }
  const consumerCheck = spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), "consumers", resolve(root, "backend/app"), "tempo_scheduling", JSON.stringify(manifest.components.find(component => component.id === "scheduling").publicExports)], { encoding: "utf8" });
  if (consumerCheck.status !== 0) throw new Error(consumerCheck.stdout + consumerCheck.stderr);
  return results;
}
if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  try {
    const options = parseSelection(process.argv.slice(2));
    if (options.positional.length) throw new Error("Unexpected positional argument");
    const root = process.env.TEMPO_COMPONENT_ROOT ?? repository;
    if (options.list) {
      const manifest = readManifest(root);
      const selected = options.suiteIds ? selectSuites(manifest, options.suiteIds) : selectComponents(manifest, options.componentIds);
      if (root === repository && options.componentIds === undefined) {
        validateStartupFacts(manifest, root);
        validateDdlResources(manifest, root);
        validateReductionTools(manifest, root);
        validateDatabaseCliTools(manifest, root);
        validateContractTooling(manifest, root);
      }
      validateManifest(manifest, root, { componentIds: options.suiteIds ? [] : selected.map(component => component.id), suiteIds: options.suiteIds });
      console.log(JSON.stringify({ selected }, null, 2));
    } else {
      if (options.suiteIds) throw new Error("Use test-lane.mjs to execute shared suites");
      console.log(JSON.stringify(checkComponents(root, undefined, options), null, 2));
    }
  }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
