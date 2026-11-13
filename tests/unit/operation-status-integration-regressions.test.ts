import { afterEach, expect, it, vi } from "vitest";
import { confirmOriginalOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "../../app/lib/operation-status";
import { subscribeOperationStatusChange } from "../../app/lib/operation-status-events";

vi.hoisted(() => vi.resetModules());
vi.mock("../../app/lib/study-connection", async importOriginal => {
  const actual = await importOriginal<typeof import("../../app/lib/study-connection")>();
  return { ...actual, defaultStudyClient: { ...actual.defaultStudyClient, origin: "https://learning.example" } };
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each([
  ["foreign", JSON.stringify({ operation_id: "foreign", state: "queued" })],
  ["absent", JSON.stringify({ state: "queued" })],
  ["nonstring", JSON.stringify({ operation_id: 42, state: "queued" })],
  ["empty", JSON.stringify({ operation_id: "", state: "queued" })],
  ["overlength", JSON.stringify({ operation_id: "x".repeat(129), state: "queued" })],
  ["unsupported state", JSON.stringify({ operation_id: "original", state: "complete" })],
  ["malformed JSON", "{"],
])("original confirmation rejects %s before polling or notification", async (_label, body) => {
  const request = vi.fn(); const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  try {
    await expect(confirmOriginalOperationResponse(new Response(body, { status: 202 }), "original", { fetch: request }))
      .rejects.toMatchObject({ name: "PendingOperationError", operationId: "original" });
    expect(request).not.toHaveBeenCalled(); expect(notice).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

it("confirms the exact gateway-valid generic ID with URL encoding", async () => {
  const id = "original /?%#";
  const request = vi.fn(async () => Response.json({ operation_id: id, state: "complete", response: { saved: true } }));
  const response = await confirmOriginalOperationResponse(Response.json({ operation_id: id, state: "queued" }, { status: 202 }), id, { fetch: request });
  expect(request).toHaveBeenCalledWith(`https://learning.example/api/operations/${encodeURIComponent(id)}`, undefined);
  await expect(response.json()).resolves.toEqual({ saved: true });
});

it("Study receipt callers retain the selected Learning origin by default", async () => {
  const operationId = "original-study-operation";
  const request = vi.fn(async () => Response.json({ operation_id: operationId, state: "complete", response: { saved: true } }));
  const response = await readOperationResponse(operationId, { fetch: request });
  expect(request).toHaveBeenCalledWith(`https://learning.example/api/operations/${operationId}`, undefined);
  await expect(response.json()).resolves.toEqual({ saved: true });
});

it.each(["foreground", "background", "custom"] as const)("operation receipt polling honors abort with %s options", async lane => {
  const controller = new AbortController();
  let receiptSignal: AbortSignal | null | undefined;
  const request = vi.fn((_url: RequestInfo | URL, options?: RequestInit) => {
    receiptSignal = options?.signal;
    expect(new Headers(options?.headers).get("X-Tempo-Work-Class")).toBe(lane === "background" ? "background" : null);
    return new Promise<Response>((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(options.signal?.reason), { once: true });
    });
  });
  vi.stubGlobal("fetch", lane === "custom" ? vi.fn(() => { throw new Error("Custom fetch was ignored"); }) : request);
  const response = confirmOriginalOperationResponse(Response.json({ operation_id: "abort-operation", state: "queued" }, { status: 202 }), "abort-operation", {
    signal: controller.signal, background: lane === "background", ...(lane === "custom" ? { fetch: request } : {}),
  });
  const rejected = expect(response).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => expect(request).toHaveBeenCalledOnce());
  expect(receiptSignal).toBe(controller.signal);
  controller.abort();
  await rejected;
});

it("classified operation failure preserves structured detail message and status notification", async () => {
  const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  try {
    const request = vi.fn(async () => Response.json({ state: "failed", error: {
      message: "409: saved result changed", detail: { code: "card_revision_changed", message: "Saved result changed" },
      status_code: 409, code: "card_revision_changed", retryable: false,
    } }));
    const response = confirmOriginalOperationResponse(Response.json({ operation_id: "classified-operation", state: "queued" }, { status: 202 }), "classified-operation", { fetch: request });
    await expect(response).rejects.toBeInstanceOf(FailedOperationError);
    await expect(response).rejects.toMatchObject({ message: "409: saved result changed", status: 409,
      code: "card_revision_changed", retryable: false, operationId: "classified-operation" });
    expect(notice).toHaveBeenCalledWith("classified-operation");
  } finally { unsubscribe(); }
});

it.each([
  ["missing completion payload", { state: "complete" }],
  ["invalid failed detail", { state: "failed", error: "invalid" }],
  ["wrong identity", { operation_id: "other", state: "unknown" }],
])("%s remains unconfirmed even when missing receipts may be replayed", async (_name, wire) => {
  const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  try {
    await expect(readOperationResponse("original", { fetch: async () => Response.json(wire), allowMissing: true }))
      .rejects.toMatchObject({ name: "PendingOperationError", operationId: "original" });
    expect(notice).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

it.each(["other", null, 42])("unrelated receipt identity %j preserves original operation without notification or replay", async operation_id => {
  const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  const request = vi.fn(async () => Response.json({ operation_id, state: "complete", response: { saved: true } }));
  try {
    await expect(readOperationResponse("original", { fetch: request, allowMissing: true })).rejects.toMatchObject({
      name: "PendingOperationError", operationId: "original", message: "Learning did not confirm the original operation identity.",
    });
    expect(request).toHaveBeenCalledOnce();
    expect(notice).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

it("missing required receipt identity remains pending without notification or replay", async () => {
  const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  const request = vi.fn(async () => Response.json({ state: "complete", response: { saved: true } }));
  const client = { independent: true, origin: "https://learning.example", generation: 0, learningFetch: request } as unknown as import("../../app/lib/study-connection").StudyClient;
  try {
    await expect(readOperationResponse("original", { client, allowMissing: true })).rejects.toMatchObject({
      name: "PendingOperationError", operationId: "original", message: "Learning did not confirm the original operation identity.",
    });
    expect(request).toHaveBeenCalledOnce();
    expect(notice).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

it("only a validated unknown or HTTP 404 is returned as missing", async () => {
  for (const response of [Response.json({ state: "unknown" }), new Response(null, { status: 404 })]) {
    expect((await readOperationResponse("original", { fetch: async () => response, allowMissing: true })).status).toBe(404);
  }
  await expect(readOperationResponse("original", { fetch: async () => new Response("{", { status: 200 }), allowMissing: true }))
    .rejects.toBeInstanceOf(PendingOperationError);
});

it.each(["queued", "executing", "retrying", "pending"])("%s emits the existing status notification", async state => {
  const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  try {
    await expect(readOperationResponse("original", { fetch: async () => Response.json({ state }) })).rejects.toBeInstanceOf(PendingOperationError);
    expect(notice).toHaveBeenCalledWith("original");
  } finally { unsubscribe(); }
});

it("preserves null completion responses", async () => {
  const completed = await readOperationResponse("original", { fetch: async () => Response.json({ state: "complete", response: null }) });
  expect(await completed.json()).toBeNull();

});

it("propagates an ambiguous receipt transport failure unchanged", async () => {
  const failure = new TypeError("connection lost");
  await expect(readOperationResponse("original", { fetch: async () => { throw failure; } })).rejects.toBe(failure);
});
