/** Cheap component-owned interface/DAG/SQL/intake checks. Application code is never loaded. */
import ts from "typescript";
import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { dirname, extname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { imports as typescriptImports, repository } from "./check-components.mjs";

const inside = (root, path) => path === root || path.startsWith(root + sep);
const code = /\.(?:py|[cm]?[jt]sx?)$/;
const infrastructure = new Set(["psycopg", "psycopg_pool", "sqlite3", "redis", "celery", "fastapi", "httpx", "requests", "subprocess", "fs", "fs/promises", "net", "http", "https", "tls", "child_process", "worker_threads", "pg", "better-sqlite3"]);
const glob = pattern => new RegExp("^" + pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replaceAll("*", ".*") + "$");

function filesUnder(path) {
  if (realpathSync(path) !== path) throw new Error("Source symlink/escape: " + path);
  if (!statSync(path).isDirectory()) return code.test(path) ? [path] : [];
  return readdirSync(path, { withFileTypes: true }).flatMap(entry => {
    if (["__pycache__", "node_modules", ".git", ".venv", "dist", "build"].includes(entry.name)) return [];
    return filesUnder(resolve(path, entry.name));
  });
}

function typescriptFacts(source, path) {
  const syntax = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
  if (syntax.parseDiagnostics.length) throw new Error("Invalid source syntax: " + path + ": " + ts.flattenDiagnosticMessageText(syntax.parseDiagnostics[0].messageText, " "));
  const aliases = new Map(), constants = new Map(), calls = [], references = [], functions = [], exported = [];
  const line = node => syntax.getLineAndCharacterOfPosition(node.getStart(syntax)).line + 1;
  const found = typescriptImports(source, path).map(importPath => ({ module: importPath, names: [], level: 0, line: 1 }));
  function binding(node) {
    if (ts.isImportDeclaration(node)) {
      const importPath = node.moduleSpecifier.text, names = node.importClause?.namedBindings;
      if (names && ts.isNamedImports(names)) {
        for (const item of names.elements) aliases.set(item.name.text, importPath + "#" + (item.propertyName ?? item.name).text);
        found.find(item => item.module === importPath).names = names.elements.map(item => (item.propertyName ?? item.name).text);
      } else if (names && ts.isNamespaceImport(names)) aliases.set(names.name.text, importPath + "#");
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) constants.set(node.name.text, node.initializer);
    ts.forEachChild(node, binding);
  }
  binding(syntax);
  function stringValue(node, seen = new Set()) {
    if (ts.isStringLiteralLike(node)) return node.text;
    if (ts.isIdentifier(node) && constants.has(node.text) && !seen.has(node.text)) return stringValue(constants.get(node.text), new Set([...seen, node.text]));
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
      const left = stringValue(node.left, seen), right = stringValue(node.right, seen);
      return left !== null && right !== null ? left + right : null;
    }
    return null;
  }
  function dotted(node) {
    if (node.kind === ts.SyntaxKind.ThisKeyword) return "this";
    if (ts.isIdentifier(node)) return aliases.get(node.text) ?? node.text;
    if (ts.isPropertyAccessExpression(node)) return dotted(node.expression) + "." + node.name.text;
    return "<dynamic>";
  }
  function visit(node, scope = []) {
    const functionNode = ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isArrowFunction(node) || ts.isFunctionExpression(node);
    const name = node.name?.getText(syntax) ?? (functionNode && ts.isVariableDeclaration(node.parent) ? node.parent.name.getText(syntax) : null);
    const next = name && (functionNode || ts.isClassDeclaration(node)) ? [...scope, name] : scope;
    if (functionNode && name) functions.push(next.join("."));
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      let callee = dotted(node.expression);
      if (callee.startsWith("this.") && next.length > 1) callee = next[0] + callee.slice(4);
      const call = { callee, function: next.join("."), line: line(node) };
      if (["execute", "execute_native", "executemany", "executescript", "query", "prepare"].includes(callee.split(".").at(-1)) && node.arguments?.length) {
        const sql = stringValue(node.arguments[0]);
        if (callee.toLowerCase().split(".").some(name => ["db", "database", "connection", "conn", "cursor", "pool", "writer"].includes(name)) || sql !== null && /\b(?:SELECT|WITH|INSERT|UPDATE|DELETE|TRUNCATE|MERGE|REPLACE|COPY|CREATE|ALTER|DROP)\b/i.test(sql)) call.sql = sql;
      }
      calls.push(call);
    }
    if (ts.isPropertyAccessExpression(node)) references.push({ callee: dotted(node), function: next.join("."), line: line(node) });
    if (node.modifiers?.some(item => item.kind === ts.SyntaxKind.ExportKeyword)) {
      if (node.name) exported.push(node.name.getText(syntax));
      if (ts.isVariableStatement(node)) for (const item of node.declarationList.declarations) exported.push(item.name.getText(syntax));
    }
    if (ts.isExportDeclaration(node)) {
      if (!node.exportClause) exported.push("*");
      else if (ts.isNamedExports(node.exportClause)) exported.push(...node.exportClause.elements.map(item => item.name.text));
    }
    ts.forEachChild(node, child => visit(child, next));
  }
  visit(syntax);
  return { path, imports: found, functions, calls, references, exports: exported, loaders: [] };
}

/** Small declarative ownership input supplied by the integration owner, not a runtime registry. */
export function checkInterfaceBoundaries(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  root = realpathSync(root);
  if (manifest.mode === "selected-python") {
    const result = checkSelectedPython(manifest, root, python);
    return manifest.frontend === undefined ? result : { ...result, frontend: checkSelectedFrontend(manifest.frontend, root) };
  }
  if (!Array.isArray(manifest.components) || !manifest.components.length) throw new Error("Missing component ownership map");
  const components = manifest.components.map(component => ({ ...component })), problems = [], allFiles = new Set(), ids = new Set(), tables = new Map(), modulePaths = new Map();
  const absolute = path => {
    if (typeof path !== "string" || path.startsWith("/") || !inside(root, resolve(root, path))) throw new Error("Ownership path escape: " + path);
    return resolve(root, path);
  };
  for (const component of components) {
    if (!component.id || ids.has(component.id) || !component.sources?.length || !Array.isArray(component.interfaces) || !Array.isArray(component.dependencies)) throw new Error("Invalid/duplicate component ownership");
    if (!component.composition && !component.interfaces.length) throw new Error("Component lacks a designated public interface: " + component.id);
    ids.add(component.id);
    component.roots = component.sources.map(absolute);
    component.publicRoots = component.interfaces.map(absolute);
    component.facadeTargets = new Map();
    for (const facade of component.facades ?? []) {
      if (!facade || typeof facade.path !== "string" || !Array.isArray(facade.targets) || !facade.targets.length) throw new Error("Invalid facade declaration");
      const path = absolute(facade.path);
      if (!component.publicRoots.includes(path) || component.facadeTargets.has(path)) throw new Error("Facade must be a unique designated interface: " + path);
      const targets = facade.targets.map(absolute);
      for (const target of targets) if (!component.roots.some(owner => inside(owner, target)) || !existsSync(target) || !statSync(target).isFile() || realpathSync(target) !== target) throw new Error("Unavailable/unowned facade target: " + target);
      component.facadeTargets.set(path, new Set(targets));
    }
    for (const path of component.publicRoots) {
      if (!component.roots.some(owner => inside(owner, path)) || !existsSync(path)) throw new Error("Unavailable/unowned interface: " + path);
      if (statSync(path).isDirectory() && component.roots.includes(path)) throw new Error("Entire implementation directory cannot be a public interface");
      if (!existsSync(resolve(statSync(path).isDirectory() ? path : dirname(path), "README.md"))) throw new Error("Interface README missing: " + path);
    }
    for (const path of component.roots) {
      if (!existsSync(path)) throw new Error("Unavailable source: " + path);
      for (const file of filesUnder(path)) allFiles.add(file);
    }
    for (const [name, path] of Object.entries(component.modules ?? {})) {
      if (modulePaths.has(name)) throw new Error("Duplicate import prefix: " + name);
      const resolved = absolute(path);
      if (!component.roots.some(owner => inside(owner, resolved))) throw new Error("Import prefix escapes owner: " + name);
      modulePaths.set(name, resolved);
    }
    for (const table of component.tables ?? []) {
      const key = table.toLowerCase();
      if (tables.has(key)) throw new Error("Duplicate table owner: " + table);
      tables.set(key, component.id);
    }
  }
  function ownerOf(path) {
    const found = components.flatMap(component => component.roots.filter(owner => inside(owner, path)).map(owner => ({ component, length: owner.length }))).sort((a, b) => b.length - a.length);
    if (found.length > 1 && found[0].length === found[1].length) throw new Error("Ambiguous source owner: " + path);
    return found[0]?.component;
  }
  for (const component of components) {
    if (![...allFiles].some(file => ownerOf(file).id === component.id)) throw new Error("Zero supported source files for component: " + component.id);
    for (const target of component.dependencies) if (!ids.has(target)) throw new Error("Unknown component dependency: " + target);
  }
  const candidates = path => [path, ...[".py", "/__init__.py", ".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", "/index.ts", "/index.js"].map(suffix => path + suffix), path.replace(/\.js$/, ".ts"), path.replace(/\.mjs$/, ".mts"), path.replace(/\.cjs$/, ".cts")];
  const findFile = path => candidates(path).find(candidate => allFiles.has(candidate));
  const moduleFor = file => {
    const roots = [...modulePaths].filter(([, path]) => inside(path, file)).sort((a, b) => b[1].length - a[1].length);
    if (!roots.length) return null;
    const [name, path] = roots[0], suffix = relative(path, file).replace(/\.(?:py|[cm]?[jt]sx?)$/, "").replace(/(?:^|\/)__init__$/, "").split(sep).filter(Boolean).join(".");
    return name + (suffix ? "." + suffix : "");
  };
  function resolveImport(specifier, file, level = 0) {
    if (extname(file) === ".py" && level) {
      const moduleName = moduleFor(file);
      if (!moduleName) return { error: "Python import has no declared module prefix" };
      const packageParts = moduleName.split(".").slice(0, file.endsWith("/__init__.py") ? undefined : -1);
      if (level > packageParts.length) return { error: "Relative import escapes its package" };
      specifier = [...packageParts.slice(0, packageParts.length - level + 1), specifier].filter(Boolean).join(".");
    }
    if (extname(file) !== ".py" && specifier.startsWith(".")) return { path: findFile(resolve(dirname(file), specifier)), local: true };
    const prefixes = [...modulePaths].filter(([name]) => specifier === name || specifier.startsWith(name + ".") || specifier.startsWith(name + "/")).sort((a, b) => b[0].length - a[0].length);
    if (!prefixes.length) {
      if ([...modulePaths.keys()].some(name => specifier.split(".")[0] === name.split(".")[0])) return { local: true, namespace: true };
      return { external: specifier };
    }
    const [name, path] = prefixes[0], suffix = specifier.slice(name.length).replace(/^[./]/, "").replaceAll(".", "/");
    return { path: findFile(suffix ? resolve(path, suffix) : path), local: true };
  }
  const pyFiles = [...allFiles].filter(file => file.endsWith(".py")).map(file => relative(root, file));
  const facts = new Map();
  if (pyFiles.length) {
    const result = spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), "boundary-facts", root], { input: JSON.stringify(pyFiles), encoding: "utf8", timeout: 20000 });
    if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stderr);
    for (const fact of JSON.parse(result.stdout)) facts.set(resolve(root, fact.path), fact);
  }
  for (const file of allFiles) if (!file.endsWith(".py")) facts.set(file, typescriptFacts(readFileSync(file, "utf8"), file));
  const edges = new Map(components.map(component => [component.id, new Set()]));
  function resolveMember(callee, file) {
    if (callee.includes("#")) {
      const [module, member] = callee.split("#");
      return { ...resolveImport(module, file), member: member.replace(/^\./, "").split(".")[0] };
    }
    const level = callee.match(/^\.+/)?.[0].length ?? 0, parts = callee.slice(level).split(".");
    for (let index = parts.length - 1; index > 0; index--) {
      const target = resolveImport(parts.slice(0, index).join("."), file, level);
      if (target.path) return { ...target, member: parts[index] };
    }
    return {};
  }
  for (const [file, fact] of facts) {
    const owner = ownerOf(file), isPublic = owner.publicRoots.some(path => inside(path, file));
    const report = (line, message) => problems.push(relative(root, file) + ":" + line + ": " + message);
    if (fact.loaders.length) report(fact.loaders[0], "Unresolved dynamic loader can bypass component boundaries");
    if (isPublic && (!Array.isArray(fact.exports) || fact.exports.some(name => typeof name !== "string" || name === "*" || name.startsWith("_")))) report(1, "Interface requires explicit non-private exports; no wildcard/implicit barrel");
    for (const imported of fact.imports) {
      const base = resolveImport(imported.module, file, imported.level);
      const children = imported.names.map(name => resolveImport([imported.module, name].filter(Boolean).join("."), file, imported.level)).filter(item => item.path);
      const targets = base.namespace && children.length ? children : [base, ...children];
      for (const target of targets) {
      if (target.error || target.local && !target.path) { report(imported.line, target.error ?? "Unavailable local import: " + imported.module); continue; }
      if (target.external) {
        if (isPublic && infrastructure.has(target.external.replace(/^node:/, "").split(".")[0])) report(imported.line, "Public interface imports infrastructure: " + target.external);
        continue;
      }
      const targetOwner = ownerOf(target.path);
      if (!targetOwner) { report(imported.line, "Local dependency has no declared owner"); continue; }
      if (isPublic && targetOwner.id === owner.id && !owner.publicRoots.some(path => inside(path, target.path)) && ![...owner.facadeTargets].some(([path, targets]) => inside(path, file) && targets.has(target.path))) report(imported.line, "Interface imports private implementation: " + imported.module);
      if (targetOwner.id !== owner.id) {
        edges.get(owner.id).add(targetOwner.id);
        if (targetOwner.composition) report(imported.line, "Domain imports composition: " + targetOwner.id);
        if (!owner.dependencies.includes(targetOwner.id)) report(imported.line, "Undeclared component dependency: " + targetOwner.id);
        if (!owner.composition && !targetOwner.publicRoots.some(path => inside(path, target.path))) report(imported.line, "Private cross-component import: " + imported.module);
        const exposed = facts.get(target.path)?.exports;
        if (!owner.composition && Array.isArray(exposed)) for (const name of imported.names) if (name === "*" || !exposed.includes(name)) report(imported.line, "Unavailable/private interface export: " + name);
      }
      }
    }
    for (const reference of fact.references) {
      const target = resolveMember(reference.callee, file), targetOwner = target.path && ownerOf(target.path);
      if (targetOwner && targetOwner.id !== owner.id && !owner.composition) {
        if (!targetOwner.publicRoots.some(path => inside(path, target.path))) report(reference.line, "Private cross-component member access: " + reference.callee);
        else if (!facts.get(target.path)?.exports?.includes(target.member)) report(reference.line, "Unavailable/private interface member: " + reference.callee);
      }
    }
    if (isPublic) for (const call of fact.calls) if (["open", "read_text", "read_bytes", "write_text", "write_bytes", "connect", "listen", "spawn", "execFile", "Worker"].includes(call.callee.split(".").at(-1))) report(call.line, "Public interface contains lifecycle/I/O implementation: " + call.callee);
    for (const call of fact.calls) if (Object.hasOwn(call, "sql")) {
      if (call.sql === null) { report(call.line, "Unresolved SQL target; ownership cannot be verified"); continue; }
      for (const table of writtenTables(call.sql)) {
        const tableOwner = tables.get(table);
        if (!tableOwner) report(call.line, "SQL writes an unmapped table: " + table);
        else if (tableOwner !== owner.id) report(call.line, "Cross-owner table write: " + table + " belongs to " + tableOwner);
        if (isPublic) report(call.line, "Public interface contains SQL implementation");
      }
    }
  }
  problems.push(...intakeProblems(facts, manifest.intakes ?? [], resolveImport, root));
  const active = [], visited = new Set();
  function visit(name) {
    if (active.includes(name)) { problems.push("Component dependency cycle: " + [...active.slice(active.indexOf(name)), name].join(" -> ")); return; }
    if (visited.has(name)) return;
    active.push(name);
    for (const target of new Set([...edges.get(name), ...components.find(component => component.id === name).dependencies])) visit(target);
    active.pop(); visited.add(name);
  }
  for (const id of ids) visit(id);
  if (problems.length) throw new Error([...new Set(problems)].sort().join("\n"));
  return { files: facts.size, components: components.length, edges: Object.fromEntries([...edges].map(([name, targets]) => [name, [...targets].sort()])) };
}

/** Bounded direct imports of selected optional UI sources and the staged Study facade. */
function checkSelectedFrontend(config, root) {
  const keys = ['sources', 'controllerConsumers', 'privateTargets', 'publicInterface', 'implementation'];
  if (!config || Object.keys(config).sort().join() !== keys.sort().join() ||
      !Array.isArray(config.sources) || !config.sources.length || !Array.isArray(config.controllerConsumers) || !config.controllerConsumers.length || !Array.isArray(config.privateTargets) || !config.privateTargets.length ||
      [...config.sources, ...config.controllerConsumers, ...config.privateTargets, config.publicInterface, config.implementation].some(p => typeof p !== 'string' || !/^app\/[\w./-]+$/.test(p) || p.includes('..')) ||
      [config.sources, config.controllerConsumers, config.privateTargets].some(paths => new Set(paths).size !== paths.length) ||
      config.publicInterface !== 'app/features/study/index.ts' || config.implementation !== 'app/features/study/controller.ts') throw new Error('Invalid selected frontend declaration');
  const safe = path => {
    const file = resolve(root, path);
    if (!inside(root, file) || !existsSync(file) || realpathSync(file) !== file || !statSync(file).isFile()) throw new Error('Unavailable selected frontend source: ' + path);
    return file;
  };
  const canonical = path => path.replace(/\.(?:tsx?|js)$/, '').replace(/\/index$/, '');
  const privateTargets = new Set(config.privateTargets.map(canonical));
  const publicPath = canonical(config.publicInterface);
  function importsFor(path) {
    const file = safe(path);
    return typescriptImports(readFileSync(file, 'utf8'), file).map(specifier => {
      const target = specifier.startsWith('.') ? resolve(dirname(file), specifier) : specifier.startsWith('@/') ? resolve(root, specifier.slice(2)) : null;
      return target && inside(root, target) ? canonical(relative(root, target).split(sep).join('/')) : null;
    }).filter(Boolean);
  }
  const sources = config.sources.flatMap(path => {
    const absolute = resolve(root, path);
    if (!inside(root, absolute) || !existsSync(absolute) || realpathSync(absolute) !== absolute) throw new Error('Unavailable selected frontend source: ' + path);
    return filesUnder(absolute).filter(file => /\.[jt]sx?$/.test(file)).map(file => relative(root, file).split(sep).join('/'));
  });
  if (!sources.length || new Set(sources).size !== sources.length) throw new Error('Invalid selected frontend sources');
  for (const source of sources) for (const target of importsFor(source)) {
    if (privateTargets.has(target) || (target.startsWith('app/features/study/') && target !== publicPath)) throw new Error('Private selected frontend import: ' + source + ' -> ' + target);
  }
  for (const source of config.controllerConsumers) for (const target of importsFor(source)) {
    if (target.startsWith('app/features/study/') && target !== publicPath) throw new Error('Private selected frontend import: ' + source + ' -> ' + target);
  }
  const staged = [config.publicInterface, config.implementation].filter(path => !existsSync(resolve(root, path)));
  if (staged.length && (staged.length !== 2 || process.env.TEMPO_REQUIRE_STUDY_UI === '1')) throw new Error('Unavailable staged Study frontend: ' + staged.join(', '));
  if (!staged.length) for (const target of importsFor(config.publicInterface)) {
    if (privateTargets.has(target) || (target.startsWith('app/features/study/') && target !== canonical(config.implementation))) throw new Error('Study interface imports undeclared private implementation: ' + target);
  }
  if (!staged.length) {
    for (const source of config.controllerConsumers) {
      const file = safe(source), fact = typescriptFacts(readFileSync(file, 'utf8'), file);
      const adopts = fact.calls.some(call => {
        const [module, member] = call.callee.split('#');
        if (!module || !member || !module.startsWith('.')) return false;
        return canonical(relative(root, resolve(dirname(file), module)).split(sep).join('/')) === publicPath &&
          (member === 'useStudyReviewController' || member === '.useStudyReviewController');
      });
      if (!adopts) throw new Error('Selected Home must call the public Study controller: ' + source);
    }
    for (const source of [...config.controllerConsumers, config.publicInterface, config.implementation]) {
      const file = safe(source), fact = typescriptFacts(readFileSync(file, 'utf8'), file);
      for (const call of fact.calls) {
        const member = call.callee.split(/[.#]/).at(-1);
        if (member === 'createStudySession' || (source !== config.implementation && member === 'reviewStudySession'))
          throw new Error('Duplicate selected Study session creation: ' + source);
      }
    }
  }
  return { files: sources.length, controllerConsumers: config.controllerConsumers.length, stagedMissing: staged };
}

/** Exact source/import inventory for a bounded Python slice; no app namespace is external. */
function checkSelectedPython(manifest, root, python) {
  if (!Array.isArray(manifest.sources) || !manifest.sources.length || !manifest.publicExports) throw new Error("Invalid selected ownership map");
  const safe = path => {
    const file = resolve(root, path);
    if (typeof path !== "string" || !inside(root, file) || !existsSync(file) || realpathSync(file) !== file || !statSync(file).isFile()) throw new Error("Unavailable selected source/target: " + path);
    return file;
  };
  const entries = new Map();
  for (const entry of manifest.sources) {
    if (!entry.path?.endsWith('.py') || entries.has(entry.path) || !Array.isArray(entry.imports) || typeof entry.pure !== 'boolean' || entry.imports.some(name => typeof name !== 'string' || name.includes('*'))) throw new Error("Invalid selected source");
    safe(entry.path); entries.set(entry.path, entry);
  }
  const modulePath = name => {
    const base = resolve(root, name === 'tempo_storage' || name.startsWith('tempo_storage.') ? 'database' : 'backend', ...name.split('.'));
    const path = [base + '.py', resolve(base, '__init__.py')].find(existsSync);
    if (!path) throw new Error("Unavailable selected target: " + name);
    safe(relative(root, path)); return path;
  };
  for (const entry of entries.values()) for (const name of entry.imports) if (name === 'app' || name.startsWith('app.') || name === 'tempo_storage' || name.startsWith('tempo_storage.')) modulePath(name);
  for (const name of Object.keys(manifest.publicExports)) modulePath(name);
  const result = spawnSync(python, [resolve(repository, 'tools/component-build/check-python.py'), 'boundary-facts', root], { input: JSON.stringify([...entries.keys()]), encoding: 'utf8', timeout: 20000 });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stderr);
  const facts = JSON.parse(result.stdout), problems = [];
  for (const fact of facts) {
    const entry = entries.get(fact.path), parts = fact.path.slice('backend/'.length, -3).split('/');
    if (parts.at(-1) === '__init__') parts.pop(); else parts.pop();
    const report = (line, message) => problems.push(`${fact.path}:${line}: ${message}`);
    if (fact.loaders.length) report(fact.loaders[0], 'Unresolved dynamic loader');
    for (const item of fact.imports) {
      const name = item.level ? [...parts.slice(0, parts.length - item.level + 1), item.module].filter(Boolean).join('.') : item.module;
      if (!entry.imports.includes(name)) report(item.line, 'Undeclared selected import: ' + name);
      if (item.names.includes('*')) report(item.line, 'Wildcard import in selected source');
      if (name === 'app' || name.startsWith('app.') || name === 'tempo_storage' || name.startsWith('tempo_storage.')) for (const member of item.names) {
        const child = resolve(root, name === 'tempo_storage' || name.startsWith('tempo_storage.') ? 'database' : 'backend', ...name.split('.'), member);
        if ((existsSync(child + '.py') || existsSync(resolve(child, '__init__.py'))) && !entry.imports.includes(name + '.' + member)) report(item.line, 'Undeclared selected import: ' + name + '.' + member);
      }
      if (entry.pure && infrastructure.has(name.replace(/^node:/, '').split('.')[0])) report(item.line, 'Pure selected source imports infrastructure: ' + name);
      const allowed = manifest.publicExports[name];
      if (allowed && item.names.length) for (const member of item.names) if (!allowed.includes(member)) report(item.line, 'Unavailable selected public export: ' + name + '.' + member);
    }
    for (const reference of fact.references) for (const [module, names] of Object.entries(manifest.publicExports)) {
      if (reference.callee.startsWith(module + '.')) {
        const member = reference.callee.slice(module.length + 1).split('.')[0];
        if (!names.includes(member)) report(reference.line, 'Unavailable selected public member: ' + module + '.' + member);
      }
    }
    if (entry.pure) for (const call of fact.calls) {
      if (Object.hasOwn(call, 'sql')) report(call.line, 'Pure selected source contains SQL execution');
      if (['open', 'read_text', 'read_bytes', 'write_text', 'write_bytes', 'connect', 'listen', 'spawn'].includes(call.callee.split('.').at(-1))) report(call.line, 'Pure selected source contains lifecycle/I/O: ' + call.callee);
    }
  }
  for (const [name, names] of Object.entries(manifest.publicExports)) {
    const path = relative(root, modulePath(name)), fact = facts.find(item => item.path === path);
    if (!fact || names.some(member => !(fact.exports ?? (name === 'app.games.comparison' ? fact.declarations : [])).includes(member))) throw new Error('Unavailable selected public exports: ' + name);
  }
  if (problems.length) throw new Error([...new Set(problems)].sort().join('\n'));
  return { mode: manifest.mode, files: facts.length };
}

/** Reuse the same queue-call walk for a narrow intake proof before the global map joins. */
export function intakeProblems(facts, intakes, resolveImport, root) {
  const problems = [], absolute = path => resolve(root, path);
  for (const intake of intakes) {
    const file = absolute(intake.path), fact = facts.get(file);
    if (!fact || !intake.functions?.length || !intake.forbiddenCalls?.length) throw new Error("Missing intake guard source/functions/call policy");
    const pending = intake.functions.map(fn => [file, fn]), seen = new Set();
    while (pending.length) {
      const [currentFile, fn] = pending.pop(), current = facts.get(currentFile), identity = currentFile + "#" + fn;
      if (seen.has(identity)) continue;
      if (!current?.functions.includes(fn)) throw new Error("Unavailable guarded intake entry: " + fn);
      seen.add(identity);
      for (const call of [...current.calls, ...current.references].filter(call => call.function === fn)) {
        if (Object.hasOwn(call, "sql")) problems.push(relative(root, currentFile) + ":" + call.line + ": Intake contains direct SQL execution; use its owner capability");
        if ((["getattr", "Reflect.get"].includes(call.callee) && !call.data_access) || intake.forbiddenCalls.some(pattern => glob(pattern).test(call.callee) || glob(pattern).test(call.callee.split(".").at(-1)))) problems.push(relative(root, currentFile) + ":" + call.line + ": Intake references prohibited queue work: " + call.callee);
        if (current.functions.includes(call.callee)) pending.push([currentFile, call.callee]);
        else if (call.callee.includes("#")) {
          const [module, member] = call.callee.split("#"), target = resolveImport(module, currentFile);
          if (target.path && facts.get(target.path)?.functions.includes(member.replace(/^\./, ""))) pending.push([target.path, member.replace(/^\./, "")]);
        } else {
          const level = call.callee.match(/^\.+/)?.[0].length ?? 0, parts = call.callee.slice(level).split(".");
          for (let index = parts.length - 1; index > 0; index--) {
            const target = resolveImport(parts.slice(0, index).join("."), currentFile, level), member = parts.slice(index).join(".");
            if (target.path && facts.get(target.path)?.functions.includes(member)) { pending.push([target.path, member]); break; }
          }
        }
      }
    }
  }
  return problems;
}

export function checkPythonIntake(path, functions, forbiddenCalls, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  root = realpathSync(root);
  const file = resolve(root, path), sourceRoot = dirname(file);
  if (!inside(root, file)) throw new Error("Intake source escape");
  const files = filesUnder(sourceRoot).filter(file => file.endsWith(".py")).map(file => relative(root, file));
  const result = spawnSync(python, [resolve(repository, "tools/component-build/check-python.py"), "boundary-facts", root], { input: JSON.stringify(files), encoding: "utf8", timeout: 20000 });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stderr);
  const facts = new Map(JSON.parse(result.stdout).map(fact => [resolve(root, fact.path), fact]));
  for (const fact of facts.values()) if (fact.loaders.length) throw new Error(fact.path + ": Unresolved dynamic loader in intake source boundary");
  const resolveImport = (module, importer, level = 0) => {
    const packagePath = level ? resolve(dirname(importer), ...Array(level - 1).fill(".."), ...module.split(".").filter(Boolean)) : resolve(root, "backend", ...module.split("."));
    return { path: [packagePath + ".py", resolve(packagePath, "__init__.py")].find(path => facts.has(path)) };
  };
  const problems = intakeProblems(facts, [{path, functions, forbiddenCalls}], resolveImport, root);
  if (problems.length) throw new Error([...new Set(problems)].sort().join("\n"));
  return { source: path, functions, sourceFiles: facts.size };
}

export function writtenTables(sql) {
  const clean = sql.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/--[^\n]*/g, " ").replace(/'(?:''|[^'])*'/g, "''");
  const target = /\b(INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|MERGE\s+INTO|UPDATE|COPY|CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?|ALTER\s+TABLE(?:\s+IF\s+EXISTS)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\b\s*((?:"[^"]+"|[a-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|[a-z_][\w$]*))?)/gi;
  return [...clean.matchAll(target)].filter(match => !/^UPDATE$/i.test(match[1]) || !/(?:\bFOR|\bDO|\bKEY)\s*$/i.test(clean.slice(0, match.index)) && !/^(?:SET|SKIP|NOWAIT|OF)$/i.test(match[2])).map(match => match[2].replace(/["\s]/g, "").toLowerCase());
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    if (process.argv[2] === "--intake") {
      if (!process.argv[3] || !process.argv[4] || !process.argv[5]) throw new Error("Usage: --intake <source.py> <functions comma-list> <prohibited-call-patterns comma-list> [source-root]");
      console.log(JSON.stringify(checkPythonIntake(process.argv[3], process.argv[4].split(","), process.argv[5].split(","), process.argv[6] ?? repository)));
      process.exit(0);
    }
    if (!process.argv[2]) throw new Error("Usage: node check-interface-boundaries.mjs <owner-manifest.json> [source-root]");
    console.log(JSON.stringify(checkInterfaceBoundaries(JSON.parse(readFileSync(process.argv[2], "utf8")), process.argv[3] ?? repository)));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
