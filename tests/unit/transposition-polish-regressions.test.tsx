import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
function seedSession(moves: string[]) {
  const chess = new Chess();
  const startingFen = chess.fen();
  const history = moves.map((san) => {
    const move = chess.move(san);
    return { san: move.san, uci: `${move.from}${move.to}`, fen: chess.fen() };
  });
  localStorage.setItem(
    "tempo-builder-session",
    JSON.stringify({
      version: 1,
      activeRepertoireId: "my-repertoire",
      activeRepertoireByColor: { white: "my-repertoire" },
      orientation: "white",
      startingFen,
      history,
      cursor: history.length,
      branchStart: null,
    }),
  );
}
function setup() {
  const saved: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, options) => {
      const url = String(input);
      if (url.endsWith("/branches")) {
        saved.push(JSON.parse(options.body));
        return Response.json({
          id: "new-route",
          duplicate: false,
          moves: ["g1f3", "d7d5", "d2d4"],
        });
      }
      return Response.json(
        url.includes("/repertoire/lines")
          ? {
              lines: [
                {
                  id: "existing-route",
                  repertoire_id: "my-repertoire",
                  repertoire_name: "Blitz",
                  name: "Queen pawn",
                  trained_color: "white",
                  start_fen: new Chess().fen(),
                  moves: ["d2d4", "d7d5", "g1f3", "g8f6"],
                },
              ],
            }
          : { annotations: [] },
      );
    }),
  );
  return saved;
}
const props = {
  imported: [],
  settings: new Settings(),
  theme: "brown" as const,
  pieceSet: "cburnett" as const,
};
it("exact transposition banner adds the played route as a persisted branch without waiting for Maia", async () => {
  seedSession(["Nf3", "d5", "d4"]);
  const saved = setup();
  render(<BuilderView {...props} />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Add as branch" })).toBeTruthy(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Add as branch" }));
  fireEvent.click(screen.getByRole("button", { name: "Save branch" }));
  await waitFor(() => expect(saved).toHaveLength(1));
  expect(saved[0]).toMatchObject({
    repertoire_id: "my-repertoire",
    moves: ["g1f3", "d7d5", "d2d4"],
  });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Add as branch" })).toBeNull(),
  );
});
it("transposition dismissal survives Builder remount and existing routes never prompt", async () => {
  seedSession(["Nf3", "d5", "d4"]);
  setup();
  const view = render(<BuilderView {...props} />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Dismiss transposition" }),
    ).toBeTruthy(),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Dismiss transposition" }),
  );
  view.unmount();
  const restored = render(<BuilderView {...props} />);
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Active repertoire" }),
    ).toHaveProperty("value", "my-repertoire"),
  );
  expect(screen.queryByRole("button", { name: "Add as branch" })).toBeNull();
  restored.unmount();
  seedSession(["d4", "d5", "Nf3"]);
  render(<BuilderView {...props} />);
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Active repertoire" }),
    ).toHaveProperty("value", "my-repertoire"),
  );
  expect(screen.queryByRole("button", { name: "Add as branch" })).toBeNull();
});
