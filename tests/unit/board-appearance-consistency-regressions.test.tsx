import { act, render } from "@testing-library/react";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import ts from "typescript";
import { PersistentBoardShell } from "../../app/components/board/persistent-board-shell";
import { useBoardShellStore } from "../../app/state/board-shell-store";

const entries = [
  "app/components/board/persistent-board-shell.tsx",
  "app/components/canonical-prefix-dialog.tsx", "app/components/opening-segmentation.tsx",
  "app/components/position-solution-editor.tsx", "app/components/repertoire-integrity-dialog.tsx",
  "app/views/defense_training_view.tsx", "app/views/endgames_view.tsx", "app/views/study_exercise_runner.tsx",
  "app/views/tactics_view.tsx", "app/views/training_view.tsx", "app/views/tree_browser.tsx",
  "app/features/game-review/GameReviewView.tsx", "app/features/repertoire-review/PositionViewer.tsx",
  "apps/workbench-web/src/game-browser.ts", "apps/workbench-web/src/workbench.ts",
];
function sources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sources(path) : /\.(tsx?|css)$/.test(entry.name) ? [path] : [];
  });
}

it("shared_board_appearance_all_fifteen_production_entries_use_the_shared_renderer_boundary", () => {
  expect(entries).toHaveLength(15);
  for (const file of entries) {
    const source = readFileSync(file, "utf8");
    expect(source, file).toMatch(/<Chessboard\b|<Board\b|mountBoard\(/);
    expect(source, file).not.toMatch(/\bChessground\s*\(/);
  }
  expect(readFileSync("app/components/board/chessboard.tsx", "utf8")).toMatch(/<Board\b/);
  expect(readFileSync("apps/workbench-web/src/board.tsx", "utf8")).toMatch(/<Board\b/);
  expect(readFileSync("app/views/studies_view.tsx", "utf8")).toContain('useBoardPublisher("studies"');
  for (const file of [...sources("app"), ...sources("apps/workbench-web/src")].filter(path => /\.tsx?$/.test(path))) {
    const source = readFileSync(file, "utf8");
    const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
    for (const statement of tree.statements) {
      if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier) &&
          statement.moduleSpecifier.text.startsWith("@lichess-org/chessground")) {
        expect(statement.importClause?.isTypeOnly, file).toBe(true);
      }
    }
  }
  for (const file of [...sources("app"), "apps/workbench-web/public/style.css"].filter(path => path.endsWith(".css"))) {
    expect(readFileSync(file, "utf8"), file).not.toMatch(/cg-board[^{}]*\{[^}]*background|url\([^)]*pieces\//);
  }
});

it("shared_board_appearance_settings_updates_preserve_the_persistent_board_and_position", () => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, width: 640, height: 640, right: 640, bottom: 640, toJSON() {} });
  const view = render(<PersistentBoardShell />);
  const board = view.container.querySelector("cg-board");
  const fen = useBoardShellStore.getState().board.fen;
  act(() => useBoardShellStore.setState(state => ({ board: { ...state.board, theme: "blue", pieceSet: "merida" } })));
  expect(view.container.querySelector("cg-board")).toBe(board);
  expect(view.container.querySelector(".tempo-theme-blue.tempo-pieces-merida")).not.toBeNull();
  expect(useBoardShellStore.getState().board.fen).toBe(fen);
});
