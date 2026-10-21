import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

const sourcePath = fileURLToPath(new URL("../database/browser/maia-worker-cache.js", import.meta.url));
const fileName = "maia-worker-cache.js";

/** Preserve the public classic worker's cache script URL from one source file. */
export function maiaCacheAsset(): Plugin {
  let assetPath = `/${fileName}`;
  return {
    name: "tempo-maia-cache-asset",
    applyToEnvironment: environment => environment.name === "client",
    configResolved(config) { assetPath = `${config.base}${fileName}`; },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (!["GET", "HEAD"].includes(request.method ?? "") || request.url?.split("?")[0] !== assetPath) return next();
        void readFile(sourcePath).then(bytes => {
          response.setHeader("Content-Type", "text/javascript");
          response.end(request.method === "HEAD" ? undefined : bytes);
        }).catch(next);
      });
    },
    async generateBundle() {
      this.emitFile({ type: "asset", fileName, source: await readFile(sourcePath) });
    },
  };
}
