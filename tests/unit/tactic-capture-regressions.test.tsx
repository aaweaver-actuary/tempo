import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { TacticCaptureDialog } from "../../app/components/tactic-capture-dialog";
import { EMPTY_SETUP_FEN, solutionUciMoves, usePositionSolutionEditor } from "../../app/hooks/use-position-solution-editor";
import { pendingTacticCapture, saveTacticCapture } from "../../app/lib/tactic-capture-command";
import { invalidateWorkspaceData } from "../../app/lib/workspace-data";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";

vi.mock("../../app/lib/workspace-data", () => ({ invalidateWorkspaceData: vi.fn() }));
vi.mock("../../app/components/chessboard", () => ({ Chessboard: (props: { fen: string; onMove: (from: string, to: string) => void; locked: boolean; orientation?: string }) =>
  <div data-testid="capture-board" data-fen={props.fen} data-orientation={props.orientation}><button disabled={props.locked} onClick={() => props.onMove("e2", "e4")}>Record e4</button></div> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const draft = { starting_fen: new Chess().fen(), moves: ["e2e4"], source_kind: "manual" as const, note: "" };
function success(body: string, reused = false) {
  const request = JSON.parse(body);
  return Response.json({ capture_id: request.capture_id, card_id: "captured", reused, queued: true, introduced: !reused });
}

it("capture setup starts empty, permits placement and free dragging, and keeps invalid FEN text off the board", () => {
  const { result } = renderHook(() => usePositionSolutionEditor(EMPTY_SETUP_FEN, [], true));
  expect(result.current.startingFen).toBe(EMPTY_SETUP_FEN);
  expect(result.current.positionError).toBeTruthy();
  act(() => result.current.setPiece("R"));
  act(() => result.current.placePiece("a1"));
  act(() => result.current.moveSetupPiece("a1", "a4"));
  expect(new Chess(result.current.boardFen, { skipValidation: true }).get("a4")?.type).toBe("r");
  const boardFen = result.current.boardFen;
  act(() => result.current.changeStartingFen("unfinished FEN"));
  expect(result.current.startingFen).toBe("unfinished FEN");
  expect(result.current.boardFen).toBe(boardFen);
});

it("capture records legal moves, replaces an earlier continuation, and confirms position changes", () => {
  const { result } = renderHook(() => usePositionSolutionEditor(new Chess().fen(), [], true));
  act(() => result.current.playSolution("e2", "e5"));
  expect(result.current.moves).toEqual([]);
  act(() => result.current.playSolution("e2", "e4"));
  act(() => result.current.playSolution("e7", "e5"));
  act(() => result.current.setCursor(1));
  act(() => result.current.playSolution("c7", "c5"));
  expect(solutionUciMoves(result.current.startingFen, result.current.moves)).toEqual(["e2e4", "c7c5"]);
  act(() => result.current.changeStartingFen(EMPTY_SETUP_FEN));
  expect(result.current.moves).toHaveLength(2);
  act(() => result.current.cancelStartingChange());
  expect(result.current.startingFen).toBe(new Chess().fen());
  act(() => result.current.changeStartingFen(EMPTY_SETUP_FEN));
  act(() => result.current.confirmStartingChange());
  expect(result.current.moves).toEqual([]);
  expect(result.current.startingFen).toBe(EMPTY_SETUP_FEN);
});

it("capture preserves castling and en passant and records an underpromotion", () => {
  const initial = "7k/P7/8/8/8/8/8/7K w - - 0 1";
  const { result } = renderHook(() => usePositionSolutionEditor(initial, [], true));
  act(() => result.current.setPromotion("n"));
  act(() => result.current.playSolution("a7", "a8"));
  expect(solutionUciMoves(initial, result.current.moves)).toEqual(["a7a8n"]);
  expect(solutionUciMoves(new Chess().fen(), ["e4"])).toEqual(["e2e4"]);
  const ep = "7k/8/8/3pP3/8/8/8/7K w - d6 0 1";
  act(() => result.current.setStartingFen(ep));
  expect(result.current.startingFen).toBe(ep);
});

it("capture prevents invalid saves and sends the minimal UCI request before invalidating the queue", async () => {
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => success(init.body as string));
  vi.stubGlobal("fetch", fetcher);
  const onClose = vi.fn(), changed = vi.fn();
  render(<TacticCaptureDialog theme="brown" pieceSet="cburnett" onClose={onClose} onQueueChanged={changed} />);
  expect((screen.getByRole("button", { name: "Add to training" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Solution" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Standard position" }));
  fireEvent.click(screen.getByRole("button", { name: "Solution" }));
  fireEvent.click(screen.getByRole("button", { name: "Record e4" }));
  fireEvent.change(screen.getByLabelText("Source"), { target: { value: "puzzle_rush" } });
  fireEvent.change(screen.getByLabelText("Reference (optional)"), { target: { value: "synthetic-encounter" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to training" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(changed).toHaveBeenCalledOnce();
  expect(invalidateWorkspaceData).toHaveBeenCalled();
  const request = JSON.parse(fetcher.mock.calls[0][1].body as string);
  expect(request).toMatchObject({ starting_fen: new Chess().fen(), moves: ["e2e4"], source_kind: "puzzle_rush", source_ref: "synthetic-encounter", note: "" });
  expect(fetcher.mock.calls[0][1].headers).toMatchObject({ "Idempotency-Key": request.capture_id });
  expect(pendingTacticCapture()).toBeNull();
});

it("capture transport retry and reopened dialog retain exact identity and bytes and lock edits", async () => {
  let body = "";
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => { body = init.body as string; throw new Error("lost transport"); }));
  await expect(saveTacticCapture(draft)).rejects.toThrow("lost transport");
  const id = pendingTacticCapture()!.capture_id;
  render(<TacticCaptureDialog theme="brown" pieceSet="cburnett" onClose={vi.fn()} onQueueChanged={vi.fn()} />);
  expect((screen.getByLabelText("FEN") as HTMLTextAreaElement).disabled).toBe(true);
  expect((screen.getByLabelText("FEN") as HTMLTextAreaElement).value).toBe(draft.starting_fen);
  const replay = vi.fn(async (url: string, init?: RequestInit) => url.includes("/operations/")
    ? Response.json({ state: "pending" }) : success(init!.body as string));
  vi.stubGlobal("fetch", replay);
  await saveTacticCapture({ ...draft, note: "ignored edits" });
  expect(replay.mock.calls[1][1]?.body).toBe(body);
  expect(JSON.parse(body).capture_id).toBe(id);
});

it("capture pending receipts cannot unlock edits, and a terminal failure permits a new identity", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ operation_id: "unused" }, { status: 503 })));
  await expect(saveTacticCapture(draft)).rejects.toThrow();
  const originalId = pendingTacticCapture()!.capture_id;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "failed", error: { message: "illegal line" } })));
  await expect(saveTacticCapture()).rejects.toThrow("illegal line");
  expect(pendingTacticCapture()).toBeNull();
  let freshBody = "";
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => { freshBody = init.body as string; return success(freshBody); }));
  await saveTacticCapture({ ...draft, note: "edited" });
  expect(JSON.parse(freshBody).capture_id).not.toBe(originalId);
});

it("capture validates confirmed response identity and resolves a completed receipt without another POST", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("lost"); }));
  await expect(saveTacticCapture(draft)).rejects.toThrow();
  const pending = pendingTacticCapture()!;
  const fetcher = vi.fn(async () => Response.json({ state: "complete", response: {
    capture_id: pending.capture_id, card_id: "card", queued: true, reused: false, introduced: true,
  } }));
  vi.stubGlobal("fetch", fetcher);
  expect((await saveTacticCapture()).card_id).toBe("card");
  expect(fetcher).toHaveBeenCalledOnce();
});

it("tactic provenance retains packaged links and never invents a Lichess URL for game or manual cards", () => {
  const card = { id: "card", queue_entry_id: 1, start_fen: new Chess().fen(), moves: ["e2e4"], content_type: "tactic",
    repertoire_name: "Tactics", repertoire_source: "Synthetic", source_ref: "external-id" };
  expect(mapQueueCardToPracticeCard({ ...card, repertoire_id: "__tactics__" }).sourceUrl).toBe("https://lichess.org/training/external-id");
  for (const owner of ["__game_tactics__", "__captured_tactics__"]) {
    const mapped = mapQueueCardToPracticeCard({ ...card, repertoire_id: owner });
    expect(mapped.sourceUrl).toBeUndefined();
    expect(mapped.subtitle).not.toContain("Lichess");
  }
});


it("capture retries an unknown or unavailable receipt with the saved body and retains identity through 202", async () => {
  let originalBody = "";
  let postCount = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Response.json({ state: postCount === 1 ? "unknown" : "pending" });
    postCount += 1;
    if (!originalBody) originalBody = init!.body as string;
    expect(init!.body).toBe(originalBody);
    const request = JSON.parse(originalBody);
    return Response.json({ operation_id: request.capture_id }, { status: 202 });
  }));
  await expect(saveTacticCapture(draft)).rejects.toThrow("still pending");
  await expect(saveTacticCapture()).rejects.toThrow("still pending");
  expect(postCount).toBe(2);
  const retry = vi.fn(async (url: string, init?: RequestInit) => url.includes("/operations/")
    ? Response.json({ detail: "SQLite has no operation receipts" }, { status: 503 }) : success(init!.body as string));
  vi.stubGlobal("fetch", retry);
  await saveTacticCapture();
  expect(retry.mock.calls[1][1]?.body).toBe(originalBody);
});

function openCaptureSolution(fen = new Chess().fen()) {
  render(<TacticCaptureDialog theme="brown" pieceSet="cburnett" onClose={vi.fn()} onQueueChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("FEN"), { target: { value: fen } });
  fireEvent.click(screen.getByRole("button", { name: "Solution" }));
}
function enterSan(text: string, enter = false) {
  fireEvent.change(screen.getByLabelText("SAN moves"), { target: { value: text } });
  if (enter) fireEvent.keyDown(screen.getByLabelText("SAN moves"), { key: "Enter" });
  else fireEvent.click(screen.getByRole("button", { name: "Add moves" }));
}

it("capture orients to the accepted starting side and stays fixed through moves and pending FEN changes", () => {
  const blackFen = new Chess().fen().replace(" w ", " b ");
  openCaptureSolution(blackFen);
  expect(screen.getByTestId("capture-board").getAttribute("data-orientation")).toBe("black");
  enterSan("e5", true);
  expect(screen.getByTestId("capture-board").getAttribute("data-orientation")).toBe("black");
  fireEvent.change(screen.getByLabelText("FEN"), { target: { value: new Chess().fen() } });
  expect(screen.getByTestId("capture-board").getAttribute("data-orientation")).toBe("black");
  fireEvent.click(screen.getByRole("button", { name: "Keep position" }));
  fireEvent.change(screen.getByLabelText("FEN"), { target: { value: new Chess().fen() } });
  fireEvent.click(screen.getByRole("button", { name: "Change position and clear solution" }));
  expect(screen.getByTestId("capture-board").getAttribute("data-orientation")).toBe("white");
  fireEvent.change(screen.getByLabelText("FEN"), { target: { value: "unfinished FEN b" } });
  expect(screen.getByTestId("capture-board").getAttribute("data-orientation")).toBe("white");
});

it("capture accepts numbered Black-first SAN lines and saves canonical UCI moves", async () => {
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => success(init.body as string));
  vi.stubGlobal("fetch", fetcher);
  openCaptureSolution(new Chess().fen().replace(" w ", " b ").replace("0 1", "0 12"));
  enterSan("12...e5 13.Nf3 Nc6", true);
  expect((screen.getByLabelText("SAN moves") as HTMLInputElement).value).toBe("");
  expect(screen.getByTestId("capture-board").getAttribute("data-fen")).toBe(
    "r1bqkbnr/pppp1ppp/2n5/4p3/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 14");
  fireEvent.click(screen.getByRole("button", { name: "Add to training" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(JSON.parse(fetcher.mock.calls[0][1].body as string).moves).toEqual(["e7e5", "g1f3", "b8c6"]);
});

it("capture rejects an invalid SAN line atomically and retains text for correction", () => {
  openCaptureSolution();
  enterSan("e4 e5 invalid");
  expect(screen.getByRole("alert").textContent).toContain("invalid");
  expect(screen.getByTestId("capture-board").getAttribute("data-fen")).toBe(new Chess().fen());
  expect((screen.getByLabelText("SAN moves") as HTMLInputElement).value).toBe("e4 e5 invalid");
  expect((screen.getByRole("button", { name: "Add to training" }) as HTMLButtonElement).disabled).toBe(true);
  enterSan("1. e4 e5");
  expect(screen.queryByRole("alert")).toBeNull();
  expect((screen.getByRole("button", { name: "Add to training" }) as HTMLButtonElement).disabled).toBe(false);
});

it("capture SAN entry replaces the continuation at the cursor and mixes with board moves", () => {
  openCaptureSolution();
  fireEvent.click(screen.getByRole("button", { name: "Record e4" }));
  enterSan("e5 Nf3");
  fireEvent.click(screen.getByRole("button", { name: "1.e4" }));
  enterSan("c5 Nc3");
  const board = new Chess();
  for (const san of ["e4", "c5", "Nc3"]) board.move(san);
  expect(screen.getByTestId("capture-board").getAttribute("data-fen")).toBe(board.fen());
  expect(screen.queryByRole("button", { name: "e5" })).toBeNull();
});

it("capture SAN entry stays locked while saving and awaiting capture confirmation", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("lost"); }));
  openCaptureSolution();
  enterSan("e4");
  fireEvent.click(screen.getByRole("button", { name: "Add to training" }));
  expect((screen.getByLabelText("SAN moves") as HTMLInputElement).disabled).toBe(true);
  await waitFor(() => expect(screen.getByText(/A capture is awaiting confirmation/)).toBeTruthy());
  expect((screen.getByLabelText("SAN moves") as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Add moves" }) as HTMLButtonElement).disabled).toBe(true);
});

it.each([
  ["r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "O-O O-O-O", ["e1g1", "e8c8"]],
  ["7k/8/8/3pP3/8/8/8/7K w - d6 0 1", "exd6", ["e5d6"]],
  ["7k/P7/8/8/8/8/8/7K w - - 0 1", "a8=N", ["a7a8n"]],
  ["7k/8/8/8/8/8/8/1N3N1K w - - 0 1", "Nbd2", ["b1d2"]],
])("capture SAN entry preserves special moves and disambiguation from %s", (fen, text, expected) => {
  const { result } = renderHook(() => usePositionSolutionEditor(fen, [], true));
  act(() => { result.current.playSanSolution(text); });
  expect(solutionUciMoves(fen, result.current.moves)).toEqual(expected);
});


it("capture rejects empty, ambiguous, and non-SAN input without changing an existing solution", () => {
  const { result } = renderHook(() => usePositionSolutionEditor(new Chess().fen(), [], true));
  act(() => { result.current.playSanSolution("e4 e5 Nf3"); });
  act(() => result.current.setCursor(1));
  const originalMoves = result.current.moves;
  const originalPreview = result.current.previewFen;
  for (const text of ["", "  ", "12...", "e7e5", "e5 {comment}", "e5 1-0", "e5 (Nc3)"]) {
    act(() => { expect(result.current.playSanSolution(text)).toBe(false); });
    expect(result.current.moves).toEqual(originalMoves);
    expect(result.current.cursor).toBe(1);
    expect(result.current.previewFen).toBe(originalPreview);
  }
  act(() => result.current.setStartingFen("7k/8/8/8/8/8/8/1N3N1K w - - 0 1"));
  act(() => { expect(result.current.playSanSolution("Nd2")).toBe(false); });
  expect(result.current.moves).toEqual(originalMoves);
});

it("capture SAN entry refuses invalid or unconfirmed starting positions", () => {
  const { result } = renderHook(() => usePositionSolutionEditor(EMPTY_SETUP_FEN, [], true));
  act(() => { expect(result.current.playSanSolution("e4")).toBe(false); });
  expect(result.current.moves).toEqual([]);
  act(() => result.current.changeStartingFen(new Chess().fen()));
  act(() => { result.current.playSanSolution("e4"); });
  act(() => result.current.changeStartingFen(EMPTY_SETUP_FEN));
  act(() => { expect(result.current.playSanSolution("e5")).toBe(false); });
  expect(result.current.moves).toEqual(["e4"]);
  expect(result.current.cursor).toBe(1);
});
