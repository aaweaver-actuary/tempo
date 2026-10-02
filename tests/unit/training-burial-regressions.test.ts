import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { rememberBrowserTrainingBurial, restoreBrowserTrainingBurials } from "../../app/lib/browser-training-burials";
import { buryTrainingEntry, finishTrainingBurial } from "../../app/lib/training-bury-command";
import { asCardId, asFenString, asRepertoireId, type PracticeCard } from "../../app/types";

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
  expect(restoreBrowserTrainingBurials([1, 2], cards, "2026-10-02")).toEqual([1, 2]);
  expect(JSON.parse(localStorage.getItem("tempo-daily-queue")!)).toEqual([1, 2]);
  expect(restoreBrowserTrainingBurials([1, 2, 0], cards, "2026-10-02")).toEqual([1, 2, 0]);
});

it("browser burial can empty the queue and clears deleted identities tomorrow", () => {
  rememberBrowserTrainingBurial("buried", "2026-10-01");
  rememberBrowserTrainingBurial("deleted", "2026-10-01");
  expect(restoreBrowserTrainingBurials([0], cards, "2026-10-01")).toEqual([]);
  expect(restoreBrowserTrainingBurials([], cards, "2026-10-02")).toEqual([]);
});

it("bury transport retries retain the same identity until refresh completion", async () => {
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
    .mockResolvedValueOnce(Response.json({ state: "unknown" }))
    .mockResolvedValueOnce(Response.json({ operation_id: "bury-pending" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: { buried: true, queue_entry_id: 42 } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("still pending");
  await expect(buryTrainingEntry(42)).rejects.toThrow("still pending");
  await expect(buryTrainingEntry(42)).resolves.toBeUndefined();
});

it("bury rejects an unconfirmed success body or a different card receipt", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({}))
    .mockResolvedValueOnce(Response.json({ buried: true, queue_entry_id: 99 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("did not confirm");
  const operationId = localStorage.getItem("tempo-bury-operation-42");
  expect(operationId).toBeTruthy();
  await expect(buryTrainingEntry(42)).rejects.toThrow("did not confirm");
  expect(localStorage.getItem("tempo-bury-operation-42")).toBe(operationId);
});


it("stale imported-card burial never appends a card to the independently generated next-day queue", () => {
  const importedCard = { ...cards[0], id: asCardId("personal-imported-card"), repertoireId: asRepertoireId("personal-repertoire") };
  const availableCards = [cards[1], importedCard, cards[2]];
  rememberBrowserTrainingBurial(importedCard.id, "2026-10-01");
  expect(restoreBrowserTrainingBurials([0, 2], availableCards, "2026-10-02")).toEqual([0, 2]);
  expect(localStorage.getItem("tempo-training-burials")).toBeNull();
  rememberBrowserTrainingBurial(importedCard.id, "2026-10-01");
  expect(restoreBrowserTrainingBurials([1, 0], availableCards, "2026-10-02")).toEqual([1, 0]);
  expect(localStorage.getItem("tempo-training-burials")).toBeNull();
});

it("terminal 409 burial clears its identity so a later legitimate head attempt gets a new command", async () => {
  let rejectedOperationId: string | undefined;
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    const operationId = (options.headers as Record<string, string>)["Idempotency-Key"];
    if (!rejectedOperationId) {
      rejectedOperationId = operationId;
      return Response.json({ detail: "This queue entry is no longer active" }, { status: 409 });
    }
    expect(operationId).not.toBe(rejectedOperationId);
    return Response.json({ buried: true, queue_entry_id: 42 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("no longer active");
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
  await expect(buryTrainingEntry(42)).resolves.toBeUndefined();
});

it("durable failed burial receipt clears its identity for a fresh independent attempt", async () => {
  const fetcher = vi.fn().mockImplementationOnce(async (_url: string, options: RequestInit) =>
    Response.json({ operation_id: (options.headers as Record<string, string>)["Idempotency-Key"] }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "Stale burial rejected" } }))
    .mockResolvedValueOnce(Response.json({ buried: true, queue_entry_id: 42 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("Stale burial rejected");
  const rejectedOperationId = fetcher.mock.calls[0][1].headers["Idempotency-Key"];
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
  await buryTrainingEntry(42);
  expect(fetcher.mock.calls[2][1].headers["Idempotency-Key"]).not.toBe(rejectedOperationId);
});

it("pending and blocked burial receipts retain their identity through retries", async () => {
  let operationId = "";
  let receiptState = "pending";
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes("/operations/")) return Response.json({ state: receiptState });
    const submittedId = (options!.headers as Record<string, string>)["Idempotency-Key"];
    if (!operationId) operationId = submittedId;
    expect(submittedId).toBe(operationId);
    return Response.json({ operation_id: operationId }, { status: 202 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("still pending");
  expect(localStorage.getItem("tempo-bury-operation-42")).toBe(operationId);
  receiptState = "blocked";
  await expect(buryTrainingEntry(42)).rejects.toThrow("is blocked");
  expect(localStorage.getItem("tempo-bury-operation-42")).toBe(operationId);
});


it.each([400, 401, 403, 404, 422])("definitive HTTP %s burial rejection clears its identity", async (status) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Burial rejected" }, { status })));
  await expect(buryTrainingEntry(42)).rejects.toThrow("Burial rejected");
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
});

it.each([408, 429, 503])("retriable HTTP %s burial failure preserves its identity", async (status) => {
  const fetcher = vi.fn(async () => Response.json({ detail: "Retry burial" }, { status }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow(status === 503 ? "still pending" : "Retry burial");
  const operationId = localStorage.getItem("tempo-bury-operation-42");
  expect(operationId).toBeTruthy();
  await expect(buryTrainingEntry(42)).rejects.toThrow(status === 503 ? "still pending" : "Retry burial");
  expect((fetcher.mock.calls[status === 503 ? 2 : 1] as unknown as [string, RequestInit])[1].headers).toEqual({ "Idempotency-Key": operationId });
});


it("direct 500 durable failed burial clears identity for a fresh command", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({}, { status: 500 }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "Synthetic terminal failure" } }))
    .mockResolvedValueOnce(Response.json({ buried: true, queue_entry_id: 42 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(buryTrainingEntry(42)).rejects.toThrow("Synthetic terminal failure");
  const rejectedId = fetcher.mock.calls[0][1].headers["Idempotency-Key"];
  expect(fetcher.mock.calls[1][0]).toContain(`/operations/${rejectedId}`);
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeNull();
  await buryTrainingEntry(42);
  expect(fetcher.mock.calls[2][1].headers["Idempotency-Key"]).not.toBe(rejectedId);
});

it("direct 500 completed receipt confirms original burial until queue refresh", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({}, { status: 500 }))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: { buried: true, queue_entry_id: 42 } })));
  await expect(buryTrainingEntry(42)).resolves.toBeUndefined();
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeTruthy();
});

it.each(["unknown", "queued", "pending", "executing", "retrying", "blocked", "unavailable"])(
  "direct 500 with %s receipt retains burial identity", async (state) => {
    const fetcher = vi.fn(async (url: string) => url.includes("/operations/")
      ? Response.json({ state }, { status: state === "unavailable" ? 503 : 200 })
      : Response.json({}, { status: 500 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(buryTrainingEntry(42)).rejects.toThrow();
    const operationId = localStorage.getItem("tempo-bury-operation-42");
    await expect(buryTrainingEntry(42)).rejects.toThrow();
    expect(localStorage.getItem("tempo-bury-operation-42")).toBe(operationId);
    expect(operationId).toBeTruthy();
  });

it("direct 500 mismatched completed receipt rejects and retains identity", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({}, { status: 500 }))
    .mockResolvedValueOnce(Response.json({ state: "complete", response: { buried: true, queue_entry_id: 99 } })));
  await expect(buryTrainingEntry(42)).rejects.toThrow("did not confirm");
  expect(localStorage.getItem("tempo-bury-operation-42")).toBeTruthy();
});


it.each(["complete", "failed", "queued", "pending", "executing", "retrying", "blocked", "conflict", "mismatched", "unavailable"])(
  "blocked burial retry uses durable endpoint and preserves identity through %s", async (outcome) => {
    let operationId = "";
    let retried = false;
    const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/bury")) {
        expect(operationId).toBe("");
        operationId = (options!.headers as Record<string, string>)["Idempotency-Key"];
        return Response.json({ operation_id: operationId }, { status: 202 });
      }
      expect(url).toContain(`/operations/${operationId}`);
      if (url.endsWith("/retry")) {
        expect(options?.method).toBe("POST");
        retried = true;
        return Response.json({}, { status: outcome === "conflict" ? 409 : 202 });
      }
      return Response.json(!retried ? { state: "blocked" } : {
        state: outcome === "conflict" || outcome === "mismatched" ? "complete" : outcome,
        response: { buried: true, queue_entry_id: outcome === "mismatched" ? 99 : 42 }, error: { message: "Terminal retry failure" },
      });
    });
    vi.stubGlobal("fetch", fetcher);
    await expect(buryTrainingEntry(42)).rejects.toThrow("blocked");
    if (outcome === "complete" || outcome === "conflict") await buryTrainingEntry(42, true);
    else await expect(buryTrainingEntry(42, true)).rejects.toThrow();
    expect(retried).toBe(true);
    expect(localStorage.getItem("tempo-bury-operation-42")).toBe(outcome === "failed" ? null : operationId);
  });
