import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import Home from "../../app/views/home_view";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { asCardId, asFenString, asRepertoireId, asSanMove } from "../../app/types";

vi.mock("../../app/views/fetchAndInitializeQueue", () => ({ fetchAndInitializeQueue: vi.fn() }));
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/analysis-engines", () => ({ analyzeWithStockfish: vi.fn(async () => []), analyzeWithMaia: vi.fn(async () => []) }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(), moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
const savedMoves = ["e4", "c5", "Nf3", "d5", "exd5", "Qxd5", "g3", "Nc6", "Bg2", "e5"];
const positions = [new Chess().fen()];
const position = new Chess();
const route = savedMoves.map(san => { const move = position.move(san); positions.push(position.fen()); return { san: move.san, uci: `${move.from}${move.to}`, fen: position.fen() }; });
const card = { id: asCardId("training-card"), backendId: asCardId("saved-card"), repertoireId: asRepertoireId("najdorf"), kind: "opening" as const,
  title: "Najdorf", subtitle: "", startingFen: asFenString(positions[0]), moves: savedMoves.map(asSanMove), userMoveTarget: 5, orientation: "white" as const, revision: 1 };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async input => Response.json(String(input).includes("/repertoire/lines") ? { lines: [{ id: "authored-line", repertoire_id: "najdorf", repertoire_name: "Najdorf", name: "Saved line", trained_color: "white", start_fen: positions[0], moves: route.map(move => move.uci) }] } : { providers: [], states: [], repertoires: [], discoveries: [], annotations: [] })));
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => {
    useTrainingStore.getState().hydrateLocalQueue([card], true);
    useTrainingStore.setState({ queueReadiness: "ready", step: 8, currentFenString: asFenString(positions[8]) });
  });
});

async function openTraining() {
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe("saved-card"));
  await waitFor(() => expect(useBoardShellStore.getState().board.fen).toBe(positions[8]));
}

it("training Builder handoff keeps the middle-line cursor and full saved continuation without changing the attempt", async () => {
  await openTraining();
  const attemptBefore = useTrainingStore.getState().attempt;
  fireEvent.click(within(screen.getByLabelText("Open review position")).getByRole("button", { name: "Builder" }));
  await waitFor(() => expect(JSON.parse(localStorage.getItem("tempo-builder-session")!).history).toHaveLength(10));
  const session = JSON.parse(localStorage.getItem("tempo-builder-session")!);
  expect(session).toMatchObject({ startingFen: positions[0], cursor: 8, branchStart: 8, activeRepertoireId: "najdorf", orientation: "white" });
  expect(useBoardShellStore.getState().board.fen).toBe(positions[8]);
  expect(useTrainingStore.getState().attempt).toEqual(attemptBefore);
  expect(useTrainingStore.getState().step).toBe(8);
});

it("training Analysis handoff uses the displayed historical position and retains the full route", async () => {
  await openTraining();
  act(() => useBoardShellStore.getState().board.keyboard?.previous?.());
  await waitFor(() => expect(useBoardShellStore.getState().board.fen).toBe(positions[7]));
  fireEvent.click(within(screen.getByLabelText("Open review position")).getByRole("button", { name: "Analysis" }));
  await waitFor(() => expect(useBoardShellStore.getState().board.owner).toBe("builder"));
  const session = JSON.parse(localStorage.getItem("tempo-builder-session")!);
  expect(session).toMatchObject({ startingFen: positions[0], cursor: 7, branchStart: null });
  expect(session.history).toHaveLength(10);
  expect(useBoardShellStore.getState().board.fen).toBe(positions[7]);
  expect(useTrainingStore.getState().step).toBe(8);
});

it("training Games here handoff filters the historical position without advancing the live attempt", async () => {
  await openTraining();
  act(() => useBoardShellStore.getState().board.keyboard?.previous?.());
  await waitFor(() => expect(useBoardShellStore.getState().board.fen).toBe(positions[7]));
  fireEvent.click(within(screen.getByLabelText("Open review position")).getByRole("button", { name: "Games here" }));
  await waitFor(() => expect(useBoardShellStore.getState().board.owner).toBe("games"));
  await waitFor(() => {
    const summaryRequest = vi.mocked(fetch).mock.calls.find(([input]) => String(input).includes("/games/summary?"));
    expect(summaryRequest).toBeDefined();
    expect(new URL(String(summaryRequest![0])).searchParams.get("fen")).toBe(positions[7].split(" ").slice(0, 4).join(" "));
  });
  expect(useTrainingStore.getState().step).toBe(8);
});

it("training comparison handoff preserves the historical cursor full continuation and live attempt", async () => {
  await openTraining();
  act(() => useTrainingStore.setState({ isAttemptFailed: true }));
  const attemptBefore = useTrainingStore.getState().attempt;
  act(() => useBoardShellStore.getState().board.keyboard?.previous?.());
  await waitFor(() => expect(useBoardShellStore.getState().board.fen).toBe(positions[7]));
  fireEvent.click(within(screen.getByLabelText("Open review position")).getByRole("button", { name: "Compare positions" }));
  await waitFor(() => expect(sessionStorage.getItem("tempo-comparison-session")).not.toBeNull());
  const source = JSON.parse(sessionStorage.getItem("tempo-comparison-session")!).boards[0];
  expect(source).toMatchObject({ startingFen: positions[0], cursor: 7, orientation: "white", cardId: "saved-card" });
  expect(source.history).toHaveLength(10);
  expect(source.history[source.cursor - 1].fen).toBe(positions[7]);
  expect(useTrainingStore.getState().step).toBe(8);
  expect(useTrainingStore.getState().attempt).toEqual(attemptBefore);
});
