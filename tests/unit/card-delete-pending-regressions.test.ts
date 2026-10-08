import { afterEach, expect, it, vi } from "vitest";
import { deleteCardCommand } from "../../app/lib/card-delete-command";
import { PendingOperationError } from "../../app/lib/operation-status";
import { reviseCardCommand } from "../../app/lib/card-revision-command";
import { acceptPrefixSplitCommand } from "../../app/lib/prefix-split-command";

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("pending deletion prevents conflicting card edits and pending edits prevent deletion", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify({ operationId: "delete", cardId: "shared", expectedRevision: 1 }));
  await expect(reviseCardCommand({ cardId: "shared", startingFen: "fen", moves: ["e2e4"], historyMode: "preserve", expectedRevision: 1 })).rejects.toThrow("pending card deletion");
  await expect(acceptPrefixSplitCommand("shared", 1)).rejects.toThrow("pending card deletion");
  localStorage.removeItem("tempo-pending-card-delete-v1");
  localStorage.setItem("tempo-pending-card-revision-v1", "pending-edit");
  await expect(deleteCardCommand("shared", 1)).rejects.toThrow("pending card edit");
  expect(fetcher).not.toHaveBeenCalled();
});

it("a pending permanent deletion survives reload and cannot delete a different card or revision", async () => {
  const pending = { operationId: "original-delete", cardId: "shared", expectedRevision: 2 };
  localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify(pending));
  let complete = false;
  const fetcher = vi.fn(async () => Response.json(complete ? { state: "complete", response: { deleted: true, card_id: "shared" } } : { state: "pending" }));
  vi.stubGlobal("fetch", fetcher);
  await expect(deleteCardCommand("other", 2)).rejects.toBeInstanceOf(PendingOperationError);
  await expect(deleteCardCommand("shared", 3)).rejects.toBeInstanceOf(PendingOperationError);
  expect(fetcher).not.toHaveBeenCalled();
  await expect(deleteCardCommand("shared", 2)).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-card-delete-v1")).toBe(JSON.stringify(pending));
  complete = true;
  await deleteCardCommand("shared", 2);
  expect(localStorage.getItem("tempo-pending-card-delete-v1")).toBeNull();
  expect(fetcher.mock.calls).toHaveLength(2);
});

it("a deletion saved before request delivery resubmits its exact original operation", async () => {
  localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify({ operationId: "original-delete", cardId: "shared", expectedRevision: 2 }));
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/api/operations/")) return new Response("", { status: 404 });
    expect(String(input)).toContain("permanent=true&expected_revision=2");
    expect(init?.headers).toEqual({ "Idempotency-Key": "original-delete" });
    return Response.json({ deleted: true, card_id: "shared" });
  });
  vi.stubGlobal("fetch", fetcher);
  await deleteCardCommand("shared", 2);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("permanent deletion reports a failed durable receipt and preserves an invalid receipt for diagnosis", async () => {
  const pending = { operationId: "delete", cardId: "shared", expectedRevision: 2 };
  localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify(pending));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "failed", error: { message: "The card changed" } })));
  await expect(deleteCardCommand("shared", 2)).rejects.toThrow("The card changed");
  expect(localStorage.getItem("tempo-pending-card-delete-v1")).toBeNull();
  localStorage.setItem("tempo-pending-card-delete-v1", JSON.stringify(pending));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "complete", response: { deleted: true, card_id: "another" } })));
  await expect(deleteCardCommand("shared", 2)).rejects.toThrow("different card");
  expect(localStorage.getItem("tempo-pending-card-delete-v1")).not.toBeNull();
});
