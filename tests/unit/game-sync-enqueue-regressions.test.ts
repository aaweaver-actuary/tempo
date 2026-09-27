import { act, render, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { syncResultSchema } from "../../app/domain/schemas";
import { useGameSync } from "../../app/hooks/use-game-sync";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearDebugErrors();
});

const queuedSync = {
  imported: 0,
  job_id: "65b588f2-cba2-4678-bc8c-b7a10b5227f1",
  status: "queued" as const,
};

const lichessResult = {
  provider: "lichess" as const,
  username: "player",
  status: "idle" as const,
  fetched: 1,
  inserted: 1,
  updated: 0,
  duplicates: 0,
  filtered: 0,
  rejected: 0,
  failed: 0,
  error: null,
  retry_after: null,
};

describe("game sync enqueue provider contracts", () => {
  it("queued game sync accepts an empty provider map without a diagnostic", () => {
    expect(syncResultSchema.parse({ ...queuedSync, providers: {} })).toEqual({
      ...queuedSync,
      providers: {},
    });
  });

  it("single provider sync result does not require the other provider", () => {
    expect(
      syncResultSchema.parse({
        ...queuedSync,
        status: "complete",
        providers: { lichess: lichessResult },
      }).providers,
    ).toEqual({ lichess: lichessResult });
  });

  it("malformed queued provider data still fails strict validation", () => {
    expect(
      syncResultSchema.safeParse({
        ...queuedSync,
        providers: { lichess: { ...lichessResult, unexpected: true } },
      }).success,
    ).toBe(false);
    expect(
      syncResultSchema.safeParse({
        ...queuedSync,
        providers: { unknown: lichessResult },
      }).success,
    ).toBe(false);
  });

  it("game-sync settings failures identify the settings endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) =>
        String(input).endsWith("/api/games/sync/status")
          ? Response.json({ providers: [] })
          : Response.json(
              { detail: "The local database is busy with background work." },
              { status: 503 },
            ),
      ),
    );
    function Probe() {
      useGameSync();
      return null;
    }

    render(createElement(Probe));
    await waitFor(() => expect(debugErrors().length).toBeGreaterThan(0));
    expect(debugErrors().at(-1)?.context.endpointPath).toBe("/api/settings");
  });

  it("automatic game sync backs off after service failure and resumes after recovery", async () => {
    vi.useFakeTimers();
    let statusAttempts = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/games/sync/status")) {
        statusAttempts += 1;
        if (statusAttempts <= 2) {
          return Promise.resolve(
            Response.json({ detail: "Service unavailable" }, { status: 503 }),
          );
        }
        return Promise.resolve(Response.json({ providers: [] }));
      }
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal("fetch", fetchMock);
    function Probe() {
      useGameSync();
      return null;
    }

    const rendered = render(createElement(Probe));
    await act(async () => undefined);
    expect(statusAttempts).toBe(1);

    await act(async () => vi.advanceTimersByTimeAsync(4_999));
    expect(statusAttempts).toBe(1);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(statusAttempts).toBe(2);
    expect(debugErrors().filter((record) => record.context.source === "game-sync-status")).toHaveLength(1);

    await act(async () => vi.advanceTimersByTimeAsync(9_999));
    expect(statusAttempts).toBe(2);
    await act(async () => window.dispatchEvent(new Event("online")));
    expect(statusAttempts).toBe(3);

    await act(async () => vi.advanceTimersByTimeAsync(14_999));
    expect(statusAttempts).toBe(3);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(statusAttempts).toBe(4);
    expect(debugErrors().filter((record) => record.context.source === "game-sync-status")).toHaveLength(1);
    rendered.unmount();
  });

  it("game sync status reports a new failure after recovery", async () => {
    vi.useFakeTimers();
    let statusAttempts = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/games/sync/status")) {
        statusAttempts += 1;
        return Promise.resolve(statusAttempts === 3
          ? Response.json({ providers: [] })
          : Response.json({ detail: "Service unavailable" }, { status: 503 }));
      }
      return new Promise<Response>(() => undefined);
    }));
    function Probe() { useGameSync(); return null; }
    const rendered = render(createElement(Probe));
    await act(async () => undefined);
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(debugErrors().filter((record) => record.context.source === "game-sync-status")).toHaveLength(1);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(debugErrors().filter((record) => record.context.source === "game-sync-status")).toHaveLength(2);
    rendered.unmount();
  });

  it("game sync status failure does not block a successful live training queue", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/games/sync/status"))
        return Promise.resolve(Response.json({ detail: "Service unavailable" }, { status: 503 }));
      if (String(input).includes("/api/queue/window")) return Promise.resolve(Response.json({
        count: 1, cards: [{ id: "training-still-live", queue_entry_id: 907,
          start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
          moves: ["e2e4"], content_type: "opening", repertoire_name: "Live training",
          repertoire_source: "PGN" }],
      }));
      return new Promise<Response>(() => undefined);
    }));
    function Probe() { useGameSync(); return null; }
    const rendered = render(createElement(Probe));
    await waitFor(() => expect(debugErrors().some((record) => record.context.source === "game-sync-status")).toBe(true));
    await fetchAndInitializeQueue();
    expect(useTrainingStore.getState().getCard().title).toBe("Live training");
    expect(useTrainingStore.getState().serviceError).toBe("");
    rendered.unmount();
  });
});
