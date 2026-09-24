const CACHE = "tempo-static-v3";
const MAX_CACHE_ENTRIES = 120;
const STATIC_DESTINATIONS = new Set(["script", "style", "image", "font"]);
const FETCHED_STATIC_DIRECTORIES = ["data/", "ort/", "tempo-core/", "engines/"];

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

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(["./", "./index.html", "./favicon.svg"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("tempo-static-") && key !== CACHE).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});
async function storeStaticResponse(request, response) {
  try {
    const cache = await caches.open(CACHE);
    await cache.put(request, response);
    const keys = await cache.keys();
    const scope = self.registration.scope;
    const shellUrls = new Set([
      new URL("./", scope).href,
      new URL("index.html", scope).href,
      new URL("favicon.svg", scope).href,
    ]);
    const removable = keys.filter((key) => !shellUrls.has(key.url));
    await Promise.all(removable.slice(0, Math.max(0, keys.length - MAX_CACHE_ENTRIES)).map((key) => cache.delete(key)));
  } catch {
    // A full browser cache must not fail an otherwise successful request.
  }
}
async function offlineResponse(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
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
