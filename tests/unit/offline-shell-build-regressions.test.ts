// @vitest-environment node
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { offlineShellAssets } from "../../scripts/offline-shell-assets";
import { maiaCacheAsset } from "../../scripts/maia-cache-asset";
import { build } from "vite";

it("offline build inventories generated workers and shared imports for root and Pages shells", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tempo-offline-build-"));
  try {
    const workerSource = await readFile("public/sw.js", "utf8");
    const hook = offlineShellAssets().writeBundle!;
    const handler = typeof hook === "function" ? hook : hook.handler;
    const emittedFiles = { "assets/index-a.js": {}, "assets/study.worker-b.js": {},
      "assets/shared-c.js": {}, "assets/style-d.css": {}, "assets/index-a.js.map": {} };
    await writeFile(join(directory, "sw.js"), workerSource);
    await handler.call({} as never, { dir: directory } as never, emittedFiles as never);
    const generatedWorker = await readFile(join(directory, "sw.js"), "utf8");
    expect(generatedWorker).toContain('"assets/study.worker-b.js"');
    expect(generatedWorker).toContain('"assets/shared-c.js"');
    expect(generatedWorker).toContain('"assets/style-d.css"');
    expect(generatedWorker).not.toContain('"assets/index-a.js.map"');
    // Inventory paths stay relative; the service worker resolves the active deployment scope.
    expect(generatedWorker).not.toContain('"/assets/study.worker-b.js"');
    await expect(handler.call({} as never, { dir: directory } as never, { "assets/index.js": {} } as never))
      .rejects.toThrow("Offline shell build has no study worker");
  } finally { await rm(directory, { recursive: true }); }
});

it("classic Maia cache asset builds from its single database source for root and Pages", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tempo-maia-cache-build-"));
  try {
    const entry = join(directory, "entry.js");
    await writeFile(entry, "export const retained = true;\n");
    const expected = await readFile("database/browser/maia-worker-cache.js");
    for (const base of ["/", "/tempo/"]) {
      const result = await build({ configFile: false, root: directory, base, publicDir: false,
        logLevel: "silent", plugins: [maiaCacheAsset()],
        build: { write: false, rollupOptions: { input: entry } } });
      const outputs = (Array.isArray(result) ? result : [result]).flatMap(value => {
        if (!("output" in value)) throw new Error("Expected a completed asset build");
        return value.output;
      });
      const asset = outputs.find(value => value.type === "asset" && value.fileName === "maia-worker-cache.js");
      expect(asset?.type).toBe("asset");
      expect(Buffer.from((asset as { source: Uint8Array }).source)).toEqual(expected);
    }
  } finally { await rm(directory, { recursive: true }); }
});

it("classic Maia cache development URL serves canonical bytes and leaves other routes alone", async () => {
  const expected = await readFile("database/browser/maia-worker-cache.js");
  for (const base of ["/", "/tempo/"]) {
    const plugin = maiaCacheAsset();
    const resolved = plugin.configResolved!;
    await (typeof resolved === "function" ? resolved : resolved.handler).call({} as never, { base } as never);
    let middleware: (request: { url: string; method: string }, response: unknown, next: () => void) => void;
    const configure = plugin.configureServer!;
    await (typeof configure === "function" ? configure : configure.handler).call({} as never,
      { middlewares: { use(value: typeof middleware) { middleware = value; } } } as never);
    const headers: Record<string, string> = {};
    await new Promise<void>((resolve, reject) => {
      middleware!({ url: `${base}maia-worker-cache.js?v=1`, method: "GET" }, {
        setHeader(name: string, value: string) { headers[name] = value; },
        end(bytes: Buffer) { try { expect(bytes).toEqual(expected); resolve(); } catch (error) { reject(error); } },
      }, () => reject(new Error("Canonical cache URL fell through")));
    });
    expect(headers["Content-Type"]).toBe("text/javascript");
    let fellThrough = false;
    middleware!({ url: `${base}unrelated.js`, method: "GET" }, {}, () => { fellThrough = true; });
    expect(fellThrough).toBe(true);
  }
});
