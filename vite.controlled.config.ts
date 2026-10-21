import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/postcss";
import { defineConfig } from "vite";
import { moduleResolution } from "./module-resolution";
import { offlineShellAssets } from "./scripts/offline-shell-assets";
import { maiaCacheAsset } from "./scripts/maia-cache-asset";
if (!process.env.TEMPO_CONTROLLED_BUILD_DIR || !process.env.TEMPO_CONTROLLED_PUBLIC_DIR)
  throw new Error("Use scripts/test-controlled-browser.mjs for owned build inputs/output");
export default defineConfig({
  resolve: moduleResolution,
  root: "static", base: "/", publicDir: process.env.TEMPO_CONTROLLED_PUBLIC_DIR,
  define: { __TEMPO_DEMO__: "false", __TEMPO_API_URL__: JSON.stringify("") },
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [react(), maiaCacheAsset(), offlineShellAssets()],
  build: { outDir: process.env.TEMPO_CONTROLLED_BUILD_DIR, emptyOutDir: true, sourcemap: true, rollupOptions: { input: { index: resolve("static/index.html"), priority: resolve("static/study-priority-proof.html") } } },
});
