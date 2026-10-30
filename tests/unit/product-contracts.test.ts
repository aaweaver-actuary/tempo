import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { webcrypto } from "node:crypto";
import { describe, expect, it } from "vitest";
import { FindingSchema, GamePageSchema, RepertoireSnapshotSchema, FirstDepartureWitnessSchema, GameEncounterSchema, EncounterReceiptSchema, GameLapseSchema, GameEffectControlSchema, GameDetailSchema, ProviderSyncStatusSchema, logicalEffectId, PRODUCT_SCHEMAS } from "../../app/features/product-api/contracts";
import { ProbabilityEvidenceSchema, EngineEvidenceSchema, ProposalSchema, ProposalDecisionSchema, JoinPlanSchema, matchesProposalPreview } from "../../app/features/product-api/contracts";

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
  it("represents a hold before the delayed original without owner obligation identity", () => {
    const hold = GameEffectControlSchema.parse(fixture("hold"));
    const original = GameLapseSchema.parse(fixture("lapse"));
    expect(hold.withdrawal).toBeNull();
    expect(hold.reason).toBe("source_corrected");
    expect(hold.key).toEqual(original.key);
    expect(hold.source_revision).toBeGreaterThan(original.finding.source_revision);
    expect(() => GameEffectControlSchema.parse({ ...fixture("hold"), reason: null })).toThrow();
  });
  it("freezes returned nested evidence and preserves unknown current-content authority", () => {
    const source = fixture("snapshot");
    const snapshot = RepertoireSnapshotSchema.parse(source);
    expect(() => (snapshot.membership.routes[0].moves as unknown as string[]).push("e7e5")).toThrow();
    expect(() => (snapshot.membership.scope_repertoire_ids as unknown as string[]).push("other")).toThrow();
    expect(Object.isFrozen(snapshot.membership.routes)).toBe(true);
    const withdrawal = GameEffectControlSchema.parse(fixture("withdraw")).withdrawal!;
    expect(() => Object.assign(withdrawal, { reason: "reanalysis" })).toThrow();
    expect(snapshot.decision_presentations[0].available).toBe(true);
    expect(snapshot.decision_presentations[0].introduction_matches_current_content).toBe(true);
    delete source.decision_presentations[0].available;
    delete source.decision_presentations[0].introduction_matches_current_content;
    const unknown = RepertoireSnapshotSchema.parse(source).decision_presentations[0];
    expect(unknown.available).toBe(false);
    expect(unknown.introduction_matches_current_content).toBe(false);
  });
  it("keeps original game moves separate from findings and preserves provider errors/filters", () => {
    const detail = GameDetailSchema.parse(fixture("game-detail"));
    expect(detail.moves).toEqual(["d2d4", "d7d5", "c2c4"]);
    expect(detail.findings[0].played_uci).toBe("d2d4");
    expect(() => (detail.moves as unknown as string[]).push("c7c6")).toThrow();
    expect(() => ProviderSyncStatusSchema.parse({ ...fixture("provider-sync-failed"), error: null })).toThrow();
    expect(GamePageSchema.parse(fixture("incomplete-page")).filters).toEqual([["rated", "false"], ["perfType", "rapid"]]);
  });
  it.each(["negative", "renormalized", "personal_population", "illegal_count"])("rejects invalid probability %s", fault => {
    const payload = fixture("probability-unknown-mass");
    if (fault === "negative") payload.distribution.moves[0].probability = -.1;
    else if (fault === "renormalized") payload.distribution.unknown_mass = 0;
    else if (fault === "personal_population") payload.basis = "personal";
    else payload.counts = { kind: "counts", total_count: 1, moves: [{ move_uci: "e7e5", count: 1 }] };
    expect(() => ProbabilityEvidenceSchema.parse(payload)).toThrow();
  });
  it("requires every explicit engine candidate and freezes nested scores", () => {
    const payload = fixture("engine-explicit-candidate");
    const value = EngineEvidenceSchema.parse(payload);
    expect(value.requested_candidates).toHaveLength(7);
    expect(value.candidates[6].move_uci).toBe("a2a3");
    expect(() => Object.assign(value.best!.score!, { value: -999 })).toThrow();
    payload.candidates.pop();
    expect(() => EngineEvidenceSchema.parse(payload)).toThrow();
  });
  it.each(["source_revision", "explanation", "card_changes", "max_loss_cp"])("binds approval to displayed %s", async field => {
    const payload = fixture("proposal");
    const accept = ProposalDecisionSchema.parse(fixture("proposal-accept"));
    const subtle = webcrypto.subtle as unknown as SubtleCrypto;
    expect(await matchesProposalPreview(accept, ProposalSchema.parse(payload), subtle)).toBe(true);
    if (field === "source_revision") payload.source_revision = 2;
    else if (field === "explanation") payload.explanation += " changed";
    else if (field === "card_changes") payload.card_changes[0].moves = ["d2d4"];
    else {
      payload.max_loss_cp = 300;
      expect(() => ProposalSchema.parse(payload)).toThrow();
      payload.threshold_adjustment_reason = "explicit_context_change_requires_repreview";
    }
    expect(await matchesProposalPreview(accept, ProposalSchema.parse(payload), subtle)).toBe(false);
  });
  it.each(["inherited_streak", "other_route", "lost_cue", "wrong_connection", "lost_lineage"])("rejects invalid join %s", fault => {
    const payload = fixture("join-a-b-to-ab");
    expect(JoinPlanSchema.parse(payload).result.clean_streak).toBe(0);
    if (fault === "inherited_streak") payload.result.clean_streak = 3;
    else if (fault === "other_route") payload.right.route_id = "other-route";
    else if (fault === "lost_cue") payload.result.opponent_cues = [];
    else if (fault === "wrong_connection") payload.connecting_cues[0].position_key = "other-position";
    else payload.result.lineage = [];
    expect(() => JoinPlanSchema.parse(payload)).toThrow();
  });
  it.each([true, false])("preserves source-bound pre-game introduction %s without inventing a timestamp", beforeGame => {
    const payload = fixture("snapshot-route-game-introduction");
    payload.decision_presentations[0].introduced_before_game = beforeGame;
    const value = RepertoireSnapshotSchema.parse(payload);
    const binding = value.decision_presentations[0];
    expect(value.membership.routes).toHaveLength(2);
    expect(binding.route_id).toBe("route-a");
    expect(binding.route_ply).toBe(4);
    expect(binding.introduced_at).toBeNull();
    expect(binding.introduced_before_game).toBe(beforeGame);
    expect(binding.introduction_game!.game_id).toBe("synthetic-transposed-game");
    expect(() => Object.assign(binding.introduction_game!, { source_revision: 2 })).toThrow();
  });
  it.each([["route_id", null], ["route_ply", null], ["route_ply", 5], ["introduction_game", null], ["introduced_before_game", null]])("rejects partial/wrong occurrence or introduction %s", (field, value) => {
    const payload = fixture("snapshot-route-game-introduction");
    payload.decision_presentations[0][field as string] = value;
    expect(() => RepertoireSnapshotSchema.parse(payload)).toThrow();
  });
});

describe("first-departure policy compatibility", () => {
  for (const folder of ["encounters", "first-departure"]) {
    for (const file of readdirSync(resolve(directory, folder)).filter(file => file.endsWith(".json") && file !== "rejections.json")) {
      it(`round trips Python/TypeScript ${folder}/${file}`, () => {
        const { model, payload } = JSON.parse(readFileSync(resolve(directory, folder, file), "utf8"));
        expect(JSON.parse(JSON.stringify(PRODUCT_SCHEMAS[model as keyof typeof PRODUCT_SCHEMAS].parse(payload)))).toEqual(payload);
      });
    }
  }
  const rejections: { name: string; fixture: string; updates: Record<string, unknown> }[] = JSON.parse(readFileSync(resolve(directory, "first-departure/rejections.json"), "utf8")).cases;
  it.each(rejections)("rejects shared contradiction $name", ({ fixture: name, updates }) => {
    const { model, payload } = JSON.parse(readFileSync(resolve(directory, "first-departure", `${name}.json`), "utf8"));
    for (const [path, replacement] of Object.entries(updates)) {
      let current = payload;
      const parts = path.split(".");
      for (const part of parts.slice(0, -1)) current = current[part];
      current[parts.at(-1)!] = replacement;
    }
    expect(() => PRODUCT_SCHEMAS[model as keyof typeof PRODUCT_SCHEMAS].parse(payload)).toThrow();
  });
  it("retains immutable logical identity through qualification and recompared provenance", () => {
    const command = GameEncounterSchema.parse(JSON.parse(readFileSync(resolve(directory, "encounters/unintroduced-missed.json"), "utf8")).payload);
    const payload = JSON.parse(readFileSync(resolve(directory, "first-departure/own-introduced.json"), "utf8")).payload;
    const witness = FirstDepartureWitnessSchema.parse(payload);
    expect(witness.selection.source).toEqual(command.source);
    payload.configuration_revision = 8;
    payload.stream = "reconciliation";
    payload.selection.selected_scope[0].snapshot_id = "recomparison-2";
    const later = FirstDepartureWitnessSchema.parse(payload);
    expect(later.selection.source).toEqual(witness.selection.source);
    expect(logicalEffectId(command.key)).toBe(logicalEffectId({ ...command.key }));
    expect(logicalEffectId({ ...command.key, game_id: "another-genuine-game" })).not.toBe(logicalEffectId(command.key));
    expect(logicalEffectId({ ...command.key, occurrence_ply: 3 })).not.toBe(logicalEffectId(command.key));
    expect(Object.isFrozen(witness.selection.selected_scope[0])).toBe(true);
  });
  it("keeps legacy priority separate from Again and preserves new receipts inside GameDetail", () => {
    const legacyPayload = JSON.parse(readFileSync(resolve(directory, "encounters/historical-review-priority.json"), "utf8")).payload;
    const legacy = EncounterReceiptSchema.parse(legacyPayload);
    expect(legacy.applied_at).not.toBeNull();
    expect(legacy.progress).toBeUndefined();
    const currentPayload = JSON.parse(readFileSync(resolve(directory, "first-departure/introduced-again-applied.json"), "utf8")).payload;
    const detail = GameDetailSchema.parse({ ...fixture("game-detail"), receipts: [legacyPayload, currentPayload] });
    expect(JSON.parse(JSON.stringify(detail.receipts))).toEqual([legacyPayload, currentPayload]);
  });
});
