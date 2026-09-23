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
import { scanGame, scanGameTwoPass } from "../../app/lib/game-scan";
import { asSanMove, asUciMove } from "../../app/types";

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

it("Builder requires Lichess sign-in before Explorer requests and offers its connection action", () => {
  sessionStorage.removeItem("tempo-lichess-token");
  localStorage.removeItem("tempo-lichess-token");
  const fetcher = vi.fn(async () => Response.json({ lines: [], annotations: [] }));
  vi.stubGlobal("fetch", fetcher);
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
    "Pause databases",
  );
  expect(panel.textContent).toContain("Lichess: Sign in required");
  expect(panel.textContent).toContain("Masters: Sign in required");
  expect(screen.getByRole("button", { name: "Connect Lichess" })).toBeTruthy();
  expect((fetcher.mock.calls as unknown as Array<[RequestInfo | URL]>).some(([url]) => String(url).includes("explorer.lichess.org"))).toBe(false);
});

it("Builder OAuth completion stores the session token and retries the current Explorer position", async () => {
  localStorage.removeItem("tempo-lichess-token");
  sessionStorage.removeItem("tempo-lichess-token");
  sessionStorage.setItem("tempo-lichess-verifier", "pkce-verifier");
  sessionStorage.setItem("tempo-lichess-state", "oauth-state");
  window.history.replaceState({}, "", "/?code=oauth-code&state=oauth-state");
  const explorerPayload = {
    white: 10,
    draws: 5,
    black: 5,
    moves: [{ uci: "e2e4", san: "e4", white: 6, draws: 2, black: 2 }],
  };
  const fetcher = vi.fn(async (input) => {
    const url = String(input);
    if (url.includes("lichess.org/api/token")) return Response.json({ access_token: "session-token" });
    if (url.includes("explorer.lichess.org")) return Response.json(explorerPayload);
    return Response.json({ lines: [], annotations: [] });
  });
  vi.stubGlobal("fetch", fetcher);

  render(<BuilderView imported={[]} settings={new Settings()} theme="brown" pieceSet="cburnett" />);

  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).includes("explorer.lichess.org"))).toHaveLength(2));
  expect(sessionStorage.getItem("tempo-lichess-token")).toBe("session-token");
  expect(localStorage.getItem("tempo-lichess-token")).toBeNull();
  for (const [url, options] of fetcher.mock.calls as unknown as Array<[string, RequestInit]>)
    if (url.includes("explorer.lichess.org"))
      expect(options.headers).toEqual({ Authorization: "Bearer session-token" });
  await waitFor(() => expect(screen.getByText(/Lichess: Ready/)).toBeTruthy());
  window.history.replaceState({}, "", "/");
});

it("Builder does not retry Explorer requests with a rejected token until reconnection", async () => {
  sessionStorage.setItem("tempo-lichess-token", "rejected-token");
  const explorerUrls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input) => {
    const url = String(input);
    if (url.includes("explorer.lichess.org")) {
      explorerUrls.push(url);
      return new Response("", { status: 401 });
    }
    return Response.json({ lines: [], annotations: [] });
  }));

  render(<BuilderView imported={[]} settings={new Settings()} theme="brown" pieceSet="cburnett" />);

  await waitFor(() => expect(explorerUrls).toHaveLength(2));
  expect(screen.getByRole("button", { name: "Reconnect Lichess" })).toBeTruthy();
  fireEvent.click(screen.getByText("d2d4"));
  await waitFor(() => expect(screen.getByText(/Lichess: Reconnect required/)).toBeTruthy());
  expect(explorerUrls).toHaveLength(2);
  sessionStorage.removeItem("tempo-lichess-token");
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
    { uci: asUciMove("e2e4"), san: asSanMove("e4"), cp: cp.shift() },
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

it("two-pass game scan preserves a bounded MultiPV set with its decision FEN", async () => {
  const startFen = new Chess().fen();
  const evaluate = vi.fn(async (fen: string) =>
    fen.includes(" w ")
      ? [
          { uci: asUciMove("e2e4"), san: asSanMove("e4"), cp: 30, pv: [asUciMove("e2e4"), asUciMove("e7e5")] },
          { uci: asUciMove("d2d4"), san: asSanMove("d4"), cp: 25, pv: [asUciMove("d2d4"), asUciMove("d7d5")] },
          { uci: asUciMove("g1f3"), san: asSanMove("Nf3"), cp: 20, pv: [asUciMove("g1f3"), asUciMove("d7d5")] },
          { uci: asUciMove("c2c4"), san: asSanMove("c4"), cp: 15, pv: [asUciMove("c2c4"), asUciMove("e7e5")] },
          { uci: asUciMove("b1c3"), san: asSanMove("Nc3"), cp: 10, pv: [asUciMove("b1c3"), asUciMove("d7d5")] },
        ]
      : [
          { uci: asUciMove("e7e5"), san: asSanMove("e5"), cp: 20, pv: [asUciMove("e7e5"), asUciMove("g1f3")] },
        ],
  );

  const result = await scanGameTwoPass(startFen, ["e4"], "white", evaluate);

  expect(result[0]).toMatchObject({
    position_fen: startFen,
    best_move_uci: "e2e4",
    candidate_lines: [
      { uci: "e2e4", cp: 30 },
      { uci: "d2d4", cp: 25 },
      { uci: "g1f3", cp: 20 },
      { uci: "c2c4", cp: 15 },
      { uci: "b1c3", cp: 10 },
    ],
  });
});
