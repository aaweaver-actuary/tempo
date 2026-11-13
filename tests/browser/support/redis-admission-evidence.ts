import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export function createRedisAdmissionEvidenceDirectory(base: string, project: string, identity: string): string {
  if (!/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)
      || !/^redis-admission-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(identity))
    throw new Error("Redis admission evidence requires invocation-owned identities");
  const parent = join(base, project);
  mkdirSync(parent, { recursive: true });
  const directory = join(parent, identity);
  mkdirSync(directory);
  return directory;
}

export function writeRedisAdmissionEvidence(directory: string, filename: string, contents: string): void {
  if (!/^(receipt-inventory-before-cleanup|receipt-cleanup|native-recovery)\.json$/.test(filename))
    throw new Error("Unknown Redis admission evidence name");
  writeFileSync(join(directory, filename), contents, { flag: "wx" });
}

/** Playwright serializes message and cause, but does not serialize AggregateError.errors. */
export function describeEvidenceFailure(value: unknown, seen = new Set<unknown>()): string {
  if (!(value instanceof Error)) return String(value);
  if (seen.has(value)) return "[cyclic error]";
  seen.add(value);
  const details = [`${value.name}: ${value.message}`];
  if (value instanceof AggregateError) {
    value.errors.forEach((item, index) => details.push(`failure ${index + 1}: ${describeEvidenceFailure(item, seen)}`));
  } else if (value.cause !== undefined) {
    details.push(`cause: ${describeEvidenceFailure(value.cause, seen)}`);
  }
  return details.join("; ");
}

export async function writeEvidenceThenCleanup(write: () => void, cleanup: () => Promise<void>): Promise<void> {
  let writeFailure: unknown;
  let writeFailed = false;
  try { write(); } catch (error) { writeFailed = true; writeFailure = error; }
  try { await cleanup(); } catch (error) {
    if (writeFailed) throw new AggregateError([writeFailure, error], `Evidence write and fixture cleanup both failed; evidence: ${describeEvidenceFailure(writeFailure)}; cleanup: ${describeEvidenceFailure(error)}`, { cause: writeFailure });
    throw error;
  }
  if (writeFailed) throw writeFailure;
}
