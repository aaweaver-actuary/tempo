import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { Board, fitBoardSize } from "@tempo/board-ui";

const html = renderToString(createElement(Board, { fen: "8/8/8/8/8/8/4P3/4K2k w - - 0 1",
  positionIdentity: "installed", positionRevision: 0, mode: "legal", orientation: "white", turn: "white", check: false }));
assert.match(html, /Interactive chessboard/); assert.match(html, /data-position-identity="installed"/);
assert.equal(fitBoardSize(643), 640);
const css = await readFile(new URL(import.meta.resolve("@tempo/board-ui/styles.css")), "utf8");
assert.match(css, /:focus-visible/); assert.match(css, /tempo-pieces-merida/);
for (const color of ["w", "b"]) for (const role of ["P", "N", "B", "R", "Q", "K"])
  await access(new URL(import.meta.resolve(`@tempo/board-ui/assets/merida/${color}${role}.svg`)));
await access(new URL(import.meta.resolve("@tempo/board-ui/notices/GPL-3.0.txt")));
assert.throws(() => import.meta.resolve("@tempo/board-ui/dist/controller.js"), { code: "ERR_PACKAGE_PATH_NOT_EXPORTED" });
console.log("Installed public API, React rendering, CSS, 12 piece assets, notices, and private export guard passed");
