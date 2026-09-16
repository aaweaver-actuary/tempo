import { fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { expect, it, vi } from "vitest";
import CardEditor from "../../app/views/card_editor";
import { generateLegalEndgameFen } from "../../app/lib/endgame-generator";
import { tablebaseCategoryForWhite } from "../../app/utils/tablebase";
import { asCardId, asFenString, asSanMove } from "../../app/types";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: (props: { fen: string }) => (
    <div data-testid="repair-board" data-fen={props.fen} />
  ),
}));
it("repair uses the shared board and arrows navigate the complete solution; Escape closes", () => {
  const onClose = vi.fn();
  render(
    <CardEditor
      practiceCard={{
        id: asCardId("repair"),
        kind: "puzzle",
        subtitle: "Test",
        title: "Test",
        startingFen: asFenString(new Chess().fen()),
        moves: [asSanMove("e4"), asSanMove("e5")],
        userMoveTarget: 2,
      }}
      boardTheme="brown"
      pieceSet="cburnett"
      onClose={onClose}
      onSave={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /solution/i }));
  fireEvent.keyDown(window, { key: "ArrowDown" });
  expect(
    new Chess(screen.getByTestId("repair-board").getAttribute("data-fen")!).get(
      "e5",
    )?.type,
  ).toBe("p");
  fireEvent.keyDown(window, { key: "ArrowUp" });
  expect(screen.getByTestId("repair-board").getAttribute("data-fen")).toBe(
    new Chess().fen(),
  );
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
});
it("random endgames never leave the nonmoving king in check", () => {
  for (const turn of ["w", "b"] as const)
    for (let i = 0; i < 100; i++) {
      const chess = new Chess(
        generateLegalEndgameFen(
          { white: "KR", black: "K", name: "Rook" },
          turn,
        ),
      );
      const king = chess
        .board()
        .flat()
        .find((piece) => piece?.type === "k" && piece.color !== turn)!;
      expect(chess.isAttacked(king.square, turn)).toBe(false);
    }
});
it("cursed wins and blessed losses respect the fifty-move draw rule", () => {
  expect(tablebaseCategoryForWhite(new Chess().fen(), "cursed-win")).toBe(
    "draw",
  );
  expect(tablebaseCategoryForWhite(new Chess().fen(), "blessed-loss")).toBe(
    "draw",
  );
});
