import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/postcss";
import { defineConfig } from "vite";

export default defineConfig({
  root: "static",
  base: "/tempo/",
  publicDir: "../public",
  css: { postcss: { plugins: [tailwindcss()] } },
  plugins: [react()],
  build: {
    outDir: "../pages-dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});

