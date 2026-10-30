import { expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import Navbar, { WORKSPACES } from "../../app/components/Navbar";
import { mapPackagedPuzzleToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import pawnEndgames from "../../public/data/tactics-packs/pawnEndgame-easy-01.json";
import rookEndgames from "../../public/data/tactics-packs/rookEndgame-easy-01.json";

it("Endgames has no navigation or menu entry while Train and Tactics remain", () => {
  const navigate = vi.fn();
  expect(WORKSPACES.some(view => String(view.id) === "endgames")).toBe(false);
  render(<Navbar view="train" setView={navigate} />);
  expect(screen.queryByRole("button", { name: "Endgames" })).toBeNull();
  expect(screen.getAllByRole("button", { name: "Train" }).length).toBeGreaterThan(0);
  expect(screen.getAllByRole("button", { name: "Tactics" }).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  expect(screen.queryByRole("button", { name: "Endgames" })).toBeNull();
  fireEvent.click(screen.getAllByRole("button", { name: "Tactics" })[0]);
  expect(navigate).toHaveBeenCalledWith("tactics");
});

it.each([{ records: pawnEndgames }, { records: rookEndgames }])("retained endgame-themed Tactics packs still produce legal playable puzzles", ({ records }) => {
  for (const record of records) {
    const card = mapPackagedPuzzleToPracticeCard(record as import("../../app/types").PackagedPuzzle);
    expect(card).not.toBeNull();
    expect(card?.kind).toBe("puzzle");
    expect(card!.moves.length).toBeGreaterThan(0);
  }
});
