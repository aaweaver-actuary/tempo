import { expect, it, vi } from "vitest";
import * as z from "zod";
import { formatValidationIssues, parseData, recordIdentity, validRecords, type ValidationIssue } from "../../app/domain/validation";
import { clearDataDiagnostics, dataDiagnostics } from "../../app/lib/data-validation";

const schema = z.object({ id: z.string(), value: z.number() });

it("pure validation filters and throws without time, microtasks or global publication", () => {
  clearDataDiagnostics();
  const clock = vi.spyOn(Date.prototype, "toISOString").mockImplementation(() => { throw new Error("clock used"); });
  const microtask = vi.spyOn(globalThis, "queueMicrotask").mockImplementation(() => { throw new Error("microtask used"); });
  try {
    expect(parseData(schema, { id: "ok", value: 2 }, "queue")).toEqual({ id: "ok", value: 2 });
    expect(() => parseData(schema, { id: "bad", value: "no" }, "queue")).toThrow("Invalid queue data: value:");
    expect(validRecords(schema, [{ id: "bad", value: "no" }, { id: "ok", value: 2 }], "queue")).toEqual([{ id: "ok", value: 2 }]);
    expect(dataDiagnostics()).toEqual([]);
    expect(clock).not.toHaveBeenCalled(); expect(microtask).not.toHaveBeenCalled();
  } finally { clock.mockRestore(); microtask.mockRestore(); }
});

it("explicit effects receive each issue and the exact thrown Error and context", () => {
  const issues: ValidationIssue[] = []; const errors: Array<{ error: Error; context: unknown }> = [];
  const raw = { id: 7, PuzzleId: "puzzle", value: "no" };
  const effects = { reportIssue: (issue: ValidationIssue) => issues.push(issue),
    reportError: (error: Error, context: unknown) => errors.push({ error, context }) };
  let thrown: unknown;
  try { parseData(schema, raw, "cards", { endpoint: "/api/cards", debugSource: "worker", effects }); }
  catch (error) { thrown = error; }
  expect(issues).toHaveLength(1); expect(issues[0]).toMatchObject({ source: "cards", recordId: "7", raw });
  expect(errors[0]).toEqual({ error: thrown, context: { kind: "data-validation", source: "worker", operation: "schema validation", endpoint: "/api/cards", notify: true } });
  expect((thrown as Error).message).toBe(`Invalid cards data: ${issues[0].message}`);
  expect(validRecords(schema, [raw, raw], "cards", effects)).toEqual([]);
  expect(issues).toHaveLength(3); expect(errors).toHaveLength(3);
});

it("suppression omits diagnostic publication but reports the same failure with notify false", () => {
  const reportIssue = vi.fn(), reportError = vi.fn();
  expect(() => parseData(schema, { id: "x", value: "no" }, "cards", {
    effects: { reportIssue, reportError, shouldPublishDiagnostics: () => false },
  })).toThrow("Invalid cards data");
  expect(reportIssue).not.toHaveBeenCalled();
  expect(reportError).toHaveBeenCalledWith(expect.any(Error), expect.objectContaining({ notify: false }));
});

it("keeps record identity priority and nested Zod issue formatting", () => {
  expect(recordIdentity({ id: 0, PuzzleId: "p", queue_entry_id: 3, key: "k" })).toBe("0");
  expect(recordIdentity({ id: null, PuzzleId: "p", queue_entry_id: 3 })).toBe("p");
  expect(recordIdentity({ queue_entry_id: 3, key: "k" })).toBe("3");
  expect(recordIdentity({ key: "k" })).toBe("k");
  expect(recordIdentity(null)).toBeUndefined();
  expect(formatValidationIssues([{ path: [], message: "bad" }, { path: ["nested", 0], message: "missing" }])).toBe("record: bad; nested.0: missing");
});
