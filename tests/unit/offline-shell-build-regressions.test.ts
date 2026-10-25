// @vitest-environment node
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { offlineShellAssets } from "../../scripts/offline-shell-assets";

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
