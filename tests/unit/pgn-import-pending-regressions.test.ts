import { afterEach, expect, it, vi } from "vitest";
import { savePgnImportCommand } from "../../app/lib/pgn-import-command";
import { PendingOperationError } from "../../app/lib/operation-status";

const pendingKey = "tempo-pending-pgn-import-v1";
const importResult = {
  repertoire_id: "rep", source_name: "opening.pgn", games_found: 1,
  unique_lines: 1, cards_created: 1, duplicates_merged: 0,
  cards_admitted_today: 0, integrity: { status: "unchecked", issue_count: 0, first_issue_id: null },
  decision_cards_created: 1, shared_decisions_reused: 0,
  prefix_cards_created: 0, shared_prefixes_reused: 0,
  descendant_decision_cards_created: 1, graph_state: "refreshing",
};
const openingFile = () => new File(['[Event "Test"]\n\n1. e4 e5 *'], "opening.pgn");

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

// Receipt contract: complete/failed clear identity; blocked/active/ambiguous
// retain it. Only an explicitly unknown, fingerprint-matched receipt replays.
it("unknown PGN receipt replays the exact file and settings with its original operation ID", async () => {
  const file = openingFile();
  const createId = vi.spyOn(crypto, "randomUUID");
  const submissions: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/"))
      return Response.json({ state: "unknown" });
    submissions.push(init!);
    if (submissions.length === 1) {
      const operationId = (init!.headers as Record<string, string>)["Idempotency-Key"];
      return Response.json({ operation_id: operationId, state: "unknown" }, { status: 202 });
    }
    return Response.json(importResult);
  }));
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toBeInstanceOf(PendingOperationError);
  const pending = JSON.parse(localStorage.getItem(pendingKey)!);
  await expect(savePgnImportCommand(file, "white", 4)).resolves.toEqual(importResult);
  expect(createId).toHaveBeenCalledTimes(1);
  expect(submissions).toHaveLength(2);
  for (const request of submissions) {
    expect(request.headers).toEqual({ "Idempotency-Key": pending.operationId });
    const form = request.body as FormData;
    expect(form.get("file")).toBe(file);
    expect(form.get("trained_color")).toBe("white");
    expect(form.get("initial_depth")).toBe("4");
  }
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

async function rememberImport(file = openingFile(), trainedColor = "white", initialDepth = 4) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const fingerprint = [file.name, trainedColor, initialDepth,
    Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")].join(":");
  localStorage.setItem(pendingKey, JSON.stringify({ operationId: "original-import", fingerprint }));
  return file;
}
function receiptFetcher(receipt: unknown) {
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

const legacyPendingDiagnostic = "Legacy receipt has no saved payload. Recover only from matching journal or outbox evidence; automatic replay is unavailable.";

it("legacy pending PGN receipt returns its diagnostic without polling or replay", async () => {
  const file = await rememberImport();
  const storedIdentity = localStorage.getItem(pendingKey);
  vi.useFakeTimers();
  const createId = vi.spyOn(crypto, "randomUUID");
  const fetcher = receiptFetcher({ operation_id: "original-import", state: "pending", message: legacyPendingDiagnostic });
  const controller = new AbortController();
  let rejection: unknown;
  const saving = savePgnImportCommand(file, "white", 4, { signal: controller.signal })
    .catch((error: unknown) => { rejection = error; });
  try {
    await vi.waitFor(() => expect(rejection).toMatchObject({
      name: "PendingOperationError", operationId: "original-import", blocked: false,
      message: `Import confirmation is unavailable. ${legacyPendingDiagnostic}`,
    }), { timeout: 250, interval: 10 });
    await saving;
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect([url.endsWith("/api/operations/original-import"), options.method]).toEqual([true, undefined]);
    expect(createId).not.toHaveBeenCalled();
    expect(localStorage.getItem(pendingKey)).toBe(storedIdentity);
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    controller.abort();
    await saving;
  }
});

it("rechecking legacy pending PGN import only reads its original operation", async () => {
  const file = await rememberImport();
  const storedIdentity = localStorage.getItem(pendingKey);
  const createId = vi.spyOn(crypto, "randomUUID");
  const fetcher = receiptFetcher({ operation_id: "original-import", state: "pending", message: legacyPendingDiagnostic });
  for (let action = 0; action < 2; action += 1)
    await expect(savePgnImportCommand(file, "white", 4)).rejects.toMatchObject({
      operationId: "original-import", blocked: false,
      message: `Import confirmation is unavailable. ${legacyPendingDiagnostic}`,
    });
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [url, options] of fetcher.mock.calls as unknown as [string, RequestInit][])
    expect([url.endsWith("/api/operations/original-import"), options.method]).toEqual([true, undefined]);
  expect(createId).not.toHaveBeenCalled();
  expect(localStorage.getItem(pendingKey)).toBe(storedIdentity);
});

it("PGN polling stops when a legacy no-payload pending receipt appears", async () => {
  const file = await rememberImport();
  const storedIdentity = localStorage.getItem(pendingKey);
  vi.useFakeTimers();
  const createId = vi.spyOn(crypto, "randomUUID");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ state: "queued" }))
    .mockResolvedValueOnce(Response.json({ operation_id: "original-import", state: "pending", message: legacyPendingDiagnostic }));
  vi.stubGlobal("fetch", fetcher);
  const saving = savePgnImportCommand(file, "white", 4);
  const rejected = expect(saving).rejects.toMatchObject({
    operationId: "original-import", blocked: false,
    message: `Import confirmation is unavailable. ${legacyPendingDiagnostic}`,
  });
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(1000);
  await rejected;
  await vi.advanceTimersByTimeAsync(30_000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [url, options] of fetcher.mock.calls as [string, RequestInit][])
    expect([url.endsWith("/api/operations/original-import"), options.method]).toEqual([true, undefined]);
  expect(createId).not.toHaveBeenCalled();
  expect(localStorage.getItem(pendingKey)).toBe(storedIdentity);
  expect(vi.getTimerCount()).toBe(0);
});

it.each(["pending", "queued", "executing", "retrying"])("durably %s PGN import polls the same operation without a POST or new UUID", async (state) => {
  const file = await rememberImport();
  vi.useFakeTimers();
  const createId = vi.spyOn(crypto, "randomUUID");
  let reads = 0;
  const fetcher = vi.fn(async () => Response.json(++reads === 1 ? { state } : { state: "complete", response: importResult }));
  vi.stubGlobal("fetch", fetcher);
  const saving = savePgnImportCommand(file, "white", 4);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  await vi.advanceTimersByTimeAsync(1000);
  expect(await saving).toEqual(importResult);
  expect(createId).not.toHaveBeenCalled();
  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [url, options] of fetcher.mock.calls as unknown as [string, RequestInit][])
    expect([url.endsWith("/api/operations/original-import"), options.method]).toEqual([true, undefined]);
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("completed stored PGN import returns its validated receipt without POST and clears identity", async () => {
  const file = await rememberImport();
  const createId = vi.spyOn(crypto, "randomUUID");
  const fetcher = receiptFetcher({ state: "complete", response: importResult });
  expect(await savePgnImportCommand(file, "white", 4)).toEqual(importResult);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(createId).not.toHaveBeenCalled();
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("failed stored PGN import clears identity and surfaces the backend failure without resending", async () => {
  const file = await rememberImport();
  const fetcher = receiptFetcher({ state: "failed", error: { message: "Source admission rejected" } });
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toThrow("Source admission rejected");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("blocked PGN import preserves identity and surfaces the actual diagnostic without automatic retry", async () => {
  const file = await rememberImport();
  const createId = vi.spyOn(crypto, "randomUUID");
  const fetcher = receiptFetcher({ state: "blocked", last_error: { message: "Write connection unavailable" } });
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toMatchObject({ blocked: true, message: expect.stringContaining("Write connection unavailable") });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(createId).not.toHaveBeenCalled();
  expect(localStorage.getItem(pendingKey)).not.toBeNull();
});

it.each(["unknown", "queued", "executing", "retrying", "pending", "blocked"])("different PGN fingerprint cannot replace an unresolved %s import", async (state) => {
  await rememberImport();
  const stored = localStorage.getItem(pendingKey);
  const createId = vi.spyOn(crypto, "randomUUID");
  const fetcher = receiptFetcher({ state });
  await expect(savePgnImportCommand(new File(["1. d4 *"], "other.pgn"), "black", 6)).rejects.toThrow("original file and settings");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(createId).not.toHaveBeenCalled();
  expect(localStorage.getItem(pendingKey)).toBe(stored);
});

it("terminal previous PGN import permits a different file after resolving its receipt", async () => {
  await rememberImport();
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ state: "complete", response: importResult }))
    .mockResolvedValueOnce(Response.json({ ...importResult, source_name: "other.pgn" }));
  vi.stubGlobal("fetch", fetcher);
  expect((await savePgnImportCommand(new File(["1. d4 *"], "other.pgn"), "black", 6)).source_name).toBe("other.pgn");
  expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).not.toBe("original-import");
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("failed previous PGN import reports its failure before a different deliberate import can start", async () => {
  await rememberImport();
  const otherFile = new File(["1. d4 *"], "other.pgn");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "Previous source rejected" } }))
    .mockResolvedValueOnce(Response.json({ ...importResult, source_name: "other.pgn" }));
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(otherFile, "black", 6)).rejects.toThrow("Previous source rejected");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBeNull();
  expect((await savePgnImportCommand(otherFile, "black", 6)).source_name).toBe("other.pgn");
  expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).not.toBe("original-import");
});

it("lost PGN POST and unavailable status preserve identity for later same-ID replay", async () => {
  const file = openingFile();
  const fetcher = vi.fn().mockRejectedValueOnce(new Error("Lost delivery response"))
    .mockRejectedValueOnce(new Error("Status offline"))
    .mockResolvedValueOnce(Response.json({ state: "unknown" }))
    .mockResolvedValueOnce(Response.json(importResult));
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toThrow("confirmation is unavailable");
  const original = JSON.parse(localStorage.getItem(pendingKey)!);
  expect(await savePgnImportCommand(file, "white", 4)).toEqual(importResult);
  expect(fetcher.mock.calls[3][1].headers["Idempotency-Key"]).toBe(original.operationId);
});

it.each(["transport", "http", "malformed", "unsupported"])("%s status failure preserves PGN identity without guessing unknown or resending", async (failure) => {
  const file = await rememberImport();
  const stored = localStorage.getItem(pendingKey);
  const fetcher = vi.fn(async () => {
    if (failure === "transport") throw new Error("Offline");
    if (failure === "http") return Response.json({ detail: "Offline" }, { status: 503 });
    if (failure === "malformed") return new Response("{");
    return Response.json({ state: "new-unrecognised-state" });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toBeInstanceOf(PendingOperationError);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBe(stored);
});

it("invalid complete PGN result retains recovery identity instead of claiming success", async () => {
  const file = await rememberImport();
  receiptFetcher({ state: "complete", response: {} });
  await expect(savePgnImportCommand(file, "white", 4)).rejects.toThrow();
  expect(localStorage.getItem(pendingKey)).not.toBeNull();
});

it("active PGN polling expires after thirty seconds and a later check uses the same operation", async () => {
  const file = await rememberImport();
  vi.useFakeTimers();
  let complete = false;
  const fetcher = vi.fn(async () => Response.json(complete ? { state: "complete", response: importResult } : { state: "executing" }));
  vi.stubGlobal("fetch", fetcher);
  const result = expect(savePgnImportCommand(file, "white", 4)).rejects.toThrow("still processing");
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(30_000);
  await result;
  expect(localStorage.getItem(pendingKey)).not.toBeNull();
  complete = true;
  expect(await savePgnImportCommand(file, "white", 4)).toEqual(importResult);
  expect((fetcher.mock.calls as unknown as [string][]).every(([url]) => url.endsWith("/api/operations/original-import"))).toBe(true);
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
});

it("hung PGN status transport respects the budget and late completion cannot erase identity", async () => {
  const file = await rememberImport();
  vi.useFakeTimers();
  let release!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { release = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  const result = expect(savePgnImportCommand(file, "white", 4)).rejects.toThrow("confirmation is unavailable");
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(30_000);
  await result;
  release(Response.json({ state: "complete", response: importResult }));
  await Promise.resolve(); await Promise.resolve();
  expect(localStorage.getItem(pendingKey)).not.toBeNull();
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
});

it("cancelling PGN confirmation stops polling and preserves its pending identity", async () => {
  const file = await rememberImport();
  vi.useFakeTimers();
  const fetcher = receiptFetcher({ state: "queued" });
  const controller = new AbortController();
  const result = expect(savePgnImportCommand(file, "white", 4, { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  controller.abort();
  await result;
  await vi.advanceTimersByTimeAsync(30_000);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).not.toBeNull();
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
});

it.each([false, true])("explicit blocked PGN retry keeps identity and waits beyond its acknowledgement (lost response: %s)", async (lostResponse) => {
  const file = await rememberImport();
  vi.useFakeTimers();
  let reads = 0;
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/retry")) {
      expect(options?.method).toBe("POST");
      if (lostResponse) throw new Error("Lost retry acknowledgement");
      return Response.json({ state: "blocked", operation_id: "original-import" }, { status: 202 });
    }
    return Response.json(++reads <= 2 ? { state: "blocked", last_error: { message: "Connection lost" } }
      : reads === 3 ? { state: "executing" } : { state: "complete", response: importResult });
  });
  vi.stubGlobal("fetch", fetcher);
  const saving = savePgnImportCommand(file, "white", 4, { retryBlocked: true });
  await vi.waitFor(() => expect(reads).toBe(2));
  await vi.advanceTimersByTimeAsync(2000);
  expect(await saving).toEqual(importResult);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/retry"))).toHaveLength(1);
  expect(fetcher.mock.calls.every(([url]) => url.includes("/api/operations/original-import"))).toBe(true);
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it.each([400, 422])("pre-admission HTTP %s validation rejection clears PGN identity and surfaces backend detail", async (status) => {
  const fetcher = vi.fn(async () => Response.json({ detail: "No playable lines were found" }, { status }));
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(openingFile(), "white", 4)).rejects.toThrow("No playable lines were found");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("HTTP 500 PGN admission resolves its durable failed receipt before clearing identity", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Save failed" }, { status: 500 }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "Invalid source" } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(openingFile(), "white", 4)).rejects.toThrow("Invalid source");
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it.each(["invalid JSON", "missing identity", "invalid identity"])("unreadable PGN admission acknowledgement (%s) resolves the original receipt without another POST", async (failure) => {
  let originalOperationId = "";
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      originalOperationId = (options.headers as Record<string, string>)["Idempotency-Key"];
      expect(JSON.parse(localStorage.getItem(pendingKey)!).operationId).toBe(originalOperationId);
      if (failure === "invalid JSON") return new Response("{", { status: 202 });
      return Response.json(failure === "missing identity" ? { state: "queued" } : { operation_id: 123 }, { status: 202 });
    }
    expect(url.endsWith(`/api/operations/${originalOperationId}`)).toBe(true);
    return Response.json({ state: "complete", response: importResult });
  });
  vi.stubGlobal("fetch", fetcher);
  expect(await savePgnImportCommand(openingFile(), "white", 4)).toEqual(importResult);
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("normal immediate PGN success retains existing import result behavior", async () => {
  const fetcher = receiptFetcher(importResult);
  expect(await savePgnImportCommand(openingFile(), "white", 4)).toEqual(importResult);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("discarded PGN confirmation permits a different file with a fresh operation identity", async () => {
  await rememberImport();
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ outcome: "discarded" }))
    .mockResolvedValueOnce(Response.json({ operation_id: "original-import", state: "failed", error: { code: "import_discarded" } }))
    .mockResolvedValueOnce(Response.json({ ...importResult, source_name: "different.pgn" }));
  vi.stubGlobal("fetch", fetcher);
  expect(await discardPendingPgnImport("original-import")).toBeNull();
  expect(localStorage.getItem(pendingKey)).toBeNull();
  await savePgnImportCommand(new File(["1. d4 d5 *"], "different.pgn"), "black", 6);
  expect(fetcher.mock.calls[2][1].headers["Idempotency-Key"]).not.toBe("original-import");
});

it.each(["transport", "malformed", "http"])("unconfirmed %s PGN discard retains identity", async failure => {
  await rememberImport();
  const stored = localStorage.getItem(pendingKey);
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({}, { status: 202 })).mockImplementation(async () => {
    if (failure === "transport") throw new Error("Offline");
    if (failure === "malformed") return new Response("{");
    return Response.json({}, { status: 503 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(discardPendingPgnImport("original-import")).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem(pendingKey)).toBe(stored);
});

it("lost PGN discard response resolves the original terminal receipt without another POST", async () => {
  await rememberImport();
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  const fetcher = vi.fn().mockRejectedValueOnce(new Error("Lost response"))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { code: "import_discarded" } }));
  vi.stubGlobal("fetch", fetcher);
  expect(await discardPendingPgnImport("original-import")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[0][1].method).toBe("POST");
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("stale PGN discard completion cannot erase a newer pending import", async () => {
  await rememberImport();
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  const replacement = JSON.stringify({ operationId: "new-import", fingerprint: "different" });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/api/operations/")) {
      localStorage.setItem(pendingKey, replacement);
      return Response.json({ state: "failed", error: { code: "import_discarded" } });
    }
    return Response.json({});
  }));
  await discardPendingPgnImport("original-import");
  expect(localStorage.getItem(pendingKey)).toBe(replacement);
});

it("PGN discard preserves an already completed repertoire and validates its result", async () => {
  await rememberImport();
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({}))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: importResult })));
  expect(await discardPendingPgnImport("original-import")).toEqual(importResult);
  expect(localStorage.getItem(pendingKey)).toBeNull();
});

it("timed out PGN discard retains identity and ignores a late terminal response", async () => {
  await rememberImport();
  const stored = localStorage.getItem(pendingKey);
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  vi.useFakeTimers();
  let release!: (response: Response) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({}, { status: 202 }))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  const discard = expect(discardPendingPgnImport("original-import")).rejects.toBeInstanceOf(PendingOperationError);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  await vi.advanceTimersByTimeAsync(30_000);
  await discard;
  release(Response.json({ state: "failed", error: { code: "import_discarded" } }));
  await Promise.resolve(); await Promise.resolve();
  expect(localStorage.getItem(pendingKey)).toBe(stored);
  expect(vi.getTimerCount()).toBe(0);
});

it.each([400, 422, 500])("confirmed HTTP %s PGN failure permits a subsequent different file with a fresh identity", async status => {
  const operationIds: string[] = [];
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method !== "POST") return Response.json({ state: "failed", error: { message: "Admission failed" } });
    operationIds.push((options.headers as Record<string, string>)["Idempotency-Key"]);
    return operationIds.length === 1 ? Response.json({ detail: "Admission failed" }, { status })
      : Response.json({ ...importResult, source_name: "different.pgn" });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(openingFile(), "white", 4)).rejects.toThrow("Admission failed");
  await expect(savePgnImportCommand(new File(["1. d4 d5 *"], "different.pgn"), "black", 6)).resolves.toMatchObject({ source_name: "different.pgn" });
  expect(operationIds).toHaveLength(2);
  expect(operationIds[1]).not.toBe(operationIds[0]);
});

it("stale PGN completion cannot replace a newer pending import when a different file is selected", async () => {
  await rememberImport();
  const replacement = JSON.stringify({ operationId: "new-import", fingerprint: "different" });
  const fetcher = vi.fn(async () => {
    localStorage.setItem(pendingKey, replacement);
    return Response.json({ state: "complete", response: importResult });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(savePgnImportCommand(new File(["1. d4 d5 *"], "different.pgn"), "black", 6)).rejects.toBeInstanceOf(PendingOperationError);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBe(replacement);
});

it("rejected PGN discard reports the service error and retains recovery identity", async () => {
  await rememberImport();
  const stored = localStorage.getItem(pendingKey);
  const { discardPendingPgnImport } = await import("../../app/lib/pgn-import-command");
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Only PGN imports can be discarded" }, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(discardPendingPgnImport("original-import")).rejects.toThrow("Only PGN imports can be discarded");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem(pendingKey)).toBe(stored);
});
