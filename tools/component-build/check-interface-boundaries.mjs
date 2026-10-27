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
  const aliases = new Map(), constants = new Map(), calls = [], references = [], functions = [], exported = [], symbols = {};
  const line = node => syntax.getLineAndCharacterOfPosition(node.getStart(syntax)).line + 1;
  const found = typescriptImports(source, path).map(module => ({ module, names: [], level: 0, line: 1 }));
  function binding(node) {
    if (ts.isImportDeclaration(node)) {
      const module = node.moduleSpecifier.text, names = node.importClause?.namedBindings;
      const imported = found.find(item => item.module === module && !item.bound);
      imported.bound = true;
      if (names && ts.isNamedImports(names)) {
        for (const item of names.elements) aliases.set(item.name.text, module + "#" + (item.propertyName ?? item.name).text);
        imported.names = names.elements.map(item => (item.propertyName ?? item.name).text);
        imported.bindings = names.elements.map(item => ({ name: (item.propertyName ?? item.name).text, local: item.name.text, module_level: true }));
      } else if (names && ts.isNamespaceImport(names)) aliases.set(names.name.text, module + "#");
    }
    if (ts.isExportDeclaration(node) && node.moduleSpecifier && node.exportClause && ts.isNamedExports(node.exportClause)) {
      const imported = found.find(item => item.module === node.moduleSpecifier.text && !item.bound);
      imported.bound = true;
      imported.names = node.exportClause.elements.map(item => (item.propertyName ?? item.name).text);
      imported.bindings = node.exportClause.elements.map(item => ({ name: (item.propertyName ?? item.name).text, local: item.name.text, module_level: true }));
    }
    if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal)) {
      found.push({ module: node.argument.literal.text, names: node.qualifier ? [node.qualifier.getText(syntax).split(".")[0]] : [], level: 0, line: line(node), bound: true });
    }
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) constants.set(node.name.text, node.initializer);
    ts.forEachChild(node, binding);
  }
  binding(syntax);
  function typeNames(nodes) {
    const names = new Set();
    function visit(node) {
      if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal) && node.qualifier) names.add(node.argument.literal.text + "#" + node.qualifier.getText(syntax));
      if (ts.isTypeReferenceNode(node) || ts.isTypeQueryNode(node) || ts.isExpressionWithTypeArguments(node)) {
        const [head, ...tail] = (node.typeName ?? node.exprName ?? node.expression).getText(syntax).split(".");
        names.add([aliases.get(head) ?? head, ...tail].join("."));
      }
      ts.forEachChild(node, visit);
    }
    for (const node of nodes.filter(Boolean)) visit(node);
    return [...names];
  }
  function signature(node) {
    const types = [node.type, ...(node.parameters ?? []).map(parameter => parameter.type), ...(node.heritageClauses ?? [])];
    for (const member of node.members ?? []) {
      if (member.modifiers?.some(modifier => [ts.SyntaxKind.PrivateKeyword, ts.SyntaxKind.ProtectedKeyword].includes(modifier.kind))) continue;
      types.push(member.type, ...(member.parameters ?? []).map(parameter => parameter.type));
    }
    return { types: typeNames(types) };
  }
  for (const node of syntax.statements) {
    if (node.name && [ts.isFunctionDeclaration, ts.isClassDeclaration, ts.isInterfaceDeclaration, ts.isTypeAliasDeclaration, ts.isEnumDeclaration].some(is => is(node))) symbols[node.name.text] = signature(node);
    if (ts.isVariableStatement(node)) for (const item of node.declarationList.declarations) if (ts.isIdentifier(item.name)) symbols[item.name.text] = signature(item);
  }
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
  return { path, imports: found, functions, calls, references, exports: exported, symbols, loaders: [] };
}

/** Small declarative ownership input supplied by the integration owner, not a runtime registry. */
export function checkInterfaceBoundaries(manifest, root = repository, python = process.env.TEMPO_COMPONENT_PYTHON ?? "python3") {
  root = realpathSync(root);
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
      const module = moduleFor(file);
      if (!module) return { error: "Python import has no declared module prefix" };
      const packageParts = module.split(".").slice(0, file.endsWith("/__init__.py") ? undefined : -1);
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
  function symbolAt(file, name, seen = new Set()) {
    const identity = file + "#" + name, fact = facts.get(file);
    if (!fact || seen.has(identity)) return null;
    seen = new Set([...seen, identity]);
    if (fact.symbols?.[name]) return { path: file, name, identity, ...fact.symbols[name] };
    if (name.includes(".") || name.includes("#")) {
      const target = resolveMember(name, file);
      if (target.path) return symbolAt(target.path, target.member, seen);
    }
    for (const imported of fact.imports) for (const binding of imported.bindings ?? []) {
      if (binding.local !== name || !binding.module_level || binding.name === "*") continue;
      const target = resolveImport(imported.module, file, imported.level);
      if (target.external) return { external: true };
      if (target.path) return symbolAt(target.path, binding.name, seen);
    }
    return null;
  }
  // Stable public bindings, including a facade's unchanged own implementation
  // values. Cross-owner private imports are still checked at every source edge.
  const publicSymbols = new Map(components.map(owner => [owner.id, new Set()]));
  for (const [file, fact] of facts) {
    const owner = ownerOf(file);
    if (owner.publicRoots.some(path => inside(path, file))) for (const name of fact.exports ?? []) {
      const symbol = symbolAt(file, name);
      if (symbol?.identity) publicSymbols.get(owner.id).add(symbol.identity);
    }
  }
  const lifecycle = ["open", "read_text", "read_bytes", "write_text", "write_bytes", "connect", "listen", "spawn", "execFile", "Worker", "ConnectionPool", "AsyncConnectionPool", "create_engine"];
  for (const [file, fact] of facts) {
    const owner = ownerOf(file), isPublic = owner.publicRoots.some(path => inside(path, file));
    const report = (line, message) => problems.push(relative(root, file) + ":" + line + ": " + message);
    if (fact.loaders.length) report(fact.loaders[0], "Unresolved dynamic loader can bypass component boundaries");
    if (isPublic && (!Array.isArray(fact.exports) || fact.exports.some(name => typeof name !== "string" || name === "*" || name.startsWith("_")))) report(1, "Interface requires explicit non-private exports; no wildcard/implicit barrel");
    if (isPublic) for (const name of fact.exports ?? []) {
      const symbol = symbolAt(file, name);
      if (!symbol) { report(1, "Unavailable interface export binding: " + name); continue; }
      for (const type of symbol.types ?? []) {
        const referenced = symbolAt(symbol.path, type), typeOwner = referenced?.path && ownerOf(referenced.path);
        if (typeOwner && !publicSymbols.get(typeOwner.id).has(referenced.identity)) report(1, "Unexposed private type in interface signature: " + name + " -> " + type);
      }
    }
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
      if (isPublic && targetOwner.id === owner.id && !owner.publicRoots.some(path => inside(path, target.path))) {
        const bindings = imported.bindings ?? [];
        if (!bindings.length || bindings.some(binding => !binding.module_level || binding.name.startsWith("_") || !fact.exports?.includes(binding.local) || !symbolAt(target.path, binding.name))) report(imported.line, "Interface imports private implementation without an explicit public re-export: " + imported.module);
        const implementation = facts.get(target.path);
        for (const call of implementation?.calls ?? []) if (!implementation.functions.includes(call.function) && (Object.hasOwn(call, "sql") || lifecycle.includes(call.callee.split(".").at(-1)))) report(imported.line, "Interface re-export starts implementation I/O: " + imported.module);
        for (const call of fact.calls) if (!fact.functions.includes(call.function) && bindings.some(binding => call.callee === ".".repeat(imported.level) + [imported.module, binding.name].filter(Boolean).join(".") || call.callee === imported.module + "#" + binding.name)) report(call.line, "Interface executes a re-export during import: " + imported.module);
      }
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
    if (isPublic) for (const call of fact.calls) if (lifecycle.includes(call.callee.split(".").at(-1))) report(call.line, "Public interface contains lifecycle/I/O implementation: " + call.callee);
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
