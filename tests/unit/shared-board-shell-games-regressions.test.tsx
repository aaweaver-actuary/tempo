import { render, waitFor } from "@testing-library/react";
import { expect, it, vi, beforeEach, afterEach } from "vitest";
import GamesView from "../../app/views/games_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { usesLocalApi } from "../../app/utils/local";
import { readWorkspaceResponse } from "../../app/lib/workspace-data";

vi.mock("../../app/utils/local", () => ({
  usesLocalApi: vi.fn(() => false),
}));

vi.mock("../../app/lib/analysis-engines", () => ({
  analyzeWithStockfish: vi.fn(async () => []),
}));

vi.mock("../../app/hooks/use-background-study", () => ({
  useBackgroundStudy: () => [],
}));

vi.mock("../../app/lib/workspace-data", () => ({
  readWorkspaceResponse: vi.fn(),
  invalidateWorkspaceData: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(usesLocalApi).mockReturnValue(false);
  useBoardShellStore.setState((state) => ({
    ...state,
    board: {
      ...state.board,
      owner: "train",
      interactionMode: "readonly",
      showHint: false,
      shapes: [],
      drawnShapes: [],
    },
  }));
});
afterEach(() => vi.unstubAllGlobals());

it("restoration_empty_games_navigation_never_admits_unused_full_repertoire_indexing", async () => {
  vi.mocked(usesLocalApi).mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({})));
  vi.mocked(readWorkspaceResponse).mockImplementation(async () => Response.json({ games: [], lines: [] }));
  const view = render(<GamesView onAnalyze={vi.fn()} onSettings={vi.fn()} onSync={vi.fn()}
    syncState={{ syncing: false, imported: 0, error: "", lastSuccess: "" }} theme="brown" pieceSet="cburnett" useSharedBoard />);
  await waitFor(() => expect(view.getByText("No games imported", { exact: true })).toBeTruthy());
  expect(vi.mocked(readWorkspaceResponse).mock.calls.some(([url]) => String(url).includes("/api/repertoire/lines"))).toBe(false);
  view.unmount();
});

it("Games shared board publishes readonly state and releases ownership on unmount", async () => {
  const view = render(
    <GamesView
      onAnalyze={vi.fn()}
      onSettings={vi.fn()}
      onSync={vi.fn()}
      syncState={{ syncing: false, imported: 0, error: "", lastSuccess: "" }}
      theme="brown"
      pieceSet="cburnett"
      useSharedBoard
    />,
  );

  await waitFor(() => {
    const shell = useBoardShellStore.getState().board;
    expect(shell.owner).toBe("games");
    expect(shell.interactionMode).toBe("readonly");
    expect(shell.theme).toBe("brown");
    expect(shell.pieceSet).toBe("cburnett");
  });

  expect(view.container.querySelector(".board-frame")).toBeNull();
  view.unmount();
  expect(useBoardShellStore.getState().board.owner).toBe("train");
});
