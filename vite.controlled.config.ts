import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/postcss";
import { defineConfig } from "vite";
import { moduleResolution } from "./module-resolution";
import { offlineShellAssets } from "./scripts/offline-shell-assets";

if (!process.env.TEMPO_CONTROLLED_BUILD_DIR) throw new Error("Use scripts/test-controlled-browser.mjs for an owned build directory");
export default defineConfig({
  resolve: moduleResolution,
  root: "static", base: "/", publicDir: "../public",
  define: { __TEMPO_DEMO__: "false", __TEMPO_API_URL__: JSON.stringify("") },
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [react(), offlineShellAssets()],
  build: { outDir: process.env.TEMPO_CONTROLLED_BUILD_DIR, emptyOutDir: true, sourcemap: true },
});
