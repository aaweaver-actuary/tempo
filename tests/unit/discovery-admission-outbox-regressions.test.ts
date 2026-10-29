import { beforeEach, expect, it, vi } from "vitest";
import { DISCOVERY_ADMISSION_QUEUED, enqueuePendingDiscoveryAdmission, flushPendingDiscoveryAdmissions,
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

it("accepted discovery reload preserves its operation and confirms its original intent", async () => {
  enqueuePendingDiscoveryAdmission(hashAdmission);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ intent_id: "intent" }))
    .mockResolvedValueOnce(Response.json({ state: "queued" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  const original = pendingDiscoveryAdmissions()[0];
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toEqual(original);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher.mock.calls[1][0]).toContain("/api/discovery-admissions/intent");
  expect(fetcher.mock.calls[1][1]?.method).not.toBe("POST");
  expect(pendingDiscoveryAdmissions()).toEqual([]);
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

it("unknown admission outcome cannot become success", async () => {
  vi.useFakeTimers();
  try {
    enqueuePendingDiscoveryAdmission(admission);
    const original = pendingDiscoveryAdmissions()[0];
    const queued = vi.spyOn(window, "dispatchEvent");
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
    expect(pendingDiscoveryAdmissions()[0]).toMatchObject(original);
    expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
    await vi.resetModules();
    const reloaded = await import("../../app/lib/discovery-admission-outbox");
    reloaded.recoverUnacknowledgedDiscoveryAdmissions();
    await vi.advanceTimersByTimeAsync(3_000);
    await reloaded.flushPendingDiscoveryAdmissions();
    expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ ...original, state: "accepted", intentId: "intent" });
    expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
    await reloaded.flushPendingDiscoveryAdmissions();
    expect(pendingDiscoveryAdmissions()).toEqual([]);
    expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(1);
    expect(new Headers(fetcher.mock.calls[1][1].headers).get("Idempotency-Key")).toBe(original.operationId);
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

it("legacy accepted discovery keeps its intent while waiting for preparation after reopen", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    ...admission, intentId: "old-intent", state: "accepted",
  }]));
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ state: "accepted", intentId: "old-intent" });
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ state: "preparing" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher.mock.calls[0][0]).toContain("/api/discovery-admissions/old-intent");
  expect(fetcher.mock.calls[0][1]?.method).not.toBe("POST");
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

it("terminal active-discovery 404 is retained without automatic replay", async () => {
  vi.useFakeTimers();
  try {
    enqueuePendingDiscoveryAdmission(hashAdmission);
    const original = pendingDiscoveryAdmissions()[0];
    const queued = vi.spyOn(window, "dispatchEvent");
    const fetcher = vi.fn().mockResolvedValue(Response.json(
      { detail: "Active discovery not found" }, { status: 404 },
    ));
    vi.stubGlobal("fetch", fetcher);
    await flushPendingDiscoveryAdmissions();
    const failed = { ...original, state: "failed", error: "Active discovery not found" };
    expect(pendingDiscoveryAdmissions()).toEqual([failed]);
    await vi.resetModules();
    const reloaded = await import("../../app/lib/discovery-admission-outbox");
    reloaded.recoverUnacknowledgedDiscoveryAdmissions();
    for (const elapsed of [3_000, 30_000, 86_400_000]) {
      await vi.advanceTimersByTimeAsync(elapsed);
      await reloaded.flushPendingDiscoveryAdmissions();
    }
    expect(reloaded.pendingDiscoveryAdmissions()).toEqual([failed]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1].method).toBe("POST");
    expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
  } finally { vi.useRealTimers(); }
});

it("legacy active-discovery failure with accepted intent resumes status polling", async () => {
  const original = { ...hashAdmission, operationId: "original-operation", intentId: "original-intent",
    state: "failed", error: "Active discovery not found" };
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([original]));
  const queued = vi.spyOn(window, "dispatchEvent");
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()).toEqual([{ ...original, state: "accepted", error: undefined }]);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ state: "preparing" }))
    .mockResolvedValueOnce(Response.json({ state: "queued" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ ...hashAdmission,
    operationId: original.operationId, intentId: original.intentId, state: "accepted" });
  expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [url, options] of fetcher.mock.calls) {
    expect(url).toContain("/api/discovery-admissions/original-intent");
    expect(options?.method ?? "GET").toBe("GET");
  }
  expect(pendingDiscoveryAdmissions()).toEqual([]);
  const completions = queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED);
  expect(completions).toHaveLength(1);
  expect((completions[0][0] as CustomEvent).detail).toEqual({
    opportunityId: hashAdmission.opportunityId, evidenceFingerprint: hashAdmission.evidenceFingerprint,
  });
});

it("explicit retry of a terminal discovery retains the original choice", async () => {
  const original = { ...hashAdmission, operationId: "rejected-operation", state: "failed",
    error: "Active discovery not found" };
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([original]));
  const queued = vi.spyOn(window, "dispatchEvent");
  let accept!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { accept = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  const retry = retryPendingDiscoveryAdmission(original.opportunityId);
  const pending = pendingDiscoveryAdmissions()[0];
  expect(pending).toMatchObject({ ...hashAdmission, state: "pending", retryCount: 0 });
  expect(pending.operationId).not.toBe(original.operationId);
  expect(pending.intentId).toBeUndefined();
  expect(fetcher.mock.calls[0][0]).toContain(`/api/discoveries/${original.opportunityId}/accept`);
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key")).toBe(pending.operationId);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    selected_move_uci: original.selectedMoveUci, evidence_fingerprint: original.evidenceFingerprint,
  });
  expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
  accept(Response.json({ intent_id: "new-intent" }));
  await retry;
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ ...pending, state: "accepted", intentId: "new-intent" });
  expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
});

it("failed records do not prevent independent pending saves", async () => {
  const failures = ["failed-one", "failed-two", "failed-three"].map((opportunityId) => ({
    ...admission, opportunityId, operationId: `operation-${opportunityId}`, state: "failed",
    error: "Active discovery not found",
  }));
  const pending = { ...admission, opportunityId: "pending", operationId: "pending-operation", state: "pending" };
  const accepted = { ...admission, opportunityId: "accepted", operationId: "accepted-operation",
    intentId: "existing-intent", state: "accepted" };
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([...failures, pending, accepted]));
  const queued = vi.spyOn(window, "dispatchEvent");
  const fetcher = vi.fn(async (url: string) => url.endsWith("/accept")
    ? Response.json({ intent_id: "new-intent" }) : Response.json({ state: "queued" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    expect.stringContaining("/api/discoveries/pending/accept"),
    expect.stringContaining("/api/discovery-admissions/existing-intent"),
  ]);
  expect(pendingDiscoveryAdmissions()).toEqual([...failures, { ...pending, intentId: "new-intent",
    state: "accepted", retryCount: 0 }]);
  const completions = queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED);
  expect(completions).toHaveLength(1);
  expect((completions[0][0] as CustomEvent).detail.opportunityId).toBe("accepted");
});

it("overlapping discovery flushes share one active submission identity", async () => {
  enqueuePendingDiscoveryAdmission(admission);
  const original = pendingDiscoveryAdmissions()[0];
  let accept!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { accept = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  const first = flushPendingDiscoveryAdmissions();
  const overlapping = flushPendingDiscoveryAdmissions();
  expect(overlapping).toBe(first);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key")).toBe(original.operationId);
  accept(Response.json({ intent_id: "one-intent" }));
  await Promise.all([first, overlapping]);
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ ...original, state: "accepted", intentId: "one-intent" });
});


it("legacy accepted intent that cannot be confirmed retains unresolved truth", async () => {
  const original = { ...hashAdmission, operationId: "original-operation", intentId: "missing-intent",
    state: "failed", error: "Active discovery not found" };
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([original]));
  const queued = vi.spyOn(window, "dispatchEvent");
  recoverUnacknowledgedDiscoveryAdmissions();
  const fetcher = vi.fn().mockResolvedValue(Response.json(
    { detail: "Discovery admission was not found" }, { status: 404 },
  ));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ ...original,
    error: "Discovery admission was not found" });
  recoverUnacknowledgedDiscoveryAdmissions();
  await flushPendingDiscoveryAdmissions();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toContain("/api/discovery-admissions/missing-intent");
  expect(fetcher.mock.calls[0][1]?.method ?? "GET").toBe("GET");
  expect(queued.mock.calls.filter(([event]) => event.type === DISCOVERY_ADMISSION_QUEUED)).toHaveLength(0);
});
