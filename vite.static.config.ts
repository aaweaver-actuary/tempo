import { workbenchUrlForBuild } from "./app/lib/workbench-navigation";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/postcss";
import { defineConfig } from "vite";
import { moduleResolution } from "./module-resolution";
import { studyComposition } from "./scripts/study-composition";
import { offlineShellAssets } from "./scripts/offline-shell-assets";

export default defineConfig({
  resolve: moduleResolution,
  root: "static",
  base: ["local", "study"].includes(process.env.TEMPO_TARGET ?? "") ? "/" : "/tempo/",
  publicDir: "../public",
  server: { headers: { "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp" }, proxy: { "/api": { target: process.env.TEMPO_PROXY_API ?? "http://127.0.0.1:8000", changeOrigin: true } } },
  define: { __TEMPO_STUDY__: JSON.stringify(process.env.TEMPO_TARGET === "study"), __TEMPO_WORKBENCH_URL__: JSON.stringify(workbenchUrlForBuild(process.env.TEMPO_WORKBENCH_URL)), __TEMPO_DEMO__: JSON.stringify(!["local", "study"].includes(process.env.TEMPO_TARGET ?? "")), __TEMPO_API_URL__: JSON.stringify("") },
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [react(), ...(process.env.TEMPO_TARGET === "study" ? [studyComposition()] : []), offlineShellAssets()],
  build: {
    outDir: process.env.TEMPO_BROWSER_BUILD_DIR ?? (["local", "study"].includes(process.env.TEMPO_TARGET ?? "") ? "../local-dist" : "../pages-dist"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
