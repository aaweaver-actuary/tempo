import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { FindingSchema, GamePageSchema, RepertoireSnapshotSchema, GameLapseSchema, GameEffectControlSchema, logicalEffectId, PRODUCT_SCHEMAS } from "../../app/features/product-api/contracts";

const directory = resolve("contracts/product-v02/fixtures");
function fixture(name: string) { return JSON.parse(readFileSync(resolve(directory, `${name}.json`), "utf8")).payload; }

describe("product v0.2 shared wire", () => {
  for (const file of readdirSync(directory).filter(file => file.endsWith(".json"))) {
    it(`round trips the same Python/TypeScript fixture ${file}`, () => {
      const { model, payload } = JSON.parse(readFileSync(resolve(directory, file), "utf8"));
      const schema = PRODUCT_SCHEMAS[model as keyof typeof PRODUCT_SCHEMAS];
      expect(JSON.parse(JSON.stringify(schema.parse(payload)))).toEqual(payload);
    });
  }
  it.each([["kind", "guessed_miss"], ["ply", 0], ["ply", true], ["source_revision", 0], ["introduced_before_game", null], ["expected_uci", ["e2e4", "g1f3"]]])("rejects unproved/invalid %s", (field, value) => {
    expect(() => FindingSchema.parse({ ...fixture("confirmed-introduced-miss"), [field as string]: value })).toThrow();
  });
  it("rejects incomplete pages claiming completion and conflicting source revisions", () => {
    const payload = fixture("incomplete-page");
    expect(() => GamePageSchema.parse({ ...payload, complete: true })).toThrow();
    expect(() => GamePageSchema.parse({ ...payload, records: [...payload.records, { ...payload.records[0], source_revision: 2 }] })).toThrow();
  });
  it("rejects conflicting repertoire revision and detaches caller containers", () => {
    const payload = fixture("snapshot");
    const result = RepertoireSnapshotSchema.parse(payload);
    payload.membership.routes[0].moves.push("e7e5");
    expect(result.membership.routes[0].moves).toEqual(["e2e4"]);
    payload.decision_presentations[0].repertoire_revision = 2;
    expect(() => RepertoireSnapshotSchema.parse(payload)).toThrow();
  });
  it("keeps hold/withdraw identity independent of delivery and source revision", () => {
    const lapse = GameLapseSchema.parse(fixture("lapse"));
    const control = GameEffectControlSchema.parse(fixture("hold"));
    expect(logicalEffectId(control.key)).toBe(logicalEffectId(lapse.key));
    expect(control.source_revision).toBeGreaterThan(lapse.finding.source_revision);
    const source = fixture("incomplete-page");
    expect(GamePageSchema.parse(source).records[0].played_at).toBeNull();
    expect(GamePageSchema.parse(source).records[0].pgn).toBe(source.records[0].pgn);
  });
});
