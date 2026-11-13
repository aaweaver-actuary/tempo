import { beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { dataDiagnostics, clearDataDiagnostics } from "../../app/lib/data-validation";
import { validateWorkspacePayload } from "../../app/domain/adapters/workspace-adapters";
import { queueCardsFromPayload, queueRecordWithCompatibleOpeningEvidence } from "../../app/domain/adapters/practice-card-adapters";
import { computeEssentialStudyTask } from "../../app/lib/essential-study-computation";
import { computeOptionalStudyTask } from "../../app/lib/optional-study-computation";
import { decodeStudyResult } from "../../app/lib/study-result-decoder";
import type { ValidationIssue } from "../../app/domain/validation";

beforeEach(clearDataDiagnostics);
const card = { id: "card", queue_entry_id: 1, start_fen: new Chess().fen(), moves: ["e2e4"],
  content_type: "opening", repertoire_name: "Fixture", repertoire_source: "synthetic" };

it("domain adapters do not touch the global collector without explicit effects", () => {
  const invalid = { ...card, id: "bad", moves: ["e2e5"] };
  expect(queueCardsFromPayload({ cards: [card, invalid] })).toHaveLength(1);
  expect(() => validateWorkspacePayload("/api/settings", { bad: true })).toThrow("Invalid settings data");
  expect(validateWorkspacePayload("/api/unknown", invalid)).toBe(invalid);
  expect(dataDiagnostics()).toEqual([]);
});

it("injected effects receive queue and workspace facts with original identity", () => {
  const issues: ValidationIssue[] = [], effects = { reportIssue: (issue: ValidationIssue) => issues.push(issue) };
  const invalid = { ...card, id: "bad", moves: ["e2e5"] };
  expect(queueCardsFromPayload({ cards: [card, invalid] }, effects)).toHaveLength(1);
  expect(issues).toHaveLength(1); expect(issues[0]).toMatchObject({ source: "queue card", recordId: "bad", raw: invalid });
  const settings = { bad: true };
  expect(() => validateWorkspacePayload("/api/settings", settings, undefined, effects)).toThrow("Invalid settings data");
  expect(issues.at(-1)).toMatchObject({ source: "settings", raw: settings });
  expect(dataDiagnostics()).toEqual([]);
});

it("opening evidence compatibility publishes only to the chosen sink and respects suppression", () => {
  const raw = { ...card, opening_decision_manifest: { malformed: true } }, reportIssue = vi.fn();
  const converted = queueRecordWithCompatibleOpeningEvidence(raw, { reportIssue }) as Record<string, unknown>;
  expect(converted.opening_decision_manifest).toBeUndefined(); expect(raw.opening_decision_manifest).toEqual({ malformed: true });
  expect(reportIssue).toHaveBeenCalledWith(expect.objectContaining({ source: "opening evidence", raw }));
  queueRecordWithCompatibleOpeningEvidence(raw, { reportIssue, shouldPublishDiagnostics: () => false });
  expect(reportIssue).toHaveBeenCalledTimes(1); expect(dataDiagnostics()).toEqual([]);
});

it("computation and decoder carry explicit effects while retaining reporter compatibility", () => {
  const issues: ValidationIssue[] = [], reportError = vi.fn(), effects = { reportIssue: (issue: ValidationIssue) => issues.push(issue), reportError };
  expect(() => computeEssentialStudyTask({ kind: "workspace", url: "/api/settings", payload: {} }, effects)).toThrow();
  expect(() => computeOptionalStudyTask({ kind: "workspace", url: "/api/settings", payload: {} }, effects)).toThrow();
  expect(() => decodeStudyResult({ kind: "workspace", url: "/api/settings", payload: {} }, {}, () => false, effects)).toThrow();
  expect(issues).toHaveLength(2); expect(reportError).toHaveBeenCalledTimes(3);
  expect(reportError.mock.calls[2][1]).toMatchObject({ notify: false });
  expect(() => computeEssentialStudyTask({ kind: "workspace", url: "/api/settings", payload: {} }, reportError)).toThrow();
  expect(reportError).toHaveBeenCalledTimes(4); expect(dataDiagnostics()).toEqual([]);
});

it('FE10 browser card boundary publishes invalid opening evidence through its collector', async () => {
  const { practiceCardFromQueue } = await import('../../app/utils/cards');
  const raw = { ...card, opening_decision_manifest: { malformed: true } };
  expect(practiceCardFromQueue(raw as never).backendId).toBe('card');
  expect(dataDiagnostics()).toEqual([expect.objectContaining({ source: 'opening evidence', raw })]);
});
