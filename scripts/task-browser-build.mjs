import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Only a newly created task directory is eligible for cleanup. Vite may empty
// its output directory; never point it at an existing installation or checkout.
export function withTaskBrowserBuild(run, { parent = tmpdir(), target = 'local' } = {}) {
  if (!['local', 'study', 'static'].includes(target)) throw new Error('Unsupported browser target');
  const directory = mkdtempSync(join(parent, `tempo-${target}-build-`));
  try { return run(join(directory, 'output'), directory); }
  finally { rmSync(directory, { recursive: true, force: true }); }
}

export function isLocalQualificationBuild(command, args) {
  return command === 'npm' && args.length === 2 && args[0] === 'run' && args[1] === 'build:local';
}
