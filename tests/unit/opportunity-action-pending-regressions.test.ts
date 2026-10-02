import { afterEach, expect, it, vi } from "vitest";
import { applyOpportunityCommand } from "../../app/lib/opportunity-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending discovery action reuses its operation ID and blocks another action on that discovery", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: { acknowledged: true },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(applyOpportunityCommand("white", "discovery-1", "acknowledge"))
    .rejects.toBeInstanceOf(PendingOperationError);
  await expect(applyOpportunityCommand("white", "discovery-1", "dismiss"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(keys).toHaveLength(1);
  await applyOpportunityCommand("white", "discovery-1", "acknowledge");
  expect(keys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-opportunity-v1:discovery-1")).toBeNull();
});

it("training a saved discovery waits for a confirmed queue receipt", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads === 1 ? { state: "pending" } : {
        state: "complete", response: { card_id: "card-1", queued: true, idempotent: false },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(applyOpportunityCommand("white", "discovery-2", "train", "revision-A"))
    .rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-opportunity-v1:discovery-2")).not.toBeNull();
  await applyOpportunityCommand("white", "discovery-2", "train", "revision-A");
  expect(keys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-opportunity-v1:discovery-2")).toBeNull();
});


it("a completed training receipt for older evidence cannot confirm the current revision", async () => {
  localStorage.setItem("tempo-pending-opportunity-v1:discovery-3", JSON.stringify({
    operationId: "old-training", repertoireId: "white", action: "train", evidenceFingerprint: "A",
  }));
  const fetcher = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async (input) => String(input).includes("/api/operations/")
    ? Response.json({ state: "complete", response: { card_id: "card", queued: true, idempotent: false } })
    : Response.json({ card_id: "card", queued: true, idempotent: false }));
  vi.stubGlobal("fetch", fetcher);
  await applyOpportunityCommand("white", "discovery-3", "train", "B");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1]).toEqual(expect.arrayContaining([expect.objectContaining({ body: JSON.stringify({ evidence_fingerprint: "B" }) })]));
});


it.each(["pending", "complete"])("legacy fingerprintless Train %s cannot confirm or resubmit current evidence", async (state) => {
  const key = "tempo-pending-opportunity-v1:legacy-train";
  localStorage.setItem(key, JSON.stringify({ operationId: "legacy-id", repertoireId: "white", action: "train" }));
  const fetcher = vi.fn(async () => Response.json({ state,
    response: { card_id: "card", queued: true, idempotent: false } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(applyOpportunityCommand("white", "legacy-train", "train", "current-B"))
    .rejects.toThrow(/review.*evidence.*train again/i);
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem(key)).toBeNull();
});

it("revisionless Train calls are rejected before a request or pending action is created", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  // Exercise untyped legacy callers; the typed API must require a train revision.
  await expect((applyOpportunityCommand as (...args: unknown[]) => Promise<void>)("white", "missing", "train"))
    .rejects.toThrow(/evidence revision/i);
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem("tempo-pending-opportunity-v1:missing")).toBeNull();
});
