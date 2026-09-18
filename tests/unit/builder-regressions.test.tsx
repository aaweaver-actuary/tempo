import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import BuilderView from "../../app/views/analysis_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { Settings } from "../../app/utils/settings";
import { scanGame } from "../../app/lib/game-scan";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: (props: {
    fen: string;
    orientation: string;
    shapes: unknown[];
    onMove: (from: string, to: string) => void;
    onDrawnShapesChange?: (shapes: Array<{ orig: string; brush: string }>) => void;
  }) => (
    <div
      data-testid="board"
      data-fen={props.fen}
      data-orientation={props.orientation}
      data-shapes={JSON.stringify(props.shapes)}
    >
      <button onClick={() => props.onMove("d2", "d4")}>d2d4</button>
      <button onClick={() => props.onMove("g8", "f6")}>g8f6</button>
      <button
        onClick={() =>
          props.onDrawnShapesChange?.([{ orig: "a4", brush: "yellow" }])
        }
      >
        mark-a4
      </button>
    </div>
  ),
}));
vi.mock("../../app/lib/analysis-engines", () => ({
  analyzeWithStockfish: vi.fn(async () => []),
  analyzeWithMaia: vi.fn(async () => []),
}));

it("Builder comparison exposes database connection and source status rather than unexplained blanks", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ lines: [], annotations: [] })),
  );
  render(
    <BuilderView
      imported={[]}
      settings={new Settings()}
      theme="brown"
      pieceSet="cburnett"
    />,
  );
  const panel = screen
    .getByRole("table", { name: "Move source comparison" })
    .closest("section")!;
  expect(panel.querySelector(".comparison-connect")?.textContent).toBe(
    "Connect databases",
  );
  expect(panel.textContent).toContain("Databases: not connected");
});

it("builder flip preserves repertoire identity and history across remounts", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input) =>
      Response.json(
        String(input).includes("/repertoire/lines")
          ? {
              lines: [
                {
                  id: "black-line",
                  repertoire_id: "black-repertoire",
                  repertoire_name: "Gambits",
                  name: "",
                  trained_color: "black",
                  start_fen: new Chess().fen(),
                  moves: ["d2d4", "g8f6", "0000"],
                },
              ],
            }
          : { annotations: [] },
      ),
    ),
  );
  const props = {
    imported: [],
    settings: new Settings(),
    theme: "brown" as const,
    pieceSet: "cburnett" as const,
  };
  const view = render(<BuilderView {...props} />);
  await waitFor(
    () =>
      expect(
        (
          screen.getByRole("combobox", {
            name: "Active repertoire",
          }) as unknown as HTMLSelectElement
        ).value,
      ).toBe("black-repertoire"),
    { timeout: 3000 },
  );
  fireEvent.change(
    screen.getByRole("combobox", { name: "Active repertoire" }),
    { target: { value: "black-repertoire" } },
  );
  fireEvent.click(screen.getByText("d2d4"));
  const fen = screen.getByTestId("board").getAttribute("data-fen");
  fireEvent.click(screen.getByTitle("Flip board (F)"));
  expect(
    (
      screen.getByRole("combobox", {
        name: "Active repertoire",
      }) as unknown as HTMLSelectElement
    ).value,
  ).toBe("black-repertoire");
  expect(screen.getByTestId("board").getAttribute("data-orientation")).toBe(
    "white",
  );
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(fen);
  view.unmount();
  render(<BuilderView {...props} />);
  await waitFor(
    () =>
      expect(
        (
          screen.getByRole("combobox", {
            name: "Active repertoire",
          }) as unknown as HTMLSelectElement
        ).value,
      ).toBe("black-repertoire"),
    { timeout: 3000 },
  );
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(fen);
  expect(screen.getByText(/Stopped at null move/)).toBeTruthy();
});

it("builder delete line removes Nimzo branch descendants and keeps QGD response", async () => {
  const removePayloads: Array<{ repertoire_id: string; moves: string[] }> = [];
  let nimzoRemoved = false;
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, options) => {
      const url = String(input);
      if (url.endsWith("/api/repertoire/branches/remove")) {
        removePayloads.push(JSON.parse(String(options?.body)));
        nimzoRemoved = true;
        return Response.json({
          deleted_line_count: 1,
          deleted_card_count: 1,
          retained_line_count: 1,
        });
      }
      if (url.includes("/api/repertoire/lines")) {
        return Response.json({
          lines: nimzoRemoved
            ? [
                {
                  id: "qgd-line",
                  repertoire_id: "white-repertoire",
                  repertoire_name: "Queen's Gambit",
                  name: "QGD",
                  trained_color: "white",
                  start_fen: new Chess().fen(),
                  moves: ["d2d4", "d7d5", "c2c4", "e7e6"],
                },
              ]
            : [
                {
                  id: "qgd-line",
                  repertoire_id: "white-repertoire",
                  repertoire_name: "Queen's Gambit",
                  name: "QGD",
                  trained_color: "white",
                  start_fen: new Chess().fen(),
                  moves: ["d2d4", "d7d5", "c2c4", "e7e6"],
                },
                {
                  id: "nimzo-line",
                  repertoire_id: "white-repertoire",
                  repertoire_name: "Queen's Gambit",
                  name: "Nimzo",
                  trained_color: "white",
                  start_fen: new Chess().fen(),
                  moves: ["d2d4", "g8f6", "c2c4", "e7e6", "b1c3", "f8b4"],
                },
              ],
        });
      }
      return Response.json({ annotations: [] });
    }),
  );
  render(
    <BuilderView
      imported={[]}
      settings={new Settings()}
      theme="brown"
      pieceSet="cburnett"
    />,
  );

  await waitFor(
    () =>
      expect(
        (
          screen.getByRole("combobox", {
            name: "Active repertoire",
          }) as unknown as HTMLSelectElement
        ).value,
      ).toBe("white-repertoire"),
    { timeout: 3000 },
  );
  fireEvent.click(screen.getByText("d2d4"));
  fireEvent.click(screen.getByText("g8f6"));
  fireEvent.click(
    screen.getByRole("button", { name: "Delete line from here" }),
  );

  await waitFor(() =>
    expect(removePayloads).toEqual([
      {
        repertoire_id: "white-repertoire",
        starting_fen: new Chess().fen(),
        moves: ["d2d4", "g8f6"],
      },
    ]),
  );
  await waitFor(() => expect(screen.getByText("Deleted 1 line.")).toBeTruthy());
  const previousPlyPosition = new Chess();
  previousPlyPosition.move("d4");
  expect(screen.getByTestId("board").getAttribute("data-fen")).toBe(
    previousPlyPosition.fen(),
  );
  expect(
    (screen.getByRole("button", { name: /Forward/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);

  const repertoirePanel = screen
    .getByText("Active repertoire")
    .closest("section");
  expect(repertoirePanel).toBeTruthy();
  await waitFor(() =>
    expect(
      within(repertoirePanel!).queryByRole("button", { name: /Nf6/ }),
    ).toBeNull(),
  );
});

it("Builder shared board publishes shell ownership and hides local board instance", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ lines: [], annotations: [] })),
  );
  useBoardShellStore.setState((state) => ({
    ...state,
    board: { ...state.board, owner: "train" },
  }));
  const props = {
    imported: [],
    settings: new Settings(),
    theme: "brown" as const,
    pieceSet: "cburnett" as const,
    useSharedBoard: true,
  };
  const view = render(<BuilderView {...props} />);
  await waitFor(() =>
    expect(useBoardShellStore.getState().board.owner).toBe("builder"),
  );
  expect(screen.queryByTestId("board")).toBeNull();
  view.unmount();
  expect(useBoardShellStore.getState().board.owner).toBe("train");
});

it("builder annotation save preserves exact clicked square identity", async () => {
  const savedBodies: Array<{ squares: Array<{ square: string }> }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, options) => {
      const url = String(input);
      if (url.includes("/repertoire/lines"))
        return Response.json({
          lines: [
            {
              id: "line-1",
              repertoire_id: "white-repertoire",
              repertoire_name: "QGD",
              name: "QGD",
              trained_color: "white",
              start_fen: new Chess().fen(),
              moves: ["d2d4", "d7d5"],
            },
          ],
        });
      if (url.includes("/annotations?fen="))
        return Response.json({ annotations: [] });
      if (url.includes("/annotations") && String(options?.method) === "PUT") {
        const body = JSON.parse(String(options?.body));
        savedBodies.push(body);
        return Response.json({
          repertoireId: "white-repertoire",
          fenKey: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -",
          comment: body.comment ?? "",
          arrows: [],
          squares: body.squares,
          updatedAt: "2026-09-18T00:00:00.000Z",
        });
      }
      return Response.json({ annotations: [] });
    }),
  );
  render(
    <BuilderView
      imported={[]}
      settings={new Settings()}
      theme="brown"
      pieceSet="cburnett"
    />,
  );
  await waitFor(
    () =>
      expect(
        (
          screen.getByRole("combobox", {
            name: "Active repertoire",
          }) as unknown as HTMLSelectElement
        ).value,
      ).toBe("white-repertoire"),
    { timeout: 3000 },
  );

  fireEvent.click(screen.getByText("mark-a4"));
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));

  await waitFor(() =>
    expect(savedBodies.at(-1)?.squares).toEqual([{ square: "a4", color: "yellow" }]),
  );
});

it("game scan normalizes black evaluations and identifies missed-punishment opportunities", async () => {
  const cp = [0, 150, 0];
  const evaluate = vi.fn(async () => [
    { uci: "e2e4", san: "e4", cp: cp.shift() },
  ]);
  const result = await scanGame(
    new Chess().fen(),
    ["e4", "e5"],
    "black",
    evaluate,
  );
  expect(result[1]).toMatchObject({
    before_cp: -150,
    after_cp: 0,
    opponent_created_chance: true,
  });
});
