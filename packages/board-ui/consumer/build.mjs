import assert from "node:assert/strict";
import { build } from "esbuild";
import { writeFile, mkdir } from "node:fs/promises";

const result = await build({ entryPoints: ["main.tsx"], bundle: true, format: "esm", platform: "browser",
  outdir: "dist", loader: { ".svg": "file" }, assetNames: "assets/[name]-[hash]", metafile: true,
  define: { "process.env.NODE_ENV": '"production"' }, minify: true, logLevel: "info" });
const inputs = Object.keys(result.metafile.inputs);
assert(inputs.some(path => path.includes("@tempo/board-ui/dist/Board.js")));
assert(inputs.some(path => path.includes("@lichess-org/chessground/dist/chessground.js")));
assert(!inputs.some(path => /(?:^|\/)(app|apps|services|src)\//.test(path)));
assert.equal(inputs.filter(path => path.endsWith("/react/index.js")).length, 1);
assert.equal(Object.keys(result.metafile.outputs).filter(path => path.endsWith(".svg")).length, 12);
await mkdir("dist", { recursive: true });
await writeFile("dist/metafile.json", JSON.stringify(result.metafile, null, 2));
await writeFile("dist/index.html", '<!doctype html><html lang="en"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Board UI isolated consumer</title><link rel="stylesheet" href="./main.css"><body><div id="root"></div><script type="module" src="./main.js"></script></body></html>');
