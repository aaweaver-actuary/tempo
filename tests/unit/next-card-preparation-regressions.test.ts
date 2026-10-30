import { afterEach, expect, it, vi } from "vitest";
import { IDBObjectStore } from "fake-indexeddb";
import { openIntentStore } from "@tempo/browser-intents";
import { closeStudyIntentStorage, initializeStudyIntentStorage, studyIntentStore } from "../../database/browser/study-intent-storage";
import { NextCardPreparationDeferredError, preparationRetryDelayMs, prepareNextCard } from "../../app/lib/next-card-preparation";
import { PendingOperationError } from "../../app/lib/operation-status";
import { studyWorkspace } from "../../app/lib/study-connection";
import * as connection from "../../app/lib/study-connection";

const completed = { progressed: true, pending: false, reason: null };
const invoke = (retryDeferred = false, signal = new AbortController().signal) => prepareNextCard({ progression: "review:original", signal, retryDeferred });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("preparation_reload_recovers_same_immutable_request_and_retains_unrelated_intents", async () => {
  const store = await studyIntentStore();
  await store.put({ key: "unrelated", kind: "other", attemptId: "other", requestId: "other", endpoint: "/other", createdAt: "original", payload: {} });
  const posts: string[] = [];
  let finished = false;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/api/operations/")) return Response.json({ operation_id: posts[0], state: finished ? "complete" : "executing", response: completed });
    posts.push(new Headers(options?.headers).get("Idempotency-Key")!);
    return Response.json({ operation_id: posts[0] }, { status: 202 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(invoke()).rejects.toBeInstanceOf(PendingOperationError);
  closeStudyIntentStorage(); await initializeStudyIntentStorage();
  finished = true;
  await expect(invoke()).resolves.toEqual(completed);
  expect(posts).toHaveLength(1);
  const records = await (await studyIntentStore()).list();
  expect(records.find(record => record.key === "unrelated")?.state).toBe("pending");
  expect(records.find(record => record.kind === "next-card-preparation")).toMatchObject({ state: "committed", attemptId: "review:original" });
});

it("preparation_missing_receipt_resends_only_original_identity_and_rejects_mismatched_receipt", async () => {
  const posts: string[] = [];
  let missing = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/api/operations/")) return missing ? new Response(null, { status: 404 })
      : Response.json({ operation_id: "another-operation", state: "complete", response: completed });
    posts.push(new Headers(options?.headers).get("Idempotency-Key")!);
    throw new TypeError("response lost");
  }));
  await expect(invoke()).rejects.toThrow("response lost");
  await expect(invoke()).rejects.toThrow("response lost");
  expect(posts).toEqual([posts[0], posts[0]]);
  missing = false;
  await expect(invoke()).rejects.toThrow("original operation identity");
  expect(posts).toHaveLength(2);
});

it("preparation_terminal_pending_requires_paced_explicit_retry_and_progress_permits_next_unit", async () => {
  let now = 1_000;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const pending = { progressed: false, pending: true, reason: "source_unavailable" };
  const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
    posts.push(new Headers(options?.headers).get("Idempotency-Key")!);
    return Response.json(posts.length === 1 ? pending : completed);
  }));
  await expect(invoke()).rejects.toBeInstanceOf(NextCardPreparationDeferredError);
  await expect(invoke(true)).rejects.toBeInstanceOf(NextCardPreparationDeferredError);
  now += preparationRetryDelayMs;
  await expect(invoke()).rejects.toBeInstanceOf(NextCardPreparationDeferredError);
  await expect(invoke(true)).resolves.toEqual(completed);
  await expect(invoke()).resolves.toEqual(completed);
  expect(new Set(posts).size).toBe(3);
});

it("preparation_claims_coordinate_two_contexts_and_aborted_delivery_releases_original_claim", async () => {
  let posted!: () => void;
  const admitted = new Promise<void>(resolve => { posted = resolve; });
  let requestId = "";
  let finished = false;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/api/operations/")) return Response.json({ operation_id: requestId, state: finished ? "complete" : "executing", response: completed });
    requestId = new Headers(options?.headers).get("Idempotency-Key")!;
    posted();
    return new Promise<Response>((_resolve, reject) => options!.signal!.addEventListener("abort", () => reject(options!.signal!.reason), { once: true }));
  });
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const first = invoke(false, controller.signal);
  const rejected = expect(first).rejects.toThrow("superseded");
  await admitted;
  const peer = await openIntentStore({ workspace: studyWorkspace() });
  try {
    const records = await peer.list("next-card-preparation");
    expect(records).toHaveLength(1);
    expect(await peer.claim(records[0].key, "peer-tab", Date.now(), 60_000)).toBeUndefined();
    await expect(invoke()).rejects.toBeInstanceOf(PendingOperationError);
    controller.abort(new Error("superseded")); await rejected;
    expect((await peer.get(records[0].key))?.claim).toBeUndefined();
    finished = true;
    await expect(invoke()).resolves.toEqual(completed);
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/api/queue/prepare-next"))).toHaveLength(1);
  } finally { peer.close(); }
});

it("preparation_identity_reservation_survives_failed_record_retention_without_untracked_post", async () => {
  const original = IDBObjectStore.prototype.add;
  const fault = vi.spyOn(IDBObjectStore.prototype, "add").mockImplementation(function (this: IDBObjectStore, value, key) {
    if (this.name === "intents" && value.kind === "next-card-preparation") throw new Error("storage full");
    return original.call(this, value, key);
  });
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(completed)); vi.stubGlobal("fetch", fetcher);
  await expect(invoke()).rejects.toThrow("storage full");
  expect(fetcher).not.toHaveBeenCalled();
  const store = await studyIntentStore();
  const reserved = await store.checkpoint("next-card-preparation:current") as { requestId: string };
  fault.mockRestore();
  await expect(invoke()).resolves.toEqual(completed);
  expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Idempotency-Key")).toBe(reserved.requestId);
});

it("preparation_invalid_terminal_response_stays_unresolved_and_recovers_its_original_receipt", async () => {
  let requestId = "";
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/api/operations/")) return Response.json({ operation_id: requestId, state: "complete", response: completed });
    requestId = new Headers(options?.headers).get("Idempotency-Key")!;
    return Response.json({ progressed: true });
  }));
  await expect(invoke()).rejects.toThrow("invalid result");
  expect((await (await studyIntentStore()).list("next-card-preparation"))[0].state).toBe("pending");
  await expect(invoke()).resolves.toEqual(completed);
});

it("preparation_simultaneous_contexts_reserve_one_operation_before_first_post", async () => {
  const posts: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/api/operations/")) return Response.json({ operation_id: posts[0], state: "executing" });
    posts.push(new Headers(options?.headers).get("Idempotency-Key")!);
    return Response.json({ operation_id: posts[0] }, { status: 202 });
  }));
  const outcomes = await Promise.allSettled([invoke(), prepareNextCard({ progression: "other-tab", signal: new AbortController().signal })]);
  expect(outcomes.every(outcome => outcome.status === "rejected" && outcome.reason instanceof PendingOperationError)).toBe(true);
  expect(posts).toHaveLength(1);
  expect(await (await studyIntentStore()).list("next-card-preparation")).toHaveLength(1);
});

it("preparation_authority_change_retains_old_command_without_delivering_to_new_authority", async () => {
  let requestId = "";
  let finish!: (response: Response) => void;
  const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
    requestId = new Headers(options?.headers).get("Idempotency-Key")!;
    return new Promise<Response>(resolve => { finish = resolve; });
  });
  vi.stubGlobal("fetch", fetcher);
  const pending = invoke();
  const rejected = expect(pending).rejects.toThrow("authority changed");
  await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
  vi.spyOn(connection, "studyWorkspace").mockReturnValue("new-authority");
  finish(Response.json(completed));
  await rejected;
  const records = await (await studyIntentStore()).list("next-card-preparation");
  expect(records[0]).toMatchObject({ requestId, state: "pending" });
  expect(fetcher).toHaveBeenCalledOnce();
});
