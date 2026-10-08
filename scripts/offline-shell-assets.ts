import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { Plugin } from "vite";

/** Include generated worker bundles and shared imports, not just HTML references. */
export function offlineShellAssets(): Plugin {
  return {
    name: "tempo-offline-shell-assets",
    async writeBundle(outputOptions, bundle) {
      const assetPaths = Object.keys(bundle).filter(path => /\.(?:js|mjs|css)$/.test(path)).sort();
      if (!assetPaths.some(path => /study\.worker[^/]*\.js$/.test(path)))
        throw new Error("Offline shell build has no study worker");
      const serviceWorkerPath = resolve(outputOptions.dir!, "sw.js");
      const serviceWorker = await readFile(serviceWorkerPath, "utf8");
      if (!serviceWorker.includes("const BUILD_SHELL_ASSETS = [];"))
        throw new Error("Offline shell build is missing its asset inventory placeholder");
      await writeFile(serviceWorkerPath, serviceWorker.replace("const BUILD_SHELL_ASSETS = [];",
        `const BUILD_SHELL_ASSETS = ${JSON.stringify(assetPaths)};`));
    },
  };
}
