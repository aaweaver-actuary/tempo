import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import BuilderView from "../../app/views/analysis_view";
import { Settings } from "../../app/utils/settings";
import { trainingBuilderSession } from "../../app/lib/training-builder-route";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { asCardId, asFenString, asRepertoireId, asSanMove } from "../../app/types";
import { canonicalFenKey } from "../../app/utils/canonical-line";
import * as study from "../../app/lib/background-study";
vi.mock("../../app/lib/analysis-engines", () => ({ analyzeWithStockfish: vi.fn(async () => []), analyzeWithMaia: vi.fn(async () => []) }));
const moves = ["e4", "c5", "Nf3", "d5", "exd5", "Qxd5", "g3", "Nc6", "Bg2", "e5"];
const positions = [new Chess().fen()];
const board = new Chess();
const route = moves.map(san => { const move = board.move(san); positions.push(board.fen()); return { san: move.san, uci: `${move.from}${move.to}`, fen: board.fen() }; });
const authored = { id: "authored", repertoire_id: "najdorf", repertoire_name: "Najdorf", name: "Saved line", trained_color: "black", start_fen: positions[0], moves: route.map(move => move.uci) };
const props = { imported: [], settings: new Settings(), theme: "brown" as const, pieceSet: "cburnett" as const, useSharedBoard: true };
function pendingCard(startPly = 8, cursor = 0) {
  const card = { id: asCardId("card"), kind: "opening" as const, title: "Najdorf", subtitle: "", repertoireId: asRepertoireId("najdorf"), orientation: "black" as const,
    startingFen: asFenString(positions[startPly]), moves: moves.slice(startPly).map(asSanMove), userMoveTarget: 1 };
  const session = trainingBuilderSession(card, { fen: positions[startPly + cursor], cursor }, true);
  localStorage.setItem("tempo-builder-session", JSON.stringify(session));
  sessionStorage.setItem("tempo-builder-tools", "Repertoire");
}
function storedSession() { return JSON.parse(localStorage.getItem("tempo-builder-session")!); }
function mockLines(lines = [authored]) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/repertoire/lines")) return Response.json({ lines });
    if (String(input).includes("/repertoire/branches") && init?.method === "POST") return Response.json({ id: "new-branch", duplicate: false, moves: JSON.parse(String(init.body)).moves });
    return Response.json({ annotations: [] });
  });
  vi.stubGlobal("fetch", fetcher); return fetcher;
}
beforeEach(() => { localStorage.setItem("tempo-stockfish-on", "false"); localStorage.setItem("tempo-maia-on", "false"); mockLines(); });

it("Builder restores a partial training route once and persists its original root cursor and keyboard anchor across remount", async () => {
  pendingCard();
  const mounted = render(<BuilderView {...props} />);
  await waitFor(() => expect(storedSession().trainingRouteToResolve).toBeUndefined());
  expect(storedSession()).toMatchObject({ startingFen: positions[0], cursor: 8, branchStart: 8 });
  expect(storedSession().history).toHaveLength(10);
  expect(useBoardShellStore.getState().board.fen).toBe(positions[8]);
  fireEvent.click(screen.getByRole("button", { name: /Forward/ }));
  expect(useBoardShellStore.getState().board.fen).toBe(positions[9]);
  act(() => useBoardShellStore.getState().board.keyboard?.reset?.());
  expect(useBoardShellStore.getState().board.fen).toBe(positions[8]);
  mounted.unmount(); render(<BuilderView {...props} />);
  expect(storedSession().history).toHaveLength(10);
  expect(useBoardShellStore.getState().board.fen).toBe(positions[8]);
  expect(screen.queryByText("Restore training line")).toBeNull();
});

it("Builder stepping back from the training launch saves Bg4 on the original route without replacing the saved Nc6 line", async () => {
  const fetcher = mockLines(); pendingCard(); render(<BuilderView {...props} />);
  await waitFor(() => expect(storedSession().cursor).toBe(8));
  expect(screen.getByRole("button", { name: "Save branch" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /Back/ }));
  await act(async () => useBoardShellStore.getState().board.onMove?.("c8", "g4"));
  expect(storedSession().branchStart).toBe(7);
  fireEvent.click(screen.getByRole("button", { name: "Save branch" }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => String(url).includes("/repertoire/branches") && init?.method === "POST")).toBe(true));
  const [, request] = fetcher.mock.calls.find(([url, init]) => String(url).includes("/repertoire/branches") && init?.method === "POST")!;
  expect(JSON.parse(String(request?.body))).toMatchObject({ starting_fen: positions[0], repertoire_id: "najdorf", moves: [...route.slice(0, 7).map(move => move.uci), "c8g4"] });
  await screen.findByText("Saved");
  expect(localStorage.getItem("tempo-pending-repertoire-branch-v1")).toBeNull();
  expect(authored.moves[7]).toBe("b8c6");
});

it("ambiguous partial training routes require an explicit choice and keep the displayed board fixed", async () => {
  const transposedMoves = [["Nf3", "Nf6", "g3", "g6", "Bg2", "Bg7"], ["g3", "g6", "Nf3", "Nf6", "Bg2", "Bg7"]];
  const lines = transposedMoves.map((sanMoves, index) => ({ ...authored, id: `line-${index}`, name: `Route ${index}`, moves: (() => { const position = new Chess(); return sanMoves.map(san => { const move = position.move(san); return `${move.from}${move.to}`; }); })() }));
  mockLines(lines);
  const position = new Chess(); for (const san of transposedMoves[0].slice(0, 4)) position.move(san);
  const card = { id: asCardId("card"), kind: "opening" as const, title: "Transposed", subtitle: "", repertoireId: asRepertoireId("najdorf"), startingFen: asFenString(position.fen()), moves: ["Bg2", "Bg7"].map(asSanMove), orientation: "black" as const, userMoveTarget: 1 };
  localStorage.setItem("tempo-builder-session", JSON.stringify(trainingBuilderSession(card, { fen: position.fen(), cursor: 0 }, true)));
  render(<BuilderView {...props} />);
  await screen.findByText(/Several earlier move orders/);
  expect(canonicalFenKey(useBoardShellStore.getState().board.fen)).toBe(canonicalFenKey(position.fen()));
  expect(screen.getByRole("button", { name: "Save branch" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /g3 · g6 · Nf3 · Nf6/ }));
  await waitFor(() => expect(storedSession().trainingRouteToResolve).toBeUndefined());
  expect(storedSession().history.slice(0, 4).map((move: { san: string }) => move.san)).toEqual(transposedMoves[1].slice(0, 4));
  expect(canonicalFenKey(useBoardShellStore.getState().board.fen)).toBe(canonicalFenKey(position.fen()));
});

it("failed route loading shows a retry action and never reports a false missing response or enables branch writes", async () => {
  pendingCard();
  const fetcher = mockLines(); fetcher.mockImplementation(async input => String(input).includes("/repertoire/lines") ? new Response("Unavailable", { status: 503 }) : Response.json({ annotations: [] }));
  render(<BuilderView {...props} />);
  await screen.findByRole("button", { name: "Retry route lookup" });
  expect(screen.queryByText("No saved response at this position.")).toBeNull();
  expect(screen.getByRole("button", { name: "Save branch" }).hasAttribute("disabled")).toBe(true);
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  fetcher.mockImplementation(async input => Response.json(String(input).includes("/repertoire/lines") ? { lines: [authored] } : { annotations: [] }));
  fireEvent.click(screen.getByRole("button", { name: "Retry route lookup" }));
  await waitFor(() => expect(storedSession().trainingRouteToResolve).toBeUndefined());
  expect(storedSession().cursor).toBe(8);
});

it("unmatched training continuation remains available for analysis while original-route writes stay blocked", async () => {
  pendingCard(); mockLines([{ ...authored, moves: route.slice(0, 8).map(move => move.uci) }]);
  render(<BuilderView {...props} />);
  await screen.findByText(/No original repertoire route matches/);
  expect(storedSession().trainingRouteToResolve).toBeDefined();
  expect(storedSession().history).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Delete line from here" }).hasAttribute("disabled")).toBe(true);
});

it("switching repertoires during unresolved training recovery preserves the card board route and draft anchor", async () => {
  pendingCard();
  const otherRepertoireLine = { ...authored, id: "other-line", repertoire_id: "other-repertoire", repertoire_name: "Other repertoire" };
  const fetcher = mockLines([{ ...authored, moves: route.slice(0, 8).map(move => move.uci) }, otherRepertoireLine]);
  render(<BuilderView {...props} />);
  await screen.findByText(/No original repertoire route matches/);
  fireEvent.change(screen.getByLabelText("Active repertoire"), { target: { value: "other-repertoire" } });
  await screen.findByText(/Select the training card's repertoire/);
  expect(useBoardShellStore.getState().board.fen).toBe(positions[8]);
  expect(storedSession()).toMatchObject({ startingFen: positions[8], cursor: 0, branchStart: 0 });
  expect(storedSession().history).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Save branch" }).hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("Active repertoire"), { target: { value: "najdorf" } });
  await screen.findByText(/No original repertoire route matches/);
  fetcher.mockImplementation(async input => Response.json(String(input).includes("/repertoire/lines") ? { lines: [authored, otherRepertoireLine] } : { annotations: [] }));
  fireEvent.click(screen.getByRole("button", { name: "Retry route lookup" }));
  await waitFor(() => expect(storedSession().trainingRouteToResolve).toBeUndefined());
  expect(storedSession()).toMatchObject({ startingFen: positions[0], cursor: 8, branchStart: 8 });
  expect(storedSession().history).toHaveLength(10);
  expect(useBoardShellStore.getState().board.fen).toBe(positions[8]);
});

it("late training route resolution cannot overwrite a replacement Builder session", async () => {
  pendingCard();
  const realRun = study.runStudyTask;
  let resolveOld!: (value: unknown) => void;
  const run = vi.spyOn(study, "runStudyTask").mockImplementation((task, signal) => task.kind === "resolveTrainingRoute" ? new Promise(resolve => { resolveOld = resolve; }) : realRun(task, signal));
  const mounted = render(<BuilderView {...props} />);
  await waitFor(() => expect(resolveOld).toBeDefined());
  mounted.unmount();
  localStorage.setItem("tempo-builder-session", JSON.stringify({ version: 1, activeRepertoireByColor: {}, orientation: "white", startingFen: positions[0], history: [], cursor: 0, branchStart: null }));
  render(<BuilderView {...props} />);
  await act(async () => resolveOld([{ startingFen: positions[0], prefix: route.slice(0, 8), title: "Old route" }]));
  expect(storedSession().cursor).toBe(0);
  expect(useBoardShellStore.getState().board.fen).toBe(positions[0]);
  run.mockRestore();
});

it("training route worker failure preserves the pending session and retries without inventing a route", async () => {
  pendingCard();
  const realRun = study.runStudyTask;
  let failLookup = true;
  vi.spyOn(study, "runStudyTask").mockImplementation((task, signal) => task.kind === "resolveTrainingRoute" && failLookup ? Promise.reject(new Error("Worker unavailable. Retry while connected.")) : realRun(task, signal));
  render(<BuilderView {...props} />);
  await screen.findByText(/Worker unavailable/);
  expect(storedSession().startingFen).toBe(positions[8]);
  expect(storedSession().trainingRouteToResolve).toBeDefined();
  failLookup = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry route lookup" }));
  await waitFor(() => expect(storedSession().cursor).toBe(8));
  expect(storedSession().trainingRouteToResolve).toBeUndefined();
});

it("unplayed saved continuation cannot submit a training Builder branch at its launch cursor", async () => {
  const fetcher = mockLines(); pendingCard(); render(<BuilderView {...props} />);
  await waitFor(() => expect(storedSession().trainingRouteToResolve).toBeUndefined());
  expect(storedSession().history.length).toBeGreaterThan(storedSession().cursor);
  fireEvent.click(screen.getByRole("button", { name: "Save branch" }));
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/repertoire/branches"))).toEqual([]);
  expect(storedSession().branchStart).toBe(8);
});
