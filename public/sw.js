const CACHE = "tempo-static-v6";
const MAX_CACHE_ENTRIES = 120;
const STATIC_DESTINATIONS = new Set(["script", "style", "image", "font", "worker", "manifest"]);
const FETCHED_STATIC_DIRECTORIES = ["assets/", "data/", "ort/", "tempo-core/", "engines/"];
const PIECE_ASSETS = ["w", "b"].flatMap((color) =>
  ["P", "N", "B", "R", "Q", "K"].map((piece) => `pieces/merida/${color}${piece}.svg`));
const SHELL_ASSETS = ["", "index.html", "favicon.svg", "tempo-icon.png", "manifest.webmanifest", ...PIECE_ASSETS];

async function requiredShellUrls(cache) {
  const scope = new URL(self.registration.scope);
  const documentResponse = await cache.match(new URL("index.html", scope).href);
  if (!documentResponse) return [];
  const html = await documentResponse.text();
  const referencedAssets = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
    .map((match) => new URL(match[1], scope))
    .filter((url) => url.origin === scope.origin && url.pathname.startsWith(`${scope.pathname}assets/`));
  return [...new Set([
    ...SHELL_ASSETS.map((path) => new URL(path, scope).href),
    ...referencedAssets.map((url) => url.href),
  ])];
}

function cacheableRequest(request) {
  if (request.method !== "GET") return false;
  const scope = new URL(self.registration.scope);
  const requested = new URL(request.url);
  if (requested.origin !== scope.origin || !requested.pathname.startsWith(scope.pathname)) return false;
  const relativePath = requested.pathname.slice(scope.pathname.length);
  if (relativePath.startsWith("api/")) return false;
  return request.mode === "navigate" || STATIC_DESTINATIONS.has(request.destination)
    || FETCHED_STATIC_DIRECTORIES.some((directory) => relativePath.startsWith(directory));
}

async function precacheShell() {
  const cache = await caches.open(CACHE);
  await cache.addAll(SHELL_ASSETS.map((path) => new URL(path, self.registration.scope).href));
  const shellUrls = await requiredShellUrls(cache);
  await cache.addAll(shellUrls.filter((url) => !SHELL_ASSETS.some((path) =>
    url === new URL(path, self.registration.scope).href)));
}
self.addEventListener("install", (event) => {
  event.waitUntil(precacheShell().then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("tempo-static-") && key !== CACHE).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener("message", (event) => {
  if (event.data?.type !== "tempo:offline-shell-status" || !event.ports?.[0]) return;
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    const shellUrls = await requiredShellUrls(cache);
    const ready = shellUrls.length > SHELL_ASSETS.length &&
      (await Promise.all(shellUrls.map((url) => cache.match(url, { ignoreVary: true })))).every(Boolean);
    event.ports[0].postMessage({ version: CACHE, ready });
  })());
});
async function storeStaticResponse(request, response) {
  try {
    const cache = await caches.open(CACHE);
    await cache.put(request, response);
    const keys = await cache.keys();
    const shellUrls = new Set(await requiredShellUrls(cache));
    const removable = keys.filter((key) => !shellUrls.has(key.url));
    await Promise.all(removable.slice(0, Math.max(0, keys.length - MAX_CACHE_ENTRIES)).map((key) => cache.delete(key)));
  } catch {
    // A full browser cache must not fail an otherwise successful request.
  }
}
async function offlineResponse(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request, { ignoreVary: true });
  if (cached) return cached;
  if (request.mode === "navigate") {
    const shell = await cache.match(new URL("index.html", self.registration.scope).href);
    if (shell) return shell;
  }
  return Response.error();
}
async function isolated(response) {
  if (!response || response.type === "opaque" || response.type === "error") return response;
  const headers = new Headers(response.headers);
  headers.delete("Content-Encoding");
  headers.delete("Content-Length");
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  headers.set("Cross-Origin-Embedder-Policy", "credentialless");
  headers.set("Cross-Origin-Resource-Policy", "cross-origin");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}
self.addEventListener("fetch", (event) => {
  if (!cacheableRequest(event.request)) return;
  event.respondWith(fetch(event.request).then((response) => {
    if (response.ok && response.type !== "opaque")
      event.waitUntil(storeStaticResponse(event.request, response.clone()));
    return response;
  }).catch(() => offlineResponse(event.request)).then(isolated));
});
