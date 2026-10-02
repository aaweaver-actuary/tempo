import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { rememberBrowserTrainingBurial, restoreBrowserTrainingBurials } from "../../app/lib/browser-training-burials";
import { buryTrainingEntry, finishTrainingBurial } from "../../app/lib/training-bury-command";
import { asCardId, asFenString, type PracticeCard } from "../../app/types";

const cards: PracticeCard[] = ["buried", "next", "last"].map((id) => ({
  id: asCardId(id), kind: "opening", title: id, subtitle: "", moves: [],
  startingFen: asFenString("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"), userMoveTarget: 0,
}));
beforeEach(() => localStorage.clear());
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("browser burial survives reload and removes repeated card identities until the next day", () => {
  rememberBrowserTrainingBurial("buried", "2026-10-01");
  expect(restoreBrowserTrainingBurials([0, 1, 0, 2], cards, "2026-10-01")).toEqual([1, 2]);
  // The same card identity can have a different index after deleting a repertoire.
  expect(restoreBrowserTrainingBurials([0, 1], [cards[1], cards[0]], "2026-10-01")).toEqual([0]);
  localStorage.setItem("tempo-daily-queue", "[1,2]");
  expect(restoreBrowserTrainingBurials([1, 2], cards, "2026-10-02")).toEqual([1, 2, 0]);
  expect(JSON.parse(localStorage.getItem("tempo-daily-queue")!)).toEqual([1, 2, 0]);
  expect(restoreBrowserTrainingBurials([1, 2, 0], cards, "2026-10-02")).toEqual([1, 2, 0]);
});

it("browser burial can empty the queue and restores only cards still present tomorrow", () => {
  rememberBrowserTrainingBurial("buried", "2026-10-01");
  rememberBrowserTrainingBurial("deleted", "2026-10-01");
  expect(restoreBrowserTrainingBurials([0], cards, "2026-10-01")).toEqual([]);
  expect(restoreBrowserTrainingBurials([], cards, "2026-10-02")).toEqual([0]);
});

it("bury transport and failed queue-refresh retries reuse the confirmed operation identity", async () => {
  const fetcher = vi.fn()
    .mockRejectedValueOnce(new Error("Lost response"))
    .mockImplementation(async () => Response.json({ buried: true, queue_entry_id: 42 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("Lost response");
  const operationId = localStorage.getItem("tempo-bury-operation-42");
  await buryTrainingEntry(42);
  expect(localStorage.getItem("tempo-bury-operation-42")).toBe(operationId);
  await buryTrainingEntry(42);
  for (const [, options] of fetcher.mock.calls)
    expect(options.headers["Idempotency-Key"]).toBe(operationId);
  finishTrainingBurial(42);
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
});

it("pending or failed burial cannot report success and completed receipts confirm the selected entry", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ operation_id: "bury-pending" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "pending" }))
    .mockResolvedValueOnce(Response.json({ detail: "Queue unavailable" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ operation_id: "bury-pending" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: { buried: true, queue_entry_id: 42 } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("still pending");
  await expect(buryTrainingEntry(42)).rejects.toThrow("Queue unavailable");
  await expect(buryTrainingEntry(42)).resolves.toBeUndefined();
});

it("bury rejects an unconfirmed success body or a different card receipt", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({}))
    .mockResolvedValueOnce(Response.json({ buried: true, queue_entry_id: 99 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("did not confirm");
  await expect(buryTrainingEntry(42)).rejects.toThrow("did not confirm");
});
