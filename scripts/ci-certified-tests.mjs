import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

// A deliberately bounded certificate, not a general reverse-dependency resolver.
// Unsupported/new dependency syntax invalidates the certificate until reviewed.
export function dependencyLines(source) {
  return source.split('\n').map(line => line.trim()).filter(line =>
    /\b(?:import|from|require|eval|exec|execFile|exec_module|fetch|readFile\w*|spec_from_file_location|__import__|getattr|setattr|Function|globalThis|XMLHttpRequest|WebSocket)\b|\b(?:os|sys)\.|\.py["']|^test(?:\.[\w.]+)?\s*\(/.test(line)
    && !line.startsWith('//') && !line.startsWith('#'));
}

export function validateCertificate(certificate, sources) {
  for (const [path, expected] of Object.entries(certificate.dependencies)) {
    if (typeof sources[path] !== 'string' || JSON.stringify(dependencyLines(sources[path])) !== JSON.stringify(expected)) return false;
  }
  for (const [path, expected] of Object.entries(certificate.sqlRelations ?? {})) {
    if (JSON.stringify(sqlRelations(sources[path])) !== JSON.stringify(expected)) return false;
  }
  for (const [token, expected] of Object.entries(certificate.consumers)) {
    const observed = Object.keys(sources).filter(path => sources[path].includes(token)).sort();
    if (JSON.stringify(observed) !== JSON.stringify(expected)) return false;
  }
  return true;
}

export function sqlRelations(source) {
  if (typeof source !== 'string') return null;
  const relationNames = [...source.matchAll(/\b(?:FROM|INTO|UPDATE|JOIN|TRUNCATE|TABLE)\s+([a-z_]\w*)\b/gi)].map(match => match[1]);
  for (const match of source.matchAll(/(?:snapshot_row|restore_row)\([^,]+,\s*['"]([a-z_]\w*)['"]|sql\.Identifier\(['"]([a-z_]\w*)['"]\)/g)) relationNames.push(match[1] ?? match[2]);
  // Conservatively include identifier literals used by dynamic table/column lists.
  for (const match of source.matchAll(/['"]([a-z_]\w*)['"]/gi)) relationNames.push(match[1]);
  return [...new Set(relationNames.map(name => name.toLowerCase()))].sort();
}

export function readCertificateSources() {
  const listing = spawnSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard'], { encoding: 'utf8' });
  if (listing.status !== 0) throw new Error('Certificate source inventory unavailable');
  return Object.fromEntries([...new Set(listing.stdout.split('\0'))]
    .filter(path => /\.(?:py|tsx?|mjs|json|ya?ml)$/.test(path)
      && !['scripts/ci-certified-tests.json', 'scripts/ci-fast-lane-inventory.json', 'package-lock.json'].includes(path))
    .map(path => [path, readFileSync(path, 'utf8')]));
}

export const certificate = JSON.parse(readFileSync(new URL('./ci-certified-tests.json', import.meta.url), 'utf8'));

export function nativeFixtureCases(source) {
  if (typeof source !== 'string' || [...source.matchAll(/^class (\w+)\(unittest\.TestCase\):/gm)]
    .map(match => match[1]).join() !== 'RedisFixtureIsolation'
    || [...source.matchAll(/^class /gm)].length !== 1) throw new Error('Unknown native fixture class');
  const methods = [...source.matchAll(/^    def (test_\w+)\(self\):/gm)].map(match => match[1]);
  if (!methods.length || new Set(methods).size !== methods.length
    || [...source.matchAll(/^\s+(?:async )?def test_/gm)].length !== methods.length) throw new Error('Invalid native fixture cases');
  return methods.map(method => `check_postgres_redis_fixture_isolation.RedisFixtureIsolation.${method}`).sort();
}
