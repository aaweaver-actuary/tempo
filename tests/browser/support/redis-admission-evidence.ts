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

export async function writeEvidenceThenCleanup(write: () => void, cleanup: () => Promise<void>): Promise<void> {
  let writeFailure: unknown;
  let writeFailed = false;
  try { write(); } catch (error) { writeFailed = true; writeFailure = error; }
  try { await cleanup(); } catch (error) {
    if (writeFailed) throw new AggregateError([writeFailure, error], "Evidence write and fixture cleanup both failed", { cause: writeFailure });
    throw error;
  }
  if (writeFailed) throw writeFailure;
}
