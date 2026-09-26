import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { STANDARD_FEN } from "../../app/const";
import { asCardId, asFenString, asSanMove } from "../../app/types";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

const prefixCard = {
  id: asCardId("queue-prefix"),
  backendId: asCardId("saved-prefix"),
  kind: "opening" as const,
  title: "Opening",
  subtitle: "",
  startingFen: asFenString(STANDARD_FEN),
  moves: ["e4", "e5", "Nf3", "Nc6", "Bb5"].map(asSanMove),
  userMoveTarget: 3,
  revision: 3,
  suggestShorterPrefix: true,
  prefixSplitLatestFailureId: 7,
};

beforeEach(() => {
  useTrainingStore.setState({
    cardsLeft: 2,
    step: 0,
    feedback: "wrong",
    isAttemptFailed: true,
    failureFen: asFenString(STANDARD_FEN),
    currentFenString: asFenString(STANDARD_FEN),
    queueNotice: "",
    attempt: { entryKey: "prefix", generation: 1, phase: "guided" },
  });
});

function showPrefixOffer(overrides: Record<string, unknown> = {}) {
  const onAcceptPrefixSplit = vi.fn(async (): Promise<void> => undefined);
  const onRejectPrefixSplit = vi.fn(async (): Promise<void> => undefined);
  const view = render(
    <TrainingView
      dateLabel="Today"
      serviceError=""
      refreshDatabaseQueue={vi.fn()}
      cardsLeft={2}
      card={{ ...prefixCard, ...overrides }}
      boardTheme="brown"
      pieceSet="cburnett"
      rateCard={vi.fn(async () => undefined)}
      handleAttemptFailure={vi.fn()}
      resetCardAttempt={vi.fn()}
      setEditorCard={vi.fn()}
      onAcceptPrefixSplit={onAcceptPrefixSplit}
      onRejectPrefixSplit={onRejectPrefixSplit}
      onMove={vi.fn()}
    />,
  );
  return { ...view, onAcceptPrefixSplit, onRejectPrefixSplit };
}

it("prefix split offer accepts immediately without opening a preview editor", async () => {
  let finishSave!: () => void;
  const pendingSave = new Promise<void>((resolve) => { finishSave = resolve; });
  const view = showPrefixOffer();
  view.onAcceptPrefixSplit.mockReturnValueOnce(pendingSave);
  expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  expect(view.onAcceptPrefixSplit).toHaveBeenCalledWith(prefixCard);
  expect((screen.getByRole("button", { name: "Saving…" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("dialog")).toBeNull();
  await act(async () => finishSave());
});

it("rejected prefix offer stays hidden until a later failure is loaded", async () => {
  const view = showPrefixOffer();
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Accept" })).toBeNull());
  view.rerender(
    <TrainingView
      dateLabel="Today" serviceError="" refreshDatabaseQueue={vi.fn()} cardsLeft={2}
      card={{ ...prefixCard, prefixSplitLatestFailureId: 8 }}
      boardTheme="brown" pieceSet="cburnett" rateCard={vi.fn(async () => undefined)}
      handleAttemptFailure={vi.fn()} resetCardAttempt={vi.fn()} setEditorCard={vi.fn()}
      onAcceptPrefixSplit={view.onAcceptPrefixSplit} onRejectPrefixSplit={view.onRejectPrefixSplit}
      onMove={vi.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
});

it("failed prefix split save keeps the offer and shows a retryable error", async () => {
  const view = showPrefixOffer();
  view.onAcceptPrefixSplit.mockRejectedValueOnce(new Error("The card changed"));
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("The card changed"));
  expect((screen.getByRole("button", { name: "Accept" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByTestId("board")).toBeTruthy();
});
