import { afterEach, expect, it, vi } from "vitest";
import { confirmOperationResponse, FailedOperationError, readOperationResponse } from "../../app/lib/operation-status";
import { subscribeOperationStatusChange } from "../../app/lib/operation-status-events";

vi.hoisted(() => vi.resetModules());
vi.mock("../../app/lib/study-connection", async importOriginal => {
  const actual = await importOriginal<typeof import("../../app/lib/study-connection")>();
  return { ...actual, defaultStudyClient: { ...actual.defaultStudyClient, origin: "https://learning.example" } };
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

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
