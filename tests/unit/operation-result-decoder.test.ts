import { describe, expect, it } from "vitest";
import fixture from "../fixtures/study-operation-contract.json";
import { decodeOperationAcceptance, decodeOperationResult } from "../../app/domain/adapters/operation-result-adapter";

const id = fixture.expected_operation_id;
describe("operation receipt decoder", () => {
  it.each(fixture.receipts)("decodes fixture $name in both identity modes", ({ wire, kind, name }) => {
    expect(decodeOperationResult(wire, id, true).kind).toBe(kind);
    expect(decodeOperationResult(wire, id, false).kind).toBe(name === "missing_identity" ? "complete" : kind);
  });
  it.each(["queued", "executing", "retrying", "pending"])("normalizes %s", state => {
    expect(decodeOperationResult({ state }, id, false)).toEqual({ kind: "pending", operationId: id });
  });
  it.each([null, [], "complete", {}, { state: "complete", response: {} , operation_id: null },
    { state: "complete", response: {}, operation_id: 42 }, { state: "failed", error: "bad" },
    { state: "failed", error: { retryable: "false" } }, { state: "blocked", last_error: [] },
  ])("keeps invalid input unconfirmed: %j", wire => {
    expect(decodeOperationResult(wire, id, false).kind).toBe("malformed");
  });
  it.each(["other", null, 42])("classifies present invalid identity %j distinctly", operation_id => {
    expect(decodeOperationResult({ operation_id, state: "complete", response: {} }, id, false))
      .toEqual({ kind: "malformed", operationId: id, reason: "identity" });
  });
  it("requires identity only in the required mode and keeps other malformed reasons separate", () => {
    const wire = { state: "complete", response: {} };
    expect(decodeOperationResult(wire, id, true)).toEqual({ kind: "malformed", operationId: id, reason: "identity" });
    expect(decodeOperationResult(wire, id, false)).toEqual({ kind: "complete", operationId: id, response: {} });
    expect(decodeOperationResult({ operation_id: id, state: "complete" }, id, true))
      .toEqual({ kind: "malformed", operationId: id });
  });
  it("accepts an explicit null completion response", () => {
    expect(decodeOperationResult({ operation_id: id, state: "complete", response: null }, id, true))
      .toEqual({ kind: "complete", operationId: id, response: null });
  });
});

describe("initial operation acknowledgment decoder", () => {
  it.each(["queued", "executing", "retrying", "pending", "unknown", "blocked"])("accepts existing initial %s state", state => {
    expect(decodeOperationAcceptance({ operation_id: id, state })).toEqual({
      kind: state === "unknown" || state === "blocked" ? state : "pending", operationId: id,
    });
  });
  it.each([undefined, null, 42, "", "a".repeat(129), "../other", "has space", "x?query", "x#fragment", "x%2Fy"])(
    "rejects unsafe or missing initial identity %j", operation_id => {
      expect(decodeOperationAcceptance({ operation_id, state: "pending" })).toEqual({ kind: "malformed" });
    });
  it.each([undefined, null, 42, "unrecognized", "complete", "failed"])("rejects initial state %j even with a response", state => {
    expect(decodeOperationAcceptance({ operation_id: id, state, response: { cards: [] } })).toEqual({ kind: "malformed" });
  });
  it("preserves acknowledgment-only fields and inclusive identity bounds", () => {
    for (const operation_id of ["x", "a".repeat(128), "queue.issue_head:v1:abc_def-123"])
      expect(decodeOperationAcceptance({ operation_id, state: "blocked", last_error: "not a status receipt" }))
        .toEqual({ kind: "blocked", operationId: operation_id });
    for (const value of [null, [], "pending", {}])
      expect(decodeOperationAcceptance(value)).toEqual({ kind: "malformed" });
  });
});
