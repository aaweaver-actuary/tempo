// @vitest-environment node
import { readFile, readdir } from "node:fs/promises";
import { resolve, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { expect, it } from "vitest";

const root = fileURLToPath(new URL("..", import.meta.url));
it("BUI-13 renderer imports stay within its package, React peer, and pinned Chessground", async () => {
  const directory = resolve(root, "src");
  const files = (await readdir(directory)).filter(file => /\.tsx?$/.test(file));
  expect(files.length).toBeGreaterThan(0);
  const allowedExternal = /^(react(?:\/jsx-runtime)?|@lichess-org\/chessground(?:\/(?:api|config|draw|state|wrap|render|autoPieces|svg|events|drag|util|types))?)$/;
  for (const file of files) {
    const source = await readFile(resolve(directory, file), "utf8");
    const syntax = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
    const inspect = (node: ts.Node) => {
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        const specifier = node.moduleSpecifier.text;
        if (specifier.startsWith(".")) expect(resolve(dirname(resolve(directory, file)), specifier).startsWith(directory + sep), file).toBe(true);
        else expect(allowedExternal.test(specifier), `${file}: ${specifier}`).toBe(true);
      }
      if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        ts.isIdentifier(node.expression) && node.expression.text === "require")) throw new Error("Dynamic dependencies are not part of this renderer");
      ts.forEachChild(node, inspect);
    };
    inspect(syntax);
  }
});

it("BUI-14 package metadata, styles, pieces and attribution form an installable local contract", async () => {
  const manifest = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
  expect(manifest.dependencies).toEqual({ "@lichess-org/chessground": "10.1.1" });
  expect(manifest.peerDependencies).toEqual({ react: "^19.2.6" });
  expect(manifest.private).not.toBe(true);
  expect(manifest.exports["./styles.css"]).toBe("./styles/board.css");
  const css = await readFile(resolve(root, "styles/board.css"), "utf8");
  const references = [...css.matchAll(/url\("([^\"]+)"\)/g)];
  expect(references).toHaveLength(12);
  for (const reference of references) expect(await readFile(resolve(root, "styles", reference[1]), "utf8")).toContain("<svg");
  expect(css).toContain(".tempo-board cg-container { width: 100% !important; height: 100% !important; }");
  const notices = await readFile(resolve(root, "notices/THIRD-PARTY.md"), "utf8");
  expect(notices).toContain("Armando Hernandez Marroquin"); expect(notices).toContain("Colin M. L. Burnett");
});

// Evaluate the stylesheet's hard-stop conic pattern at square interiors. This
// checks color semantics rather than requiring a particular phase/stop spelling;
// native raster/pixel proof remains with the separately granted browser lane.
function squareColorSampler(css: string, theme: string) {
  const rule = (selector: string) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
    expect(match, `CSS rule ${selector}`).not.toBeNull();
    return match![1];
  };
  const palette = new Map<string, string>();
  for (const block of [rule(".tempo-board-surface"), ...(theme === "brown" ? [] : [rule(`.tempo-board .tempo-theme-${theme}`)])])
    for (const match of block.matchAll(/(--tempo-(?:light|dark)):\s*(#[a-f\d]{6})/gi)) palette.set(match[1], match[2]);
  const board = rule(".tempo-board cg-board");
  const gradient = board.match(/conic-gradient\(from ([\d.]+)deg,\s*(.*?)\);/);
  expect(gradient, "Declared conic gradient").not.toBeNull();
  let position = 0;
  const stops = gradient![2].split(",").flatMap(stop => {
    const match = stop.trim().match(/^var\((--tempo-(?:light|dark))\)\s+([\d.%\s]+)$/);
    expect(match, `Supported color stop ${stop}`).not.toBeNull();
    return match![2].trim().split(/\s+/).map(value => {
      // CSS color-stop fixup clamps a stop behind the previous position.
      position = Math.max(position, Number.parseFloat(value));
      return { position, color: palette.get(match![1])! };
    });
  });
  for (let index = 1; index < stops.length; index++)
    if (stops[index].color !== stops[index - 1].color)
      expect(stops[index].position, "Square colors have hard edges").toBe(stops[index - 1].position);
  const size = board.match(/background-size:\s*([\d.]+)%\s+([\d.]+)%;/);
  expect(size, "Declared tile dimensions").not.toBeNull();
  const [width, height] = size!.slice(1).map(value => Number(value) * 8 / 100);
  return (column: number, row: number, offset: number) => {
    const x = (column + offset) % width - width / 2;
    const y = (row + offset) % height - height / 2;
    // CSS conic angles start upward and increase clockwise.
    const angle = ((Math.atan2(x, -y) * 180 / Math.PI - Number(gradient![1])) % 360 + 360) % 360 / 3.6;
    return (stops.find(stop => angle < stop.position) ?? stops.at(-1)!).color;
  };
}

it.each([
  ["brown", "white", "#b58863", "#f0d9b5"], ["brown", "black", "#b58863", "#f0d9b5"],
  ["blue", "white", "#6f8fa8", "#dee3e6"], ["blue", "black", "#6f8fa8", "#dee3e6"],
  ["green", "white", "#6e8268", "#e5e3ce"], ["green", "black", "#6e8268", "#e5e3ce"],
])("BUI-25 %s theme/%s orientation has dark a1/h8 and light h1/a8 across all 64 squares", async (theme, orientation, dark, light) => {
  const sample = squareColorSampler(await readFile(resolve(root, "styles/board.css"), "utf8"), theme);
  for (let file = 0; file < 8; file++) for (let rank = 0; rank < 8; rank++) {
    const column = orientation === "white" ? file : 7 - file;
    const row = orientation === "white" ? 7 - rank : rank;
    const square = String.fromCharCode(97 + file) + (rank + 1);
    for (const offset of [0.25, 0.5, 0.75])
      expect(sample(column, row, offset), `${square} interior ${offset}`).toBe((file + rank) % 2 === 0 ? dark : light);
  }
});
