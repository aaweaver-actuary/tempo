import { render, waitFor } from "@testing-library/react";
import { expect, it, vi, beforeEach } from "vitest";
import GamesView from "../../app/views/games_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";

vi.mock("../../app/utils/local", () => ({
  usesLocalApi: () => false,
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

it("Games shared board publishes readonly state and releases ownership on unmount", async () => {
  const view = render(
    <GamesView
      onAnalyze={vi.fn()}
      onSettings={vi.fn()}
      onSync={vi.fn()}
      syncState={{ syncing: false, error: "", lastSuccess: "" }}
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
