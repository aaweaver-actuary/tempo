import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { pathToFileURL } from "node:url";

if (process.env.BOARD_UI_BROWSER_GRANT !== "1")
  throw new Error("Browser execution deferred. Obtain the parent resource grant, then set BOARD_UI_BROWSER_GRANT=1.");
const directory = resolve(process.argv[2] ?? "");
const output = resolve(process.argv[3] ?? "browser-results");
assert(process.argv[2], "Supply the qualified installed consumer-dist directory");
const driverPath = process.env.BOARD_UI_PLAYWRIGHT;
assert(driverPath, "Supply the parent-prepared Playwright 1.63.0 module path as BOARD_UI_PLAYWRIGHT");
const driverManifest = JSON.parse(await readFile(new URL("./package.json", pathToFileURL(resolve(driverPath))), "utf8"));
assert.equal(driverManifest.version, "1.63.0", "Use the current pinned browser toolchain");
const { chromium } = await import(pathToFileURL(resolve(driverPath)).href);
const { scenarios } = await import("./scenarios.mjs");
const mime = { ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".html": "text/html" };
await mkdir(output, { recursive: true });
const server = createServer(async (request, response) => {
  const path = resolve(directory, "." + new URL(request.url, "http://localhost").pathname);
  if (!path.startsWith(directory + sep) && path !== directory) { response.writeHead(403).end(); return; }
  try {
    const target = path === directory ? resolve(directory, "index.html") : path;
    response.setHeader("Content-Type", mime[extname(target)] ?? "application/octet-stream");
    response.end(await readFile(target));
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let browser;
const receipts = [];
try {
  browser = await chromium.launch({ headless: true });
  for (const scenario of scenarios) {
    const context = await browser.newContext({ viewport: { width: 768, height: 1000 }, reducedMotion: "reduce",
      deviceScaleFactor: scenario.deviceScaleFactor ?? 1 });
    const page = await context.newPage(); page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const requests = [];
    page.on("request", request => requests.push(request.url()));
    await context.tracing.start({ screenshots: true, snapshots: true });
    const started = performance.now();
    try {
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.locator("cg-board").waitFor({ state: "visible" });
      await scenario.run(page);
      assert.deepEqual(errors, [], "No page errors");
      assert(requests.every(url => url.startsWith(`http://127.0.0.1:${server.address().port}/`)), "No external runtime requests");
      await page.screenshot({ path: resolve(output, `${scenario.id}.png`) });
      receipts.push({ id: scenario.id, outcome: "passed", seconds: (performance.now() - started) / 1000 });
    } catch (error) {
      receipts.push({ id: scenario.id, outcome: "failed", error: String(error) });
      throw error;
    } finally {
      await context.tracing.stop({ path: resolve(output, `${scenario.id}.zip`) });
      await context.close();
    }
  }
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
  const hashes = {};
  for (const name of ["main.js", "main.css", "metafile.json"])
    hashes[name] = createHash("sha256").update(await readFile(resolve(directory, name))).digest("hex");
  await writeFile(resolve(output, "results.json"), JSON.stringify({ receipts, hashes, driverPath,
    browserVersion: browser?.version(), node: process.version, finished: new Date().toISOString() }, null, 2));
}
