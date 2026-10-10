import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
export function validateMigrationInventory(files, schemaSource) {
  const versions = files.filter(file => file.endsWith('.sql')).map(file => {
    const match = file.match(/^(\d{3})_.+\.sql$/);
    if (!match) throw new Error(`Invalid migration filename: ${file}`);
    return Number(match[1]);
  }).sort((left, right) => left - right);
  if (!versions.length || versions.some((version, index) => version !== index + 1)) throw new Error('Migration versions must be unique and contiguous from 001');
  const matches = [...schemaSource.matchAll(/^POSTGRES_SCHEMA_VERSION\s*=\s*(\d+)\s*$/gm)];
  if (matches.length !== 1 || Number(matches[0][1]) !== versions.at(-1)) throw new Error('Migration inventory disagrees with authoritative schema version');
  return versions.at(-1);
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  console.log(`Migration inventory: ${validateMigrationInventory(readdirSync('backend/migrations'), readFileSync('backend/app/schema_version.py', 'utf8'))}`);
}
