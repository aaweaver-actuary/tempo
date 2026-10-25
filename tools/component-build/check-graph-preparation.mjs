// Named source boundaries for graph -> fresh Learning -> Study preparation.
// No plugin framework, service probe, history migration or cutover qualification.
import ts from "typescript";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, resolve, relative } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { imports, checkConsumer } from "./check-components.mjs";

const directory = dirname(fileURLToPath(import.meta.url));
const repository = resolve(directory, "../..");
const deployment = "./tempo-deployment.mjs";
const rules = {
  "scripts/tempo-cutover-plan.mjs": { builtins: ["node:util", "node:path"], public: ["targetKey"] },
  "scripts/tempo-cutover-transition.mjs": { builtins: ["node:crypto", "node:fs", "node:path", "node:util"], public: ["acquireTargetLock", "atomicJson"] },
  "scripts/tempo-cutover-readiness.mjs": { builtins: ["node:crypto"], public: [] },
};
const pythonSources = ["database/tooling/opening_graph_transfer.py", "database/tooling/opening_graph_fresh_learning.py"];
export const graphPreparationSources = [...pythonSources, ...Object.keys(rules)];

export function checkJavaScriptSource(path, source) {
  const rule = rules[path];
  if (!rule) throw new Error("Unknown graph preparation source: " + path);
  for (const imported of imports(source, path)) {
    if (!rule.builtins.includes(imported) && !(imported === deployment && rule.public.length))
      throw new Error(`Undeclared graph preparation import: ${path} -> ${imported}`);
  }
  checkConsumer(source, deployment, rule.public);
  const syntax = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  if (syntax.parseDiagnostics.length) throw new Error("Invalid graph preparation source: " + path);
  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier?.text === deployment) {
      const bindings = ts.isImportDeclaration(node) ? node.importClause?.namedBindings : node.exportClause;
      if (!bindings || !(ts.isNamedImports(bindings) || ts.isNamedExports(bindings)) || node.importClause?.name
        || bindings.elements.some(binding => !rule.public.includes((binding.propertyName ?? binding.name).text)))
        throw new Error("Named public deployment operations required: " + path);
    }
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword
      || (ts.isIdentifier(node.expression) && node.expression.text === "require")) && node.arguments[0]?.text === deployment)
      throw new Error("Named public deployment operations required: " + path);
    ts.forEachChild(node, visit);
  }
  visit(syntax);
}

export function checkPythonSources(sources, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const result = spawnSync(python, [resolve(directory, "check-graph-preparation.py")], {
    input: JSON.stringify(sources), encoding: "utf8", timeout: 10_000,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stdout + result.stderr);
}

export function checkGraphPreparation(root = repository, selected) {
  root = realpathSync(root);
  const present = selected ?? graphPreparationSources.filter(path => existsSync(resolve(root, path)));
  if (selected?.length === 0) throw new Error("Empty graph preparation selection");
  const python = {}, dependencies = new Set();
  for (const path of present) {
    if (!graphPreparationSources.includes(path)) throw new Error("Unknown graph preparation source: " + path);
    const file = resolve(root, path);
    if (!existsSync(file) || realpathSync(file) !== file) throw new Error("Missing or symlinked graph preparation source: " + path);
    const source = readFileSync(file, "utf8");
    if (path.endsWith(".py")) python[path] = source;
    else {
      checkJavaScriptSource(path, source);
      if (imports(source, path).includes(deployment)) dependencies.add("scripts/tempo-deployment.mjs");
    }
  }
  if (Object.keys(python).length) checkPythonSources(python);
  // Follow only the already permitted public adapter closure. A new helper may
  // never become a dependency of that adapter, directly or through a bridge.
  const visited = new Set(), active = new Set();
  function checkDependency(path) {
    if (graphPreparationSources.includes(path)) throw new Error("Preparation dependency points backward: " + path);
    if (active.has(path)) throw new Error("Public dependency cycle: " + path);
    if (visited.has(path)) return;
    active.add(path);
    const file = resolve(root, path);
    if (!existsSync(file) || realpathSync(file) !== file) throw new Error("Missing or symlinked public dependency: " + path);
    for (const imported of imports(readFileSync(file, "utf8"), path)) if (imported.startsWith(".")) {
      const target = relative(root, resolve(dirname(file), imported));
      if (target.startsWith("../")) throw new Error("Public dependency source escape: " + path);
      checkDependency(target);
    }
    active.delete(path);
    visited.add(path);
  }
  for (const path of dependencies) checkDependency(path);
  return { checked: present, absent: graphPreparationSources.filter(path => !present.includes(path)) };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(checkGraphPreparation(process.env.TEMPO_COMPONENT_ROOT ?? repository, process.argv.length > 2 ? process.argv.slice(2) : undefined))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
