import ts from "typescript";
import { readFileSync, readdirSync, realpathSync, existsSync } from "node:fs";
import { resolve, dirname, sep } from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

export const repository = resolve(new URL("../../", import.meta.url).pathname);
const readJson = path => JSON.parse(readFileSync(path, "utf8"));
const inside = (root, path) => path === root || path.startsWith(root + sep);
export function validateManifest(manifest, root = repository) {
  if (Object.keys(manifest).sort().join() !== "components,schemaVersion" || manifest.schemaVersion !== 1 || !Array.isArray(manifest.components) || !manifest.components.length) throw new Error("Invalid manifest");
  const schema = readJson(resolve(repository, "components.schema.json"));
  const fields = schema.properties.components.items;
  const identities = new Set();
  for (const component of manifest.components) {
    if (fields.required.some(key => !(key in component)) || Object.keys(component).some(key => !(key in fields.properties))) throw new Error("Missing/unknown component fields");
    for (const [key, rule] of Object.entries(fields.properties)) {
      const value = component[key];
      const valueType = value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
      if (rule.type && ![rule.type].flat().includes(valueType)) throw new Error("Invalid " + key);
      if (rule.enum && !rule.enum.includes(value)) throw new Error("Invalid " + key);
      if (rule.minLength && value.length < rule.minLength) throw new Error("Empty " + key);
      if (rule.items && value.some(item => typeof item !== "string")) throw new Error("Invalid array " + key);
    }
    if (identities.has(component.id)) throw new Error("Duplicate component identity");
    identities.add(component.id);
    if (!inside(resolve(root), resolve(root, component.root)) || component.root.startsWith("/")) throw new Error("Source root escape");
    if (component.status === "implemented") {
      if (!component.buildCommand || !component.artifact?.name || !component.artifact?.version || !component.tests.length || !component.publicExports.length) throw new Error("Implemented component lacks build/export/test contract");
      for (const input of component.buildInputs) {
        const inputPath = resolve(root, component.root, input);
        if (!inside(resolve(root, component.root), inputPath) || !existsSync(inputPath) || !inside(realpathSync(resolve(root, component.root)), realpathSync(inputPath))) throw new Error("Unavailable/escaping build input: " + input);
      }
    } else if (component.buildCommand || component.tests.some(test => test.status !== "planned")) throw new Error("Planned component claims executable build/test");
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
  const files = sourceFiles(sourceRoot);
  const packageMetadata = readJson(resolve(root, "package.json"));
  if (packageMetadata.name !== component.artifact.name || packageMetadata.version !== component.artifact.version) throw new Error("Artifact identity differs from manifest");
  if (Object.keys(packageMetadata.dependencies ?? {}).sort().join() !== [...component.dependencies].sort().join()) throw new Error("Dependencies differ from manifest");
  if (Object.keys(packageMetadata.exports ?? {}).join() !== ".") throw new Error("Private exports exposed by package map");
  for (const file of files) for (const imported of imports(readFileSync(file, "utf8"), file)) {
    if (imported.startsWith(".")) {
      const target = resolve(dirname(file), imported);
      if (!inside(sourceRoot, target)) throw new Error("Source escape: " + imported);
      const candidates = [target, target.replace(/\.js$/, ".ts"), target + ".ts", resolve(target, "index.ts")];
      if (!candidates.some(candidate => existsSync(candidate) && inside(sourceRoot, realpathSync(candidate)))) throw new Error("Unavailable local dependency: " + imported);
    } else {
      const packageName = imported.startsWith("@") ? imported.split("/").slice(0, 2).join("/") : imported.split("/")[0];
      if (!component.dependencies.includes(packageName)) throw new Error("Undeclared dependency: " + imported);
      createRequire(resolve(root, "package.json")).resolve(imported);
    }
  }
  const program = ts.createProgram(files, { strict: true, moduleResolution: ts.ModuleResolutionKind.NodeNext, module: ts.ModuleKind.NodeNext, target: ts.ScriptTarget.ES2022, skipLibCheck: true, noEmit: true });
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, { getCanonicalFileName: name => name, getCurrentDirectory: () => root, getNewLine: () => "\n" }));
  const checker = program.getTypeChecker();
  const exported = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(resolve(sourceRoot, "index.ts")))).map(symbol => symbol.name).sort();
  if (exported.join() !== [...component.publicExports].sort().join()) throw new Error("Public exports differ from manifest: " + exported);
  return { files: files.length, exports: exported };
}
export function checkConsumer(source, artifact, exports) {
  for (const imported of imports(source, "consumer.ts", false)) if (imported.startsWith(artifact + "/")) throw new Error("Private implementation import: " + imported);
  const syntax = ts.createSourceFile("consumer.ts", source, ts.ScriptTarget.Latest, true);
  for (const statement of syntax.statements) if (ts.isImportDeclaration(statement) && statement.moduleSpecifier.text === artifact) {
    const bindings = statement.importClause?.namedBindings;
    if (bindings && ts.isNamedImports(bindings)) for (const imported of bindings.elements) if (!exports.includes((imported.propertyName ?? imported.name).text)) throw new Error("Unavailable export: " + imported.name.text);
  }
}
export function checkComponents(root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  const manifest = readJson(resolve(root, "components.json"));
  validateManifest(manifest, root);
  const results = [];
  for (const component of manifest.components.filter(component => component.status === "implemented")) {
    const componentRoot = resolve(root, component.root);
    if (existsSync(resolve(componentRoot, "package.json"))) results.push({ id: component.id, ...checkTypeScript(componentRoot, component) });
    else {
      const result = spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), componentRoot, JSON.stringify(component.dependencies), JSON.stringify(component.publicExports)], { encoding: "utf8" });
      if (result.status !== 0) throw new Error(result.stdout + result.stderr);
      results.push({ id: component.id, ...JSON.parse(result.stdout) });
    }
  }
  for (const file of sourceFiles(resolve(root, "app"))) {
    const source = readFileSync(file, "utf8");
    checkConsumer(source, "@tempo/chess-line", manifest.components.find(component => component.id === "chess-line").publicExports);
    for (const imported of imports(source, file, false)) if (imported.startsWith(".") && inside(resolve(root, "packages"), resolve(dirname(file), imported))) throw new Error("Consumer bypasses public package: " + file);
  }
  const consumerCheck = spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), "consumers", resolve(root, "backend/app"), "tempo_scheduling", JSON.stringify(manifest.components.find(component => component.id === "scheduling").publicExports)], { encoding: "utf8" });
  if (consumerCheck.status !== 0) throw new Error(consumerCheck.stdout + consumerCheck.stderr);
  return results;
}
if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  try { console.log(JSON.stringify(checkComponents(), null, 2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
