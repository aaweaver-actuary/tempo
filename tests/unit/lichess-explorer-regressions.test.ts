import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadExplorer, readCachedExplorer } from "../../app/lib/lichess-explorer";
import { readLichessSessionToken } from "../../app/lib/lichess-session";

const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const payload = {
  white: 10,
  draws: 5,
  black: 5,
  moves: [{ uci: "e2e4", san: "e4", white: 6, draws: 2, black: 2 }],
};

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

afterEach(() => vi.restoreAllMocks());

it("authenticated Explorer requests send the bearer token to both sources and cache their independent results", async () => {
  const fetcher = vi.fn(async () => Response.json(payload));
  vi.stubGlobal("fetch", fetcher);

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(fetcher).toHaveBeenCalledTimes(2);
  for (const [, options] of fetcher.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit]>)
    expect(options.headers).toEqual({ Authorization: "Bearer secret-token" });
  expect(result.lichess.state).toBe("ready");
  expect(result.masters.state).toBe("ready");
  expect(result.lichess.moves).toEqual(payload.moves);
  expect(readCachedExplorer(fen, "rapid", "1600")).toMatchObject({ human: payload, masters: payload });
});

it("missing Explorer authentication reports sign-in required without making anonymous requests", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);

  const result = await loadExplorer(fen, "rapid", "1600", "");

  expect(fetcher).not.toHaveBeenCalled();
  expect(result.lichess.state).toBe("authentication-required");
  expect(result.masters.state).toBe("authentication-required");
});

it("an authentication rejection is distinct and never exposes the bearer token", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("secret-token", { status: 401 })));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.category).toBe("authentication-failed");
  expect(JSON.stringify(result)).not.toContain("secret-token");
});

it("rate limits are retryable and preserve cached source data as stale", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(payload)));
  await loadExplorer(fen, "rapid", "1600", "secret-token");
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 429 })));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.state).toBe("stale");
  expect(result.lichess.category).toBe("rate-limited");
  expect(result.lichess.moves).toEqual(payload.moves);
  expect(result.lichess.retryable).toBe(true);
});

it("Masters outage does not discard ready Lichess results", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) =>
    String(input).includes("/masters")
      ? new Response("", { status: 503 })
      : Response.json(payload),
  ));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.state).toBe("ready");
  expect(result.lichess.moves).toEqual(payload.moves);
  expect(result.masters.state).toBe("upstream-unavailable");
  expect(result.masters.status).toBe(503);
});

it("Lichess outage does not discard ready Masters results", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) =>
    String(input).includes("/lichess")
      ? new Response("", { status: 504 })
      : Response.json(payload),
  ));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.state).toBe("upstream-unavailable");
  expect(result.masters.state).toBe("ready");
  expect(result.masters.moves).toEqual(payload.moves);
});

it("network failures preserve cached moves and report offline state", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(payload)));
  await loadExplorer(fen, "rapid", "1600", "secret-token");
  vi.stubGlobal("navigator", { onLine: false });
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.state).toBe("stale");
  expect(result.lichess.category).toBe("offline");
  expect(result.lichess.moves).toEqual(payload.moves);
});

it("HTTP 200 with an incompatible payload is invalid and cannot poison the cache", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) =>
    String(input).includes("/masters")
      ? Response.json({ moves: [{ uci: "invalid" }] })
      : Response.json(payload),
  ));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.state).toBe("ready");
  expect(result.masters.state).toBe("invalid-response");
  expect(readCachedExplorer(fen, "rapid", "1600")?.masters).toBeUndefined();
});

it("legacy paired cache migrates without adding authentication to its key", async () => {
  localStorage.setItem("tempo-explorer-cache-v1:" + `${fen}|rapid|1600`, JSON.stringify({ human: payload, masters: payload, fetchedAt: Date.now() }));
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));

  const result = await loadExplorer(fen, "rapid", "1600", "secret-token");

  expect(result.lichess.state).toBe("stale");
  expect(readCachedExplorer(fen, "rapid", "1600")?.human).toEqual(payload);
  expect(localStorage.getItem("tempo-explorer-cache-v2:" + `${fen}|rapid|1600:lichess`)).toContain('"fetchedAt"');
});

it("legacy persistent Lichess token migrates into the session and is removed from local storage", () => {
  localStorage.setItem("tempo-lichess-token", "legacy-session-token");

  expect(readLichessSessionToken()).toBe("legacy-session-token");
  expect(sessionStorage.getItem("tempo-lichess-token")).toBe("legacy-session-token");
  expect(localStorage.getItem("tempo-lichess-token")).toBeNull();
});
