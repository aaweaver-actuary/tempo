import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

// A deliberately bounded certificate, not a general reverse-dependency resolver.
// Unsupported/new dependency syntax invalidates the certificate until reviewed.
export function dependencyLines(source) {
  return source.split('\n').map(line => line.trim()).filter(line =>
    /\b(?:import|from|require|eval|exec|execFile|exec_module|fetch|readFile\w*|spec_from_file_location|__import__)\b|\.py["']|^test(?:\.[\w.]+)?\s*\(/.test(line)
    && !line.startsWith('//') && !line.startsWith('#'));
}

export function validateCertificate(certificate, sources) {
  for (const [path, expected] of Object.entries(certificate.dependencies)) {
    if (typeof sources[path] !== 'string' || JSON.stringify(dependencyLines(sources[path])) !== JSON.stringify(expected)) return false;
  }
  for (const [token, expected] of Object.entries(certificate.consumers)) {
    const observed = Object.keys(sources).filter(path => sources[path].includes(token)).sort();
    if (JSON.stringify(observed) !== JSON.stringify(expected)) return false;
  }
  return true;
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
