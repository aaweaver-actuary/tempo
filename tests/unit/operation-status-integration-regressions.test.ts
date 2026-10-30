import { afterEach, expect, it, vi } from "vitest";
import { confirmOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "../../app/lib/operation-status";
import { subscribeOperationStatusChange } from "../../app/lib/operation-status-events";
import { createGameReviewApi } from "../../app/features/game-review/api";

vi.hoisted(() => vi.resetModules());
vi.mock("../../app/lib/study-connection", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/study-connection")>(), LEARNING_URL: "https://learning.example",
}));

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each([
  ["https://games.example/api/product/game-review/sync", "complete"],
  ["https://games.example/api/product/game-review/sync", "pending"],
  ["/api/product/game-review/sync", "complete"],
] as const)("Games receipts use the request origin and original identity for %s with %s state", async (endpoint, state) => {
  const operationId = "original-games-operation";
  const origin = new URL(endpoint, window.location.href).origin;
  const result = { provider: "lichess", status: "pending", operation_id: operationId, error: null };
  const request = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    if (options?.method === "POST") {
      expect(JSON.parse(String(options.body)).operation_id).toBe(operationId);
      return Response.json({ operation_id: operationId }, { status: 202 });
    }
    expect(String(input)).toBe(`${origin}/api/operations/${operationId}`);
    return Response.json({ operation_id: operationId, state, response: result });
  });
  const client = createGameReviewApi({ fetch: request, endpoints: {
    recentGames: endpoint, gameDetail: () => endpoint, sync: endpoint, retryFeedback: endpoint,
  } });
  const pending = client.sync({ provider: "lichess", operation_id: operationId });
  if (state === "pending") await expect(pending).rejects.toMatchObject({ name: "PendingOperationError", operationId });
  else await expect(pending).resolves.toEqual(result);
  expect(request).toHaveBeenCalledTimes(2);
  expect(request.mock.calls.some(([input]) => String(input).startsWith("https://learning.example"))).toBe(false);
});

it("relative Games receipts have a deterministic origin outside the browser", async () => {
  vi.stubGlobal("window", undefined);
  const operationId = "server-relative-operation";
  const request = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    if (options?.method === "POST") return Response.json({ operation_id: operationId }, { status: 202 });
    expect(String(input)).toBe(`http://tempo.local/api/operations/${operationId}`);
    return Response.json({ operation_id: operationId, state: "pending" });
  });
  const endpoint = "/api/product/game-review/sync";
  const client = createGameReviewApi({ fetch: request, endpoints: {
    recentGames: endpoint, gameDetail: () => endpoint, sync: endpoint, retryFeedback: endpoint,
  } });
  await expect(client.sync({ provider: "lichess", operation_id: operationId })).rejects.toBeInstanceOf(PendingOperationError);
  expect(request).toHaveBeenCalledTimes(2);
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
  const response = confirmOperationResponse(Response.json({ operation_id: "abort-operation" }, { status: 202 }), {
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
    const response = confirmOperationResponse(Response.json({ operation_id: "classified-operation" }, { status: 202 }), { fetch: request });
    await expect(response).rejects.toBeInstanceOf(FailedOperationError);
    await expect(response).rejects.toMatchObject({ message: "409: saved result changed", status: 409,
      code: "card_revision_changed", retryable: false, operationId: "classified-operation" });
    expect(notice).toHaveBeenCalledWith("classified-operation");
  } finally { unsubscribe(); }
});
