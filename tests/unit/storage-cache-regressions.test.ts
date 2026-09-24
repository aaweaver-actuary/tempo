import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";
import { readWorkspaceData } from "../../app/lib/workspace-data";

it("workspace persistence rejects oversized API responses and bounds total bytes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ payload: "x".repeat(150_000) })));
  await readWorkspaceData("/api/storage-large");
  expect(localStorage.getItem("tempo-workspace-cache-v2:/api/storage-large")).toBeNull();

  vi.mocked(fetch).mockImplementation(async () => Response.json({ payload: "x".repeat(100_000) }));
  for (let index = 0; index < 15; index++)
    await readWorkspaceData(`/api/storage-${index}`);
  const cacheKeys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
    .filter((key): key is string => Boolean(key?.startsWith("tempo-workspace-cache-v2:")));
  const estimatedBytes = cacheKeys.reduce(
    (total, key) => total + 2 * (key.length + (localStorage.getItem(key)?.length ?? 0)),
    0,
  );
  expect(estimatedBytes).toBeLessThanOrEqual(2 * 1024 * 1024);
  expect(cacheKeys.length).toBeLessThan(15);
});

it("Pages service worker caches scoped static assets and bypasses API and external GETs", async () => {
  const listeners = new Map<string, (event: unknown) => void>();
  const cached = new Map<string, Response>();
  const cache = {
    addAll: vi.fn(async () => undefined),
    put: vi.fn(async (request: { url: string }, response: Response) => {
      cached.set(request.url, response);
    }),
    match: vi.fn(async (request: { url: string }) => cached.get(request.url)),
    keys: vi.fn(async () => Array.from(cached.keys(), (url) => ({ url }))),
    delete: vi.fn(async (request: { url: string }) => cached.delete(request.url)),
  };
  const fetchMock = vi.fn(async () => new Response("static asset"));
  runInNewContext(readFileSync("public/sw.js", "utf8"), {
    self: {
      registration: { scope: "https://tempo.test/tempo/" },
      addEventListener: (name: string, listener: (event: unknown) => void) => listeners.set(name, listener),
      skipWaiting: vi.fn(),
      clients: { claim: vi.fn() },
    },
    caches: {
      open: vi.fn(async () => cache),
      keys: vi.fn(async () => []),
      delete: vi.fn(async () => true),
      match: vi.fn(async (request: { url: string }) => cached.get(request.url)),
    },
    fetch: fetchMock,
    URL,
    Response,
    Headers,
  });
  const onFetch = listeners.get("fetch");
  expect(onFetch).toBeDefined();

  function dispatch(url: string, destination: string) {
    const respondWith = vi.fn();
    const waitUntil = vi.fn();
    onFetch!({ request: { url, method: "GET", mode: "same-origin", destination }, respondWith, waitUntil });
    return { respondWith, waitUntil };
  }

  expect(dispatch("https://tempo.test/tempo/api/games", "").respondWith).not.toHaveBeenCalled();
  expect(dispatch("https://lichess.org/api/explorer", "").respondWith).not.toHaveBeenCalled();
  const asset = dispatch("https://tempo.test/tempo/assets/app.js", "script");
  expect(asset.respondWith).toHaveBeenCalledOnce();
  await asset.respondWith.mock.calls[0][0];
  await Promise.all(asset.waitUntil.mock.calls.map(([promise]) => promise));
  expect(cache.put).toHaveBeenCalledOnce();

  fetchMock.mockRejectedValueOnce(new Error("offline"));
  const offlineAsset = dispatch("https://tempo.test/tempo/assets/app.js", "script");
  await expect(offlineAsset.respondWith.mock.calls[0][0]).resolves.toBeInstanceOf(Response);
});
