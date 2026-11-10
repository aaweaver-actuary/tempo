import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../pages-dist/", import.meta.url);
const required = [
  "index.html",
  "sw.js",
  "favicon.svg",
  "tempo-core/tempo_core.js",
  "tempo-core/tempo_core_bg.wasm",
  "data/tactics-decks.json",
  "data/tactics-catalog.json",
  "data/tactics-packs",
  "sounds/move-self.mp3",
  "sounds/move-opponent.mp3",
  "sounds/capture.mp3",
  "sounds/check.mp3",
  "sounds/castle.mp3",
  "sounds/promote.mp3",
  "sounds/illegal.mp3",
];

const missing = required.filter((path) => !existsSync(new URL(path, root)));
if (missing.length) throw new Error(`Static build is missing: ${missing.join(", ")}`);
if (!readdirSync(new URL("data/tactics-packs/", root)).some((name) => name.endsWith(".json")))
  throw new Error("Static build has no Tactics packs");

const html = readFileSync(new URL("index.html", root), "utf8");
if (!html.includes('/tempo/assets/')) throw new Error("Generated HTML does not use the /tempo/ base path");
if (html.includes('/src/main.tsx')) throw new Error("Generated HTML still references the Vite source entry");
if (!html.includes('location.protocol === "file:"')) throw new Error("Generated HTML is missing the file-protocol explanation");

let total = 0;
let largest = { path: "", size: 0 };
function measure(directory, prefix = "") {
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    const relative = join(prefix, name);
    const stats = statSync(path);
    if (/^(?:engines|maia3|ort)(?:\/|$)/i.test(relative) || /(?:stockfish|\.nnue$|maia3|ort-wasm)/i.test(relative))
      throw new Error(`Static build contains retired compute payload: ${relative}`);
    if (stats.isDirectory()) measure(path, relative);
    else {
      total += stats.size;
      if (stats.size > largest.size) largest = { path: relative, size: stats.size };
    }
  }
}
measure(root.pathname);
if (total >= 1_000_000_000) throw new Error(`Static site is too large: ${total} bytes`);
if (largest.size >= 100_000_000) throw new Error(`Static asset is too large: ${largest.path}`);
console.log(`Static build verified: ${required.length} required assets, ${Math.round(total / 1_000_000)} MB total, largest ${largest.path} (${Math.round(largest.size / 1_000_000)} MB).`);
