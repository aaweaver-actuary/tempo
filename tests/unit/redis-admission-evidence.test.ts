import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { createRedisAdmissionEvidenceDirectory, writeEvidenceThenCleanup, writeRedisAdmissionEvidence } from "../browser/support/redis-admission-evidence";

const project = "tempo-pg-regressions-17-abcdef12";
const first = "redis-admission-11111111-1111-4111-8111-111111111111";
const second = "redis-admission-22222222-2222-4222-8222-222222222222";

describe("Redis admission evidence ownership", () => {
  it("keeps repeated runs separate and refuses directory and file collisions", () => {
    const base = mkdtempSync(join(tmpdir(), "redis-evidence-test-"));
    try {
      const one = createRedisAdmissionEvidenceDirectory(base, project, first);
      const two = createRedisAdmissionEvidenceDirectory(base, project, second);
      expect(one).not.toBe(two);
      writeRedisAdmissionEvidence(one, "native-recovery.json", "original");
      writeRedisAdmissionEvidence(two, "native-recovery.json", "second");
      expect(() => createRedisAdmissionEvidenceDirectory(base, project, first)).toThrow();
      expect(() => writeRedisAdmissionEvidence(one, "native-recovery.json", "overwrite")).toThrow();
      expect(readFileSync(join(one, "native-recovery.json"), "utf8")).toBe("original");
      expect(readFileSync(join(two, "native-recovery.json"), "utf8")).toBe("second");
      expect(() => createRedisAdmissionEvidenceDirectory(base, "../foreign", first)).toThrow();
      expect(() => createRedisAdmissionEvidenceDirectory(base, project, "../foreign")).toThrow();
      expect(() => writeRedisAdmissionEvidence(one, "../foreign", "x")).toThrow();
    } finally { rmSync(base, { recursive: true, force: true }); }
  });

  it("runs cleanup after evidence failure and preserves both errors", async () => {
    const writeFailure = new Error("write failure");
    const cleanupFailure = new Error("cleanup failure");
    let cleanups = 0;
    const write = () => { throw writeFailure; };
    await expect(writeEvidenceThenCleanup(write, async () => { cleanups++; })).rejects.toBe(writeFailure);
    expect(cleanups).toBe(1);
    try {
      await writeEvidenceThenCleanup(write, async () => { cleanups++; throw cleanupFailure; });
      throw new Error("expected failure");
    } catch (error) {
      expect(error).toBeInstanceOf(AggregateError);
      expect((error as AggregateError).errors).toEqual([writeFailure, cleanupFailure]);
      expect((error as AggregateError).cause).toBe(writeFailure);
    }
    expect(cleanups).toBe(2);
    await expect(writeEvidenceThenCleanup(() => {}, async () => { throw cleanupFailure; })).rejects.toBe(cleanupFailure);
  });
});
