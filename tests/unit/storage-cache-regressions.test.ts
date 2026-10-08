import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";
import { readWorkspaceData } from "../../app/lib/workspace-data";

it("workspace persistence excludes live API responses and bounds static data", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ payload: "x".repeat(150_000) })));
  await readWorkspaceData("/api/storage-large");
  expect(localStorage.getItem("tempo-workspace-cache-v3:/api/storage-large")).toBeNull();

  vi.mocked(fetch).mockImplementation(async () => Response.json({ payload: "x".repeat(100_000) }));
  for (let index = 0; index < 15; index++)
    await readWorkspaceData(`/data/storage-${index}.json`);
  const cacheKeys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
    .filter((key): key is string => Boolean(key?.startsWith("tempo-workspace-cache-v3:")));
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
    match: vi.fn(async (request: { url: string } | string) => cached.get(typeof request === "string" ? request : request.url)?.clone()),
    keys: vi.fn(async () => Array.from(cached.keys(), (url) => ({ url }))),
    delete: vi.fn(async (request: { url: string }) => cached.delete(request.url)),
  };
  const fetchMock = vi.fn(async () => new Response("static asset"));
  const serviceWorkerSource = readFileSync("public/sw.js", "utf8").replace("const BUILD_SHELL_ASSETS = [];",
    'const BUILD_SHELL_ASSETS = ["assets/app.js", "assets/study.worker.js", "assets/shared.js"];');
  runInNewContext(serviceWorkerSource, {
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
      match: vi.fn(async (request: { url: string } | string) => cached.get(typeof request === "string" ? request : request.url)?.clone()),
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

  fetchMock.mockRejectedValueOnce(new Error("offline"));
  const uncachedAsset = dispatch("https://tempo.test/tempo/assets/missing.js", "script");
  const missingResponse = await uncachedAsset.respondWith.mock.calls[0][0];
  expect(missingResponse.type).toBe("error");

  cached.set("https://tempo.test/tempo/index.html", new Response('<script src="/tempo/assets/app.js"></script>'));
  cached.set("https://tempo.test/tempo/assets/app.js", new Response("app"));
  cached.set("https://tempo.test/tempo/", new Response("shell"));
  for (let index = 0; index < 125; index++) {
    const nextAsset = dispatch(`https://tempo.test/tempo/assets/${index}.js`, "script");
    await nextAsset.respondWith.mock.calls[0][0];
    await Promise.all(nextAsset.waitUntil.mock.calls.map(([promise]) => promise));
  }
  expect(cached.size).toBeLessThanOrEqual(120);
  expect(cached.has("https://tempo.test/tempo/index.html")).toBe(true);
  expect(cached.has("https://tempo.test/tempo/assets/app.js")).toBe(true);
  expect(cached.has("https://tempo.test/tempo/")).toBe(true);

  const onMessage = listeners.get("message");
  expect(onMessage).toBeDefined();
  const postMessage = vi.fn();
  const waitUntil = vi.fn();
  onMessage!({ data: { type: "tempo:offline-shell-status" }, ports: [{ postMessage }], waitUntil });
  await waitUntil.mock.calls[0][0];
  expect(postMessage).toHaveBeenCalledWith({ version: "tempo-static-v7", ready: false });

  for (const path of ["favicon.svg", "tempo-icon.png", "manifest.webmanifest",
    ...["w", "b"].flatMap((color) => ["P", "N", "B", "R", "Q", "K"]
      .map((piece) => `pieces/merida/${color}${piece}.svg`))])
    cached.set(`https://tempo.test/tempo/${path}`, new Response(path));
  onMessage!({ data: { type: "tempo:offline-shell-status" }, ports: [{ postMessage }], waitUntil });
  await waitUntil.mock.calls[1][0];
  expect(postMessage).toHaveBeenLastCalledWith({ version: "tempo-static-v7", ready: false });
  cached.set("https://tempo.test/tempo/assets/study.worker.js", new Response("worker"));
  cached.set("https://tempo.test/tempo/assets/shared.js", new Response("shared dependency"));
  onMessage!({ data: { type: "tempo:offline-shell-status" }, ports: [{ postMessage }], waitUntil });
  await waitUntil.mock.calls[2][0];
  expect(postMessage).toHaveBeenLastCalledWith({ version: "tempo-static-v7", ready: true });

  for (let index = 125; index < 250; index++) {
    const asset = dispatch(`https://tempo.test/tempo/assets/${index}.js`, "script");
    await asset.respondWith.mock.calls[0][0];
    await Promise.all(asset.waitUntil.mock.calls.map(([promise]) => promise));
  }
  expect(cached.has("https://tempo.test/tempo/assets/study.worker.js")).toBe(true);
  expect(cached.has("https://tempo.test/tempo/assets/shared.js")).toBe(true);
});
