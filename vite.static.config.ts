import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/postcss";
import { defineConfig } from "vite";

export default defineConfig({
  root: "static",
  base: process.env.TEMPO_TARGET === "local" ? "/" : "/tempo/",
  publicDir: "../public",
  server: { headers: { "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp" }, proxy: { "/api": { target: process.env.TEMPO_PROXY_API ?? "http://127.0.0.1:8000", changeOrigin: true } } },
  define: { __TEMPO_DEMO__: JSON.stringify(process.env.TEMPO_TARGET !== "local"), __TEMPO_API_URL__: JSON.stringify("") },
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [react()],
  build: {
    outDir: process.env.TEMPO_TARGET === "local" ? "../local-dist" : "../pages-dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});
