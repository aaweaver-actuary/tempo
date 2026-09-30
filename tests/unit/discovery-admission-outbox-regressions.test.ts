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

it("legacy oversized stored operation key is repaired only after its confirmed rejection", () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    ...hashAdmission, operationId: "x".repeat(140), state: "failed",
    error: "Idempotency-Key must be at most 128 characters",
  }]));
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "pending" });
  expect(pendingDiscoveryAdmissions()[0].operationId.length).toBeLessThanOrEqual(128);
});

it("uncertain invalid stored operation key remains intact with an actionable error", () => {
  const saved = JSON.stringify([{ ...hashAdmission, operationId: "x".repeat(140), state: "pending" }]);
  localStorage.setItem("tempo-pending-discovery-admissions-v1", saved);
  expect(() => recoverUnacknowledgedDiscoveryAdmissions()).toThrow("Saved discovery requests are invalid");
  expect(localStorage.getItem("tempo-pending-discovery-admissions-v1")).toBe(saved);
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

it("two indefinitely preparing admissions cannot starve a later unsent discovery", async () => {
  for (const opportunityId of ["first", "second", "third"])
    enqueuePendingDiscoveryAdmission({ ...admission, opportunityId });
  const submitted: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/accept")) {
      const opportunityId = url.split("/discoveries/")[1].split("/")[0];
      submitted.push(opportunityId);
      return Response.json({ status: "preparing", intent_id: `intent-${opportunityId}` });
    }
    return Response.json({ state: "preparing", error: null });
  }));
  await flushPendingDiscoveryAdmissions();
  await flushPendingDiscoveryAdmissions();
  expect(submitted).toEqual(["first", "second", "third"]);
  expect(pendingDiscoveryAdmissions()).toHaveLength(3);
});

it("a large saved discovery backlog receives bounded submission service", async () => {
  for (let admissionNumber = 0; admissionNumber < 24; admissionNumber += 1)
    enqueuePendingDiscoveryAdmission({ ...admission, opportunityId: `gap-${admissionNumber}` });
  const submitted: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/accept")) {
      const opportunityId = url.split("/discoveries/")[1].split("/")[0];
      submitted.push(opportunityId);
      return Response.json({ status: "preparing", intent_id: `intent-${opportunityId}` });
    }
    return Response.json({ state: "preparing", error: null });
  }));
  for (let flushNumber = 0; flushNumber < 12; flushNumber += 1)
    await flushPendingDiscoveryAdmissions();
  expect(new Set(submitted).size).toBe(24);
  expect(pendingDiscoveryAdmissions()).toHaveLength(24);
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

it("confirms later discovery C within three eligible flushes while A and B keep preparing", async () => {
  for (const opportunityId of ["A", "B", "C"])
    enqueuePendingDiscoveryAdmission({ ...admission, opportunityId });
  const confirmationFlushes: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.endsWith("/accept")) {
      const opportunityId = url.split("/discoveries/")[1].split("/")[0];
      return Response.json({ status: "preparing", intent_id: `intent-${opportunityId}` });
    }
    const opportunityId = url.split("/discovery-admissions/intent-")[1];
    confirmationFlushes.push(opportunityId);
    return Response.json({ state: opportunityId === "C" ? "queued" : "preparing", error: null });
  }));

  let confirmationsBeforeC = 0;
  for (let eligibleFlush = 1; eligibleFlush <= 3; eligibleFlush += 1) {
    await flushPendingDiscoveryAdmissions();
    if (confirmationFlushes.includes("C")) break;
    confirmationsBeforeC = eligibleFlush;
  }
  expect(confirmationFlushes).toContain("C");
  expect(confirmationFlushes.filter((id) => id === "C").length).toBe(1);
  expect(pendingDiscoveryAdmissions().map(({ opportunityId }) => opportunityId)).toEqual(["A", "B"]);
  expect(confirmationsBeforeC).toBeLessThanOrEqual(3);
});

it("stalled discovery confirmations cannot starve a later queued admission across reloads", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify(
    ["a", "b", "c"].map((opportunityId) => ({
      ...admission, opportunityId, operationId: `operation-${opportunityId}`,
      intentId: `intent-${opportunityId}`, state: "accepted",
    })),
  ));
  const polled: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const intentId = url.split("/discovery-admissions/")[1];
    polled.push(intentId);
    return Response.json({ state: intentId === "intent-c" ? "queued" : "preparing" });
  }));
  await flushPendingDiscoveryAdmissions("a");
  await vi.resetModules();
  const reloaded = await import("../../app/lib/discovery-admission-outbox");
  await reloaded.flushPendingDiscoveryAdmissions("a");
  expect(polled).toContain("intent-c");
  expect(reloaded.pendingDiscoveryAdmissions().map((item) => item.opportunityId))
    .toEqual(["a", "b"]);
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
