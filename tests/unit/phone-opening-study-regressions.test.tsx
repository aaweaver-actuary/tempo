import { TrainingRepairNotice } from "../../app/components/TrainingRepairNotice";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { STANDARD_FEN } from "../../app/const";
import { asCardId, asFenString, asSanMove } from "../../app/types";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

afterEach(() => vi.unstubAllGlobals());

const phoneMediaListeners = new Set<() => void>();
let phoneViewport = true;
beforeEach(() => {
  phoneViewport = true;
  phoneMediaListeners.clear();
  vi.stubGlobal("matchMedia", () => ({ matches: phoneViewport,
    addEventListener: (_: string, listener: () => void) => phoneMediaListeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => phoneMediaListeners.delete(listener),
  }));
  useTrainingStore.setState({ cardsLeft: 2, step: 0, feedback: "ready", showHint: false,
    queueNotice: "", isAttemptFailed: false, failureAnnotation: undefined, failureFen: undefined,
    currentFenString: asFenString(STANDARD_FEN), opponentLastMove: undefined, lastMove: undefined,
    teachingEncounterKey: null, boardAttempt: 0,
    attempt: { entryKey: "phone-opening", generation: 0, phase: "playerTurn" },
  });
});

function trainingProps(title = "London System") {
  return { dateLabel: "10/4/2026", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 2,
    card: { id: asCardId(title), kind: "opening" as const, title, subtitle: "White.pgn",
      startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("d4")],
      userMoveTarget: 1, orientation: "white" as const, priorStudyReviewCount: 7 },
    boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
    handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), onMove: vi.fn(),
  };
}

it("Phone opening study shows repertoire identity above the board", () => {
  const view = render(<TrainingView {...trainingProps()} />);
  const boardColumn = view.container.querySelector(".board-column")!;
  expect(boardColumn.querySelector("h2")?.textContent).toBe("London System");
  expect(boardColumn.querySelector("h2")!.compareDocumentPosition(screen.getByTestId("board")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByRole("heading", { name: "London System" })).toHaveLength(1);
});

it("Phone study heading follows London-to-Ruy-Lopez card transitions", () => {
  const view = render(<TrainingView {...trainingProps()} offlineQueue />);
  view.rerender(<TrainingView {...trainingProps("Ruy Lopez")} offlineQueue />);
  expect(view.container.querySelector(".board-column h2")?.textContent).toBe("Ruy Lopez");
  expect(screen.queryByText("London System")).toBeNull();
});

it("Phone study details keep feedback and save retries outside the disclosure", () => {
  const view = render(<TrainingView {...trainingProps()} reviewPersistenceState="saveFailed" reviewSaveError="Save failed" />);
  const details = view.container.querySelector("details.study-details") as HTMLDetailsElement;
  expect(details).not.toBeNull();
  expect(details.open).toBe(false);
  expect(details.textContent).toContain("White.pgn");
  expect(details.textContent).toContain("Seen before 7 times");
  expect(details.contains(screen.getByRole("button", { name: "Retry save" }))).toBe(false);
  expect(details.contains(screen.getByRole("button", { name: "Correct" }))).toBe(false);
  expect(details.contains(view.container.querySelector(".feedback"))).toBe(false);
});

it("Phone study More retains restart and edit handlers and closes after selection", () => {
  const props = trainingProps();
  const view = render(<TrainingView {...props} />);
  const menu = view.container.querySelector(".phone-study-actions") as HTMLDetailsElement;
  expect(menu).not.toBeNull();
  menu.open = true;
  fireEvent.click(screen.getByRole("button", { name: /Restart/ }));
  expect(props.resetCardAttempt).toHaveBeenCalledOnce();
  expect(menu.open).toBe(false);
  expect(document.activeElement).toBe(menu.querySelector("summary"));
  menu.open = true;
  fireEvent.click(screen.getByRole("button", { name: /Edit card/ }));
  expect(props.setEditorCard).toHaveBeenCalledWith(props.card);
  expect(menu.open).toBe(false);
});

it("Phone opening study preserves the empty-state header without a stale repertoire", () => {
  const view = render(<TrainingView {...trainingProps()} cardsLeft={0} />);
  expect(screen.getByRole("heading", { name: "You're done for today" })).toBeTruthy();
  expect(view.container.querySelector(".phone-study-heading")).toBeNull();
});

it("Phone study actions retain pending-burial locks", () => {
  render(<TrainingView {...trainingProps()} burialPending />);
  expect(screen.getByRole("button", { name: /Show move/ }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByText("More", { exact: true }));
  for (const name of [/^Bury$/, /Restart/, /Edit card/])
    expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
});


it("Phone repair notice retains counts, explanation, and resume outside study details", () => {
  const onResume = vi.fn();
  const view = render(<TrainingView {...trainingProps()} repairNotice={<TrainingRepairNotice blockedDue={25} issueCount={33} onResume={onResume} />} />);
  expect(screen.getByText("25 opening cards paused.")).toBeTruthy();
  expect(screen.getByText("33 issues remaining.")).toBeTruthy();
  const resume = screen.getByRole("button", { name: "Resume repair" });
  expect(view.container.querySelector(".study-details")?.contains(resume)).toBe(false);
  fireEvent.click(resume);
  expect(onResume).toHaveBeenCalledOnce();
  const explanation = view.container.querySelector(".repair-explanation") as HTMLDetailsElement;
  expect(explanation.open).toBe(false);
  expect(explanation.textContent).toContain("Unaffected openings and tactics remain available.");
});

it("Phone opening study preserves the accessible training page heading", () => {
  const props = trainingProps();
  const view = render(<TrainingView {...props} />);
  const expectHeadings = (pageTitle: string, activeRepertoire = true) => {
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: pageTitle })).toBeTruthy();
    expect(screen.queryAllByRole("heading", { level: 2, name: "London System" }))
      .toHaveLength(activeRepertoire ? 1 : 0);
  };
  expectHeadings("Daily training");
  expect(view.container.querySelector(".training-header")).toBeNull();
  expect(view.container.querySelector(".session-count")).toBeNull();
  expect(screen.getByRole("heading", { level: 1 }).classList.contains("sr-only")).toBe(true);

  view.rerender(<TrainingView {...props} serviceError="Study service unavailable" />);
  expectHeadings("Local service unavailable");
  expect(screen.getByRole("alert").textContent).toContain("Study service unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(props.refreshDatabaseQueue).toHaveBeenCalledOnce();

  view.rerender(<TrainingView {...props} cardsLeft={0} />);
  expectHeadings("You're done for today", false);
  expect(view.container.querySelector(".phone-study-heading")).toBeNull();
  act(() => useTrainingStore.setState({ queueNotice: "Prepared exercises require the computer" }));
  expectHeadings("Prepared exercises unavailable offline", false);

  act(() => useTrainingStore.setState({ queueNotice: "" }));
  view.rerender(<TrainingView {...props} />);
  for (const isPhone of [true, false, true]) {
    act(() => {
      phoneViewport = isPhone;
      phoneMediaListeners.forEach(listener => listener());
    });
    expectHeadings("Daily training");
    expect(view.container.querySelector(".session-count") !== null).toBe(!isPhone);
  }
});
