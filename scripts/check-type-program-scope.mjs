import { realpathSync } from 'node:fs';
import { resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

export function compareTypeProgramRoots(aggregate, browser, tooling) {
  const all = new Set(aggregate), union = new Set([...browser, ...tooling]);
  return { missing: [...all].filter(path => !union.has(path)).sort(),
    extra: [...union].filter(path => !all.has(path)).sort() };
}

export function readTypeProgramRoots(configPath, root = process.cwd()) {
  const path = resolve(root, configPath);
  const config = ts.readConfigFile(path, ts.sys.readFile);
  if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root, undefined, path);
  if (parsed.errors.length) throw new Error(ts.formatDiagnostics(parsed.errors, {
    getCurrentDirectory: () => root, getCanonicalFileName: value => value, getNewLine: () => '\n',
  }));
  return parsed.fileNames.map(path => relative(root, path)).sort();
}

export function verifyTypeProgramRoots(root = process.cwd()) {
  const aggregate = readTypeProgramRoots('tsconfig.json', root);
  const browser = readTypeProgramRoots('tsconfig.browser.json', root);
  const tooling = readTypeProgramRoots('tsconfig.tooling.json', root);
  const difference = compareTypeProgramRoots(aggregate, browser, tooling);
  if (difference.missing.length || difference.extra.length)
    throw new Error('Type program roots differ from aggregate: ' + JSON.stringify(difference));
  return { aggregate: aggregate.length, browser: browser.length, tooling: tooling.length, ...difference };
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { console.log(JSON.stringify(verifyTypeProgramRoots())); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
