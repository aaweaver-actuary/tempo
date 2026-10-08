import { afterEach, expect, it, vi } from "vitest";
import { deleteRepertoireCommand } from "../../app/lib/repertoire-delete-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("a pending repertoire deletion cannot remove another repertoire and reuses its operation ID", async () => {
  const commandKeys: string[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) {
      receiptReads += 1;
      return Response.json(receiptReads < 2 ? { state: "pending" } : {
        state: "complete", response: { deleted: true, id: "white" },
      });
    }
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    commandKeys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  }));
  await expect(deleteRepertoireCommand("white")).rejects.toBeInstanceOf(PendingOperationError);
  await expect(deleteRepertoireCommand("black")).rejects.toBeInstanceOf(PendingOperationError);
  expect(commandKeys).toHaveLength(1);
  await deleteRepertoireCommand("white");
  expect(commandKeys).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
});

it("a pending repertoire deletion cannot switch its learned-card policy", async () => {
  const submissions: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return Response.json({ state: "pending" });
    submissions.push(init!);
    return Response.json({ operation_id: "pending-delete", state: "pending" }, { status: 202 });
  }));
  await expect(deleteRepertoireCommand("black", "keep")).rejects.toBeInstanceOf(PendingOperationError);
  await expect(deleteRepertoireCommand("black", "delete")).rejects.toBeInstanceOf(PendingOperationError);
  expect(submissions).toHaveLength(1);
});

it("repertoire deletion sends the explicitly selected learned-card policy", async () => {
  const submissions: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    submissions.push(String(input));
    return Response.json({ deleted: true, id: "black" });
  }));
  await deleteRepertoireCommand("black", "keep");
  expect(new URL(submissions[0]).searchParams.get("learned_cards")).toBe("keep");
});

it("repertoire deletion preserves an unconfirmed receipt and recovers a missing receipt with the same policy", async () => {
  const pending = { operationId: "original-delete", repertoireId: "black", learnedCards: "keep" };
  localStorage.setItem("tempo-pending-repertoire-delete-v1", JSON.stringify(pending));
  const fetchMock = vi.fn<(input?: unknown, init?: RequestInit) => Promise<Response>>(async () => Response.json({ state: "complete", response: { deleted: true, id: "wrong" } }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(deleteRepertoireCommand("black", "keep")).rejects.toThrow("different repertoire");
  expect(JSON.parse(localStorage.getItem("tempo-pending-repertoire-delete-v1")!)).toEqual(pending);
  fetchMock.mockImplementation(async (_input?: unknown, init?: RequestInit) => {
    if (!init) return Response.json({ detail: "Unknown operation" }, { status: 404 });
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("original-delete");
    return Response.json({ deleted: true, id: "black" });
  });
  await deleteRepertoireCommand("black", "keep");
  expect(localStorage.getItem("tempo-pending-repertoire-delete-v1")).toBeNull();
});
