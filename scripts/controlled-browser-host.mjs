import { createServer } from "node:http";
import { readFile, realpath, stat } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";

export function controlledEnvironment(source) {
  const allowed = /^(PATH|HOME|TMPDIR|TMP|TEMP|SYSTEMROOT|USERPROFILE|LANG|LC_.+|CI|PLAYWRIGHT_BROWSERS_PATH|PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD|NODE_EXTRA_CA_CERTS)$/;
  return { ...Object.fromEntries(Object.entries(source).filter(([name]) => allowed.test(name))), TZ: "America/New_York" };
}
const contentTypes = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".wasm": "application/wasm", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json", ".woff2": "font/woff2", ".mp3": "audio/mpeg", ".wav": "audio/wav" };
export async function startControlledHost(buildDirectory, runId) {
  const rootDirectory = await realpath(buildDirectory);
  const apiEscapes = [];
  const server = createServer(async (request, response) => {
    response.setHeader("Cross-Origin-Opener-Policy", "same-origin");
    response.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
    response.setHeader("Cache-Control", "no-cache");
    try {
      const pathname = decodeURIComponent(new URL(request.url, "http://controlled.invalid").pathname);
      if (pathname === "/api" || pathname.startsWith("/api/")) {
        apiEscapes.push({ method: request.method, path: request.url });
        response.writeHead(501, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "Unintercepted API request on frontend-only controlled host" }));
        return;
      }
      if (pathname === "/__controlled_host/ready") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ host: "controlled-frontend-only", run_id: runId, backend_services: [] }));
        return;
      }
      if (!["GET", "HEAD"].includes(request.method)) { response.writeHead(405); response.end(); return; }
      let filePath = resolve(rootDirectory, `.${pathname === "/" ? "/index.html" : pathname}`);
      if (!filePath.startsWith(`${rootDirectory}${sep}`)) { response.writeHead(403); response.end(); return; }
      try { if (!(await stat(filePath)).isFile()) throw new Error("Not a file"); }
      catch {
        if (request.headers.accept?.includes("text/html") && !extname(pathname)) filePath = resolve(rootDirectory, "index.html");
        else { response.writeHead(404); response.end(); return; }
      }
      const actualPath = await realpath(filePath);
      if (!actualPath.startsWith(`${rootDirectory}${sep}`)) { response.writeHead(403); response.end(); return; }
      const bytes = await readFile(actualPath);
      response.writeHead(200, { "Content-Type": contentTypes[extname(actualPath)] ?? "application/octet-stream", "Content-Length": bytes.length });
      response.end(request.method === "HEAD" ? undefined : bytes);
    } catch { response.writeHead(400); response.end(); }
  });
  await new Promise((resolveListening, rejectListening) => {
    server.once("error", rejectListening); server.listen(0, "127.0.0.1", resolveListening);
  });
  return { origin: `http://127.0.0.1:${server.address().port}`, apiEscapes,
    async close() { server.closeAllConnections(); await new Promise((resolveClosed, rejectClosed) => server.close(error => error ? rejectClosed(error) : resolveClosed())); } };
}

// Only declared shell assets and the genuine prebuilt chess core enter this build.
export async function prepareControlledAssets(root, directory) {
  const { createHash } = await import("node:crypto");
  const { mkdir, copyFile } = await import("node:fs/promises");
  const { dirname, join } = await import("node:path");
  const inputs = JSON.parse(await readFile(join(root, "tests/fixtures/controlled-browser/inputs.json"), "utf8"));
  const hashes = {};
  for (const name of [...inputs.publicFiles, ...Object.keys(inputs.wasm).map(name => `tempo-core/${name}`)]) {
    const source = join(root, "public", name);
    let bytes;
    try { bytes = await readFile(source); }
    catch { throw new Error(`Missing real controlled build input: ${name}; provision the documented prebuilt artifact`); }
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (name.startsWith("tempo-core/") && digest !== inputs.wasm[name.slice("tempo-core/".length)])
      throw new Error(`Controlled chess artifact digest mismatch: ${name}`);
    const target = join(directory, name);
    await mkdir(dirname(target), { recursive: true });
    await copyFile(source, target);
    hashes[name] = digest;
  }
  return hashes;
}
