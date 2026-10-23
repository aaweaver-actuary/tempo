import { fireEvent, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { expect, it, vi } from "vitest";
import CardEditor from "../../app/views/card_editor";
import { probeTablebase } from "../../app/utils/tablebase";
import {
  asCardId,
  asFenString,
  asRepertoireId,
  asSanMove,
} from "../../app/types";

vi.mock("../../app/components/chessboard", async () => {
  const { KeyboardTestBoard } = await import("./keyboard-board-fixture");
  return { Chessboard: (props: React.ComponentProps<typeof KeyboardTestBoard>) => <KeyboardTestBoard {...props} testId="repair-board" /> };
});

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

it("removed tablebase calculation rejects locally without external-provider fallback", async () => {
  const request = vi.spyOn(globalThis, "fetch");
  await expect(probeTablebase(new Chess().fen())).rejects.toThrow("calculation has been removed");
  expect(request).not.toHaveBeenCalled();
  request.mockRestore();
});

it("card repair offers Lichess restoration only for explicitly packaged tactics", () => {
  const card = { id: asCardId("captured"), kind: "puzzle" as const, title: "Synthetic", subtitle: "Captured tactic",
    startingFen: asFenString(new Chess().fen()), moves: [asSanMove("e4")], userMoveTarget: 1,
    sourceUrl: "https://lichess.org/training/synthetic", repertoireId: asRepertoireId("__captured_tactics__") };
  const view = render(<CardEditor practiceCard={card} boardTheme="brown" pieceSet="cburnett" onClose={vi.fn()} onSave={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Restore Lichess original" })).toBeNull();
  view.rerender(<CardEditor practiceCard={{ ...card, repertoireId: asRepertoireId("__tactics__") }} boardTheme="brown" pieceSet="cburnett" onClose={vi.fn()} onSave={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Restore Lichess original" })).toBeTruthy();
});
