import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import BuilderView from "../../app/views/analysis_view";
import { Settings } from "../../app/utils/settings";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));
vi.mock("../../app/lib/analysis-engines", () => ({
  analyzeWithStockfish: vi.fn(async () => []),
  analyzeWithMaia: vi.fn(async () => []),
}));

it("Builder unrelated rerender does not rewrite repertoire selection or session", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input) => Response.json(
    String(input).includes("/api/repertoire/lines")
      ? { lines: [{
          id: "line-one",
          repertoire_id: "white-one",
          repertoire_name: "White repertoire",
          name: "First line",
          trained_color: "white",
          start_fen: new Chess().fen(),
          moves: ["e2e4", "e7e5"],
        }] }
      : { annotations: [] },
  )));
  const storageWrites = vi.spyOn(Storage.prototype, "setItem");
  const props = {
    imported: [],
    settings: new Settings(),
    theme: "brown" as const,
    pieceSet: "cburnett" as const,
  };
  const view = render(<BuilderView {...props} />);
  await waitFor(() => expect(
    (screen.getByRole("combobox", { name: "Active repertoire" }) as unknown as HTMLSelectElement).value,
  ).toBe("white-one"));
  await waitFor(() => {
    expect(localStorage.getItem("tempo-active-repertoire-white")).toBe("white-one");
    expect(JSON.parse(localStorage.getItem("tempo-builder-session") ?? "null")?.activeRepertoireId)
      .toBe("white-one");
  });
  storageWrites.mockClear();
  view.rerender(<BuilderView {...props} />);
  expect(storageWrites.mock.calls.filter(([key]) =>
    key === "tempo-active-repertoire-white" || key === "tempo-builder-session",
  )).toEqual([]);
});
