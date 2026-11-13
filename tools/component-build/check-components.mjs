import ts from "typescript";
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

// Named retained seams only. This is a selected source set, not a claim that
// all backend/storage files have been classified or isolated.
export const retainedBackendOwnerIds = ["backend-contracts", "study-next-core", "study-queue-composition", "study-queue-preparation", "study-next-storage", "study-position-issuance", "study-position-storage", "game-review-policy", "game-review-http", "game-review-storage"];
export function retainedBackendPaths(root = repository) {
  const result = spawnSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error("Cannot enumerate tracked backend seams");
  const exact = new Set([
    "backend/app/study/next_exercise.py", "backend/app/study/queue_serving.py",
    "backend/app/product_composition/study.py", "backend/app/services/queue_next_card.py",
    "backend/app/study/position_learning/issuance.py", "database/tempo_storage/study/next_exercise_reads.py",
    "database/tempo_storage/study/position_issued_plans.py", "database/tempo_storage/game_review.py",
  ]);
  return result.stdout.split("\0").filter(path => exact.has(path) ||
    ((path.startsWith("backend/app/contracts/") || path.startsWith("backend/app/games/")) && path.endsWith(".py")));
}
export function validateRetainedBackendOwnership(manifest, paths) {
  validateOwnedPaths(manifest, paths, retainedBackendOwnerIds);
  const declared = manifest.components.filter(component => retainedBackendOwnerIds.includes(component.id)).flatMap(component => component.ownedFiles ?? []);
  if (new Set(declared).size !== declared.length || declared.length !== paths.length || declared.some(path => !paths.includes(path))) throw new Error("Retained backend seam inventory contains duplicate or untracked source");
}
export function validateRetainedBackendInterfaces(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3", ownerIds = retainedBackendOwnerIds) {
  const owners = manifest.components.filter(component => ownerIds.includes(component.id));
  if (owners.length !== ownerIds.length || owners.some(owner => !owner.publicExports?.length)) throw new Error("Retained backend owner lacks named interfaces");
  const references = owners.flatMap(owner => owner.publicExports.map(value => {
    const parts = value.split("#");
    if (parts.length !== 2 || !owner.ownedFiles?.includes(parts[0]) || parts[0].split("/").some(part => !part || part === "." || part === "..") || !inside(resolve(root), resolve(root, parts[0])) || !/^[A-Za-z_][A-Za-z_0-9]*$/.test(parts[1])) throw new Error("Invalid retained interface: " + value);
    return { path: parts[0], symbol: parts[1] };
  }));
  const code = `import ast,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
for ref in json.loads(sys.argv[2]):
 tree=ast.parse((root/ref["path"]).read_text(),filename=ref["path"])
 names={node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
 if ref["symbol"] not in names: raise SystemExit("Missing retained interface: "+ref["path"]+"#"+ref["symbol"])`;
  const result = spawnSync(python, ["-c", code, root, JSON.stringify(references)], { encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stdout + result.stderr);
}

// Selected review/command seams only; this is not a backend-wide inventory.
export const reviewCommandOwnerIds = ["backend-command-dispatch", "backend-command-gateway", "operation-receipt-storage", "study-review-command", "study-review-adapter", "study-review-scheduling", "study-review-reconciliation", "study-review-bridge-storage", "study-review-storage", "study-review-reconciliation-storage", "study-review-savepoint-storage"];
export const reviewCommandFiles = ["backend/app/command_dispatch.py", "backend/app/command_gateway.py", "database/tempo_storage/commands/receipts/storage.py", "backend/app/review_commands.py", "backend/app/services/learning_review_adapter.py", "backend/app/services/review_service.py", "backend/app/services/review_reconciliation.py", "backend/app/study/policy/review_reconciliation.py", "database/tempo_storage/learning_bridge/review.py", "database/tempo_storage/study/review/storage.py", "database/tempo_storage/review_reconciliation.py", "database/tempo_storage/controls/review_reconciliation.py"];
export function reviewCommandPaths(root = repository) {
  const result = spawnSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error("Cannot enumerate tracked review seams");
  return result.stdout.split("\0").filter(path => reviewCommandFiles.includes(path));
}
export function validateReviewCommandOwnership(manifest, paths) {
  validateOwnedPaths(manifest, paths, [...retainedBackendOwnerIds, ...reviewCommandOwnerIds]);
  const declared = manifest.components.filter(component => reviewCommandOwnerIds.includes(component.id)).flatMap(component => component.ownedFiles ?? []);
  if (new Set(declared).size !== declared.length || declared.length !== paths.length || declared.some(path => !paths.includes(path))) throw new Error("Review seam inventory contains duplicate or untracked source");
}

export function checkComponents(root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3", options = {}) {
  const manifest = readManifest(root);
  const selected = selectComponents(manifest, options.componentIds);
  if (root === repository && options.componentIds === undefined) {
    validateCompositionOwnership(manifest, compositionPaths(root));
    validateRetainedBackendOwnership(manifest, retainedBackendPaths(root));
    validateRetainedBackendInterfaces(manifest, root, python);
    validateReviewCommandOwnership(manifest, reviewCommandPaths(root));
    validateRetainedBackendInterfaces(manifest, root, python, reviewCommandOwnerIds);
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
      validateManifest(manifest, root, { componentIds: options.suiteIds ? [] : selected.map(component => component.id), suiteIds: options.suiteIds });
      console.log(JSON.stringify({ selected }, null, 2));
    } else {
      if (options.suiteIds) throw new Error("Use test-lane.mjs to execute shared suites");
      console.log(JSON.stringify(checkComponents(root, undefined, options), null, 2));
    }
  }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
