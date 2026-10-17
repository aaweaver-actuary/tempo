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
  const allowedExternal = /^(react(?:\/jsx-runtime)?|@lichess-org\/chessground(?:\/(?:api|config|draw))?)$/;
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
