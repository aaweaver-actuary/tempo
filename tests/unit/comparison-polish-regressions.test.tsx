import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { MoveComparisonTable } from "../../app/components/move-comparison-table";
import { asUciMove, asSanMove } from "../../app/types";
const e4 = {
  uci: asUciMove("e2e4"),
  san: asSanMove("e4"),
  cp: 40,
  probability: 0.6,
  white: 60,
  draws: 20,
  black: 20,
};
const d4 = {
  uci: asUciMove("d2d4"),
  san: asSanMove("d4"),
  cp: 20,
  probability: 0.3,
  white: 10,
  draws: 20,
  black: 20,
};
const nf3 = { uci: asUciMove("g1f3"), san: asSanMove("Nf3") };
const props = {
  repertoire: [nf3],
  engine: [e4, d4],
  maia: [e4, d4],
  lichess: [e4, d4],
  masters: [d4],
  turn: "white" as const,
  onPlay: vi.fn(),
  onHover: vi.fn(),
};
const moveOrder = () =>
  within(screen.getByRole("table"))
    .getAllByRole("row")
    .slice(1)
    .map(
      (row) =>
        within(row).getByRole("button", { name: /^(e4|d4|Nf3)$/ }).textContent,
    );
it("comparison headers sort ascending then descending with missing values last and preserve playable hover rows", () => {
  render(<MoveComparisonTable {...props} />);
  const header = screen.getByRole("columnheader", { name: "Stockfish" });
  fireEvent.click(within(header).getByRole("button"));
  expect(moveOrder()).toEqual(["d4", "e4", "Nf3"]);
  expect(header.getAttribute("aria-sort")).toBe("ascending");
  fireEvent.click(within(header).getByRole("button"));
  expect(moveOrder()).toEqual(["e4", "d4", "Nf3"]);
  expect(header.getAttribute("aria-sort")).toBe("descending");
  fireEvent.focus(screen.getByRole("button", { name: "e4" }));
  expect(props.onHover).toHaveBeenCalledWith("e2e4");
  fireEvent.click(screen.getByRole("button", { name: "e4" }));
  expect(props.onPlay).toHaveBeenCalledWith("e2e4");
});
it.each([
  ["Move", ["d4", "e4", "Nf3"], ["Nf3", "e4", "d4"]],
  ["Repertoire", ["e4", "d4", "Nf3"], ["Nf3", "e4", "d4"]],
  ["Maia", ["d4", "e4", "Nf3"], ["e4", "d4", "Nf3"]],
  ["Lichess", ["d4", "e4", "Nf3"], ["e4", "d4", "Nf3"]],
  ["Masters", ["d4", "Nf3", "e4"], ["d4", "Nf3", "e4"]],
])(
  "comparison %s column sorts both directions with missing values last",
  (column, ascending, descending) => {
    render(<MoveComparisonTable {...props} />);
    const header = screen.getByRole("columnheader", { name: String(column) });
    fireEvent.click(within(header).getByRole("button"));
    expect(moveOrder()).toEqual(ascending);
    fireEvent.click(within(header).getByRole("button"));
    expect(moveOrder()).toEqual(descending);
  },
);

it("compact database cells reveal WDL frequency and practical score only on demand", () => {
  render(<MoveComparisonTable {...props} />);
  expect(screen.queryByText(/frequency/)).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Lichess details for e4" }),
  );
  expect(screen.getByText(/60%.*20%.*20%/)).toBeTruthy();
  expect(screen.getByText(/67% frequency/)).toBeTruthy();
});
