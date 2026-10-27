/** Root registration only; the #482 checker owns all boundary/negative rules. */
import { readFileSync, existsSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { checkInterfaceBoundaries } from '../tools/component-build/check-interface-boundaries.mjs';

const root = realpathSync(process.cwd());
const allocation = JSON.parse(readFileSync(resolve(root, 'docs/architecture/interface-directory-allocation/allocation.json'), 'utf8'));
const missing = allocation.allocations.flatMap(owner => owner.public_interface_directories
  .filter(path => !existsSync(resolve(root, path)))
  .map(path => `#${owner.owner_issue} ${owner.component}: missing public surface ${path}`));
if (missing.length) {
  console.error(missing.join('\n'));
  process.exitCode = 1;
} else {
  const components = allocation.allocations.map(owner => ({
    id: owner.component,
    ...owner.boundary,
    interfaces: owner.public_interface_directories,
    dependencies: owner.depends_on,
  }));
  try { console.log(JSON.stringify(checkInterfaceBoundaries({ components }, root))); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
