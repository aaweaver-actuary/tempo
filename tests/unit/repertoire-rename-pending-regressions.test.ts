import { afterEach, expect, it, vi } from "vitest";
import { renameRepertoireCommand } from "../../app/lib/repertoire-rename-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending repertoire rename reuses its operation ID and waits for a receipt", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 3 ? { state: "pending" } : {
        state: "complete", response: { id: "rep", name: "New name" },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(renameRepertoireCommand("rep", "New name")).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).not.toBeNull();
  await renameRepertoireCommand("rep", "New name");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBeNull();
});

it.each([
  ["malformed", { state: "complete" }],
  ["foreign", { state: "complete", operation_id: "other", response: { id: "rep", name: "old" } }],
])("%s receipt preserves the saved operation", async (_label, receipt) => {
  const original = JSON.stringify({ operationId: "original", repertoireId: "rep", name: "old" });
  localStorage.setItem("tempo-pending-repertoire-rename-v1", original);
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  await expect(renameRepertoireCommand("rep", "new")).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([
  ["missing name", { id: "rep" }],
  ["wrong repertoire", { id: "other", name: "Original" }],
  ["wrong name", { id: "rep", name: "Different" }],
])("%s completed rename retains the saved identity and name", async (_label, result) => {
  const original = JSON.stringify({ operationId: "original", repertoireId: "rep", name: "Original" });
  localStorage.setItem("tempo-pending-repertoire-rename-v1", original);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ operation_id: "original", state: "complete", response: result })));
  await expect(renameRepertoireCommand("rep", "Later edit")).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBe(original);
});

it.each(["direct", "receipt"])("%s rename validates the server-normalized original before clearing", async mode => {
  const submittedName = "\u001c  Original  \u0085";
  const original = JSON.stringify({ operationId: "original", repertoireId: "rep", name: submittedName });
  if (mode === "receipt") localStorage.setItem("tempo-pending-repertoire-rename-v1", original);
  const fetcher = vi.fn(async () => Response.json(mode === "receipt"
    ? { operation_id: "original", state: "complete", response: { id: "rep", name: "Original" } }
    : { id: "rep", name: "Original" }));
  vi.stubGlobal("fetch", fetcher);
  await renameRepertoireCommand("rep", submittedName);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBeNull();
});

it("direct rename with a mismatched result retains its exact submitted name", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ id: "rep", name: "Wrong" })));
  await expect(renameRepertoireCommand("rep", "Original")).rejects.toThrow();
  expect(JSON.parse(localStorage.getItem("tempo-pending-repertoire-rename-v1")!)).toMatchObject({ repertoireId: "rep", name: "Original" });
});

it("an older confirmed rename cannot settle a later edited name", async () => {
  localStorage.setItem("tempo-pending-repertoire-rename-v1", JSON.stringify({ operationId: "old", repertoireId: "rep", name: "Old" }));
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json({ operation_id: "old", state: "complete", response: { id: "rep", name: "Old" } });
    expect(init?.body).toBe(JSON.stringify({ name: "New" }));
    expect(new Headers(init?.headers).get("Idempotency-Key")).not.toBe("old");
    return Response.json({ id: "rep", name: "New" });
  });
  vi.stubGlobal("fetch", fetcher);
  await renameRepertoireCommand("rep", "New");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(localStorage.getItem("tempo-pending-repertoire-rename-v1")).toBeNull();
});


it.each(["missing", "foreign"])("%s initial acknowledgment retains the exact original operation through retry", async kind => {
  const storageKey = "tempo-pending-repertoire-rename-v1";
  const writes: { url: string; method: string | undefined; key: string | null; body: BodyInit | null | undefined }[] = [];
  const urls: string[] = [];
  let operationId = "";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); urls.push(url);
    if (urls.length > 1) expect(localStorage.getItem(storageKey)).toBe(saved);
    if (url.includes("/api/operations/")) {
      expect(decodeURIComponent(url)).toContain(`/api/operations/${operationId}`);
      if (writes.length < 2) return Response.json({ state: "pending" });
      return Response.json({ operation_id: operationId, state: "complete", response: { id: "rep", name: "New name" } });
    }
    const key = new Headers(init?.headers).get("Idempotency-Key");
    writes.push({ url, method: init?.method, key, body: init?.body });
    if (writes.length === 1) {
      operationId = key!;
      return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
    }
    return Response.json({ operation_id: key, state: "queued" }, { status: 202 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(renameRepertoireCommand("rep", "New name")).rejects.toBeInstanceOf(PendingOperationError);
  const saved = localStorage.getItem(storageKey);
  expect(saved).not.toBeNull();
  expect(JSON.parse(saved!).operationId).toBe(operationId);
  expect(urls).toHaveLength(1);
  await renameRepertoireCommand("rep", "New name");
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
  expect(urls.some(url => url.includes("foreign"))).toBe(false);
  expect(localStorage.getItem(storageKey)).toBeNull();
});
