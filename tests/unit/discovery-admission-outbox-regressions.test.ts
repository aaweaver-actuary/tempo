import { beforeEach, expect, it, vi } from "vitest";
import { enqueuePendingDiscoveryAdmission, flushPendingDiscoveryAdmissions,
  pendingDiscoveryAdmissions, recoverUnacknowledgedDiscoveryAdmissions,
  retryPendingDiscoveryAdmission } from
  "../../app/lib/discovery-admission-outbox";

beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

const admission = { opportunityId: "gap", selectedMoveUci: "f3e5", evidenceFingerprint: "revision" };
const hashAdmission = { opportunityId: "a".repeat(64), selectedMoveUci: "g1f3",
  evidenceFingerprint: "b".repeat(64) };

it("legacy rejected discovery saves recover with a bounded key after reload", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    ...hashAdmission, state: "failed",
    error: "Idempotency-Key must be at most 128 characters",
  }]));
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({
    ...hashAdmission, state: "pending",
  });
  const fetcher = vi.fn().mockResolvedValue(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key")?.length).toBeLessThanOrEqual(128);
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "intent" });
});

it("uncertain discovery retries retain one operation key and the same choice", async () => {
  vi.useFakeTimers();
  try {
    enqueuePendingDiscoveryAdmission(hashAdmission);
    const fetcher = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "temporary outage" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
    vi.stubGlobal("fetch", fetcher);
    await flushPendingDiscoveryAdmissions();
    await vi.advanceTimersByTimeAsync(3_000);
    await flushPendingDiscoveryAdmissions();
    const firstRequest = fetcher.mock.calls[0][1];
    const retryRequest = fetcher.mock.calls[1][1];
    expect(new Headers(firstRequest.headers).get("Idempotency-Key")).toBe(
      new Headers(retryRequest.headers).get("Idempotency-Key"));
    expect(firstRequest.body).toBe(retryRequest.body);
    expect(new Headers(firstRequest.headers).get("Idempotency-Key")?.length).toBeLessThanOrEqual(128);
  } finally { vi.useRealTimers(); }
});

it("accepted discovery readmission after reload uses a new operation key", async () => {
  enqueuePendingDiscoveryAdmission(hashAdmission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }))
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  recoverUnacknowledgedDiscoveryAdmissions();
  await flushPendingDiscoveryAdmissions();
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key")).not.toBe(
    new Headers(fetcher.mock.calls[1][1].headers).get("Idempotency-Key"));
  expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body);
});

it("terminal discovery retry uses a new operation key for the same choice", async () => {
  enqueuePendingDiscoveryAdmission(hashAdmission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: "publication stopped" }))
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0].state).toBe("failed");
  await retryPendingDiscoveryAdmission(hashAdmission.opportunityId);
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key")).not.toBe(
    new Headers(fetcher.mock.calls[2][1].headers).get("Idempotency-Key"));
  expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[2][1].body);
});

it("failed discovery operation receipt requires a new key on explicit retry", async () => {
  enqueuePendingDiscoveryAdmission(hashAdmission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ operation_id: "first", state: "pending" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "worker failed" } }))
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "failed", error: "worker failed" });
  await retryPendingDiscoveryAdmission(hashAdmission.opportunityId);
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key")).not.toBe(
    new Headers(fetcher.mock.calls[2][1].headers).get("Idempotency-Key"));
});

it("discovery replay sends at most two due admissions and prioritizes the new choice", async () => {
  for (const opportunityId of ["old-one", "old-two", "new-choice"])
    enqueuePendingDiscoveryAdmission({ ...admission, opportunityId });
  const acceptedIds: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/discoveries/") && url.endsWith("/accept")) {
      const opportunityId = url.split("/discoveries/")[1].split("/")[0];
      acceptedIds.push(opportunityId);
      return Response.json({ status: "preparing", intent_id: `intent-${opportunityId}` });
    }
    return Response.json({ state: "preparing" });
  }));
  await flushPendingDiscoveryAdmissions("new-choice");
  expect(acceptedIds).toHaveLength(2);
  expect(acceptedIds[0]).toBe("new-choice");
  await flushPendingDiscoveryAdmissions();
  expect(acceptedIds).toHaveLength(3);
});

it("discovery save outbox survives reload and waits for queued confirmation", async () => {
  enqueuePendingDiscoveryAdmission(admission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }))
    .mockResolvedValueOnce(Response.json({ state: "preparing", error: null }))
    .mockResolvedValueOnce(Response.json({ state: "queued", error: null }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ intentId: "intent", state: "accepted" });
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()).toHaveLength(1);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()).toEqual([]);
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/accept"))).toHaveLength(1);
});

it("legacy discovery admission 202 keeps its intent receipt without a Celery operation ID", async () => {
  enqueuePendingDiscoveryAdmission(admission);
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json(
    { status: "preparing", intent_id: "legacy-intent" }, { status: 202 },
  ));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({
    state: "accepted", intentId: "legacy-intent",
  });
  expect(pendingDiscoveryAdmissions()[0].error).toBeUndefined();
});

it("timed out discovery save remains unconfirmed and replays the same choice after reload", async () => {
  vi.useFakeTimers();
  try {
    enqueuePendingDiscoveryAdmission(admission);
    const fetcher = vi.fn()
      .mockImplementationOnce((_url: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
        options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }))
      .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }))
      .mockResolvedValueOnce(Response.json({ state: "queued", error: null }));
    vi.stubGlobal("fetch", fetcher);
    const firstFlush = flushPendingDiscoveryAdmissions();
    await vi.advanceTimersByTimeAsync(15_000);
    await firstFlush;
    expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "pending" });
    recoverUnacknowledgedDiscoveryAdmissions();
    await vi.advanceTimersByTimeAsync(3_000);
    await flushPendingDiscoveryAdmissions();
    expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "intent" });
    await flushPendingDiscoveryAdmissions();
    expect(pendingDiscoveryAdmissions()).toEqual([]);
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/accept"))).toHaveLength(2);
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
  } finally { vi.useRealTimers(); }
});

it("legacy failed discovery timeout reopens as an unconfirmed save with the same choice", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    ...admission, intentId: "old-intent", state: "failed",
    error: "Discovery save timed out after 15 seconds. Retry save.",
  }]));
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({
    ...admission, state: "pending",
  });
  expect(pendingDiscoveryAdmissions()[0].intentId).toBeUndefined();
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "intent" });
  expect(fetcher.mock.calls[0][1].body).toContain("f3e5");
});

it("accepted discovery save replays its same choice after reopen to unblock preparation", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    ...admission, intentId: "old-intent", state: "accepted",
  }]));
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "pending" });
  expect(pendingDiscoveryAdmissions()[0].intentId).toBeUndefined();
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "old-intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher.mock.calls[0][1].body).toContain("f3e5");
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "old-intent" });
});

it("confirmed discovery rejection stays visible and an explicit retry reuses the same request", async () => {
  enqueuePendingDiscoveryAdmission(admission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ detail: "stale evidence" }, { status: 409 }))
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "failed", error: "stale evidence" });
  await flushPendingDiscoveryAdmissions();
  expect(fetcher).toHaveBeenCalledTimes(1);
  await retryPendingDiscoveryAdmission("gap");
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "intent" });
  expect(fetcher.mock.calls[1][1].body).toContain("f3e5");
});

it("transient discovery server failure is replayed after reload", async () => {
  vi.useFakeTimers();
  try {
  enqueuePendingDiscoveryAdmission(admission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ detail: "temporary outage" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0].state).toBe("pending");
  recoverUnacknowledgedDiscoveryAdmissions();
  await vi.advanceTimersByTimeAsync(3_000);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "intent" });
  } finally { vi.useRealTimers(); }
});

it("terminal discovery admission failure remains retryable after acknowledgment", async () => {
  enqueuePendingDiscoveryAdmission(admission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: "publication stopped" }))
    .mockResolvedValueOnce(Response.json({ status: "preparing", intent_id: "intent" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "failed", error: "publication stopped" });
  await retryPendingDiscoveryAdmission("gap");
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "intent" });
});
