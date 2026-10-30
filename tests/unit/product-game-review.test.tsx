import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Chess, DEFAULT_POSITION } from "chess.js";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { BoardProps } from "@tempo/board-ui";
import { GameReviewView } from "../../app/features/game-review/GameReviewView";
import { createGameReviewApi, type GameReviewClient } from "../../app/features/game-review/api";
import { boardPosition, findingLabels, gamePositions, receiptLabel } from "../../app/features/game-review/state";
import { FindingSchema, GameDetailSchema, LearningReceiptSchema, EncounterReceiptSchema, type Finding, type GameDetail, type LearningReceipt, type EncounterReceipt } from "../../app/features/product-api/contracts";
import confirmedFixture from "../../contracts/product-v02/fixtures/confirmed-introduced-miss.json";
import heldFixture from "../../contracts/product-v02/fixtures/held-receipt.json";
import pausedEncounterFixture from "../../contracts/product-v02/fixtures/encounters/paused-introduction.json";
import { useTrainingStore } from "../../app/state/training-store";

const { boards } = vi.hoisted(() => ({ boards: [] as BoardProps[] }));
// Capture supplied state while mounting the real public renderer and its cleanup.
vi.mock("@tempo/board-ui", async importOriginal => {
  const actual = await importOriginal<typeof import("@tempo/board-ui")>();
  return { ...actual, Board: (props: BoardProps) => { boards.push(props); return <actual.Board {...props} />; } };
});

const moves = ["d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c1g5", "f8e7"];
const baseFinding = FindingSchema.parse(confirmedFixture.payload);
const firstSummary = { provider: "lichess" as const, game_id: baseFinding.game_id, source_revision: 1, played_at: "2026-10-25T00:00:00Z", time_precision: "day" as const, player_color: "white" as const };
const secondSummary = { ...firstSummary, provider: "chesscom" as const, game_id: "second-game", player_color: "black" as const, played_at: null, time_precision: "unknown" as const };
const idleSync = [{ provider: "lichess" as const, status: "idle" as const, operation_id: null, error: null }, { provider: "chesscom" as const, status: "idle" as const, operation_id: null, error: null }];

function finding(kind: Finding["kind"], ply = 1): Finding {
  return FindingSchema.parse({ ...baseFinding, kind, ply, played_uci: moves[ply - 1],
    decision_id: `decision-${ply}`, presentation_id: `card-${ply}`,
    lapse_eligible: kind === "confirmed_miss", introduced_before_game: kind === "confirmed_miss" ? true : null,
    diagnostic: kind === "confirmed_miss" ? null : `Evidence for ${kind}`,
    membership: kind === "confirmed_miss" ? "present" : kind === "conflict" ? "conflict" : "unknown" });
}
function receipt(status: LearningReceipt["status"] = "pending", target = finding("confirmed_miss")): LearningReceipt {
  return LearningReceiptSchema.parse({ ...heldFixture.payload, source_revision: target.source_revision, status, outcome: "accepted", reason: null,
    key: { ...heldFixture.payload.key, provider: target.provider, game_id: target.game_id, decision_id: target.decision_id, occurrence_ply: target.ply } });
}
function encounterReceipt(overrides: Partial<EncounterReceipt> = {}, target = finding("confirmed_miss")): EncounterReceipt {
  return EncounterReceiptSchema.parse({ ...pausedEncounterFixture.payload, key: receipt("pending", target).key,
    source_revision: target.source_revision, source_digest: target.source_digest, ...overrides });
}
function detail(overrides: Partial<GameDetail> = {}): GameDetail {
  return GameDetailSchema.parse({ ...firstSummary, starting_fen: DEFAULT_POSITION, moves, findings: [finding("confirmed_miss")], receipts: [], ...overrides });
}
function refreshStatus(provider: "lichess" | "chesscom", operation_id: string) {
  return { provider, operation_id, job_id: `job:${operation_id}`, window: { start_at: "2026-10-25T00:00:00Z", end_at: "2026-10-30T12:00:00Z", precision: "millisecond" as const }, status: "queued" as const, pages_committed: 0, next_attempt_at: 0, error: null };
}
function fakeApi(overrides: Partial<GameReviewClient> = {}): GameReviewClient {
  return {
    recentGames: vi.fn(async () => ({ games: [firstSummary, secondSummary], sync: idleSync })),
    gameDetail: vi.fn(async () => detail()),
    sync: vi.fn<GameReviewClient["sync"]>(async ({ provider, operation_id }) => ({ provider, operation_id, status: "pending", error: null })),
    refresh: vi.fn<GameReviewClient["refresh"]>(async ({ provider, operation_id }) => refreshStatus(provider, operation_id)),
    refreshStatus: vi.fn<GameReviewClient["refreshStatus"]>(async ({ operation_id }) => refreshStatus("lichess", operation_id)),
    retryFeedback: vi.fn(async () => receipt()), ...overrides,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function selectFirst() {
  fireEvent.click(await screen.findByRole("button", { name: /Lichess · synthetic-game/ }));
}
async function readyBoard() { await screen.findByRole("group", { name: /Original game board/ }); }
beforeEach(() => { boards.length = 0; });

it("renders every finding distinctly and labels uncertain source time without calling a gap forgetting", async () => {
  const findings = Object.keys(findingLabels).map((kind, index) => finding(kind as Finding["kind"], index + 1));
  render(<GameReviewView api={fakeApi({ gameDetail: vi.fn(async () => detail({ findings })) })} />);
  await selectFirst(); await readyBoard();
  for (const label of Object.values(findingLabels)) expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getAllByText(/day precision; time unknown/)).toHaveLength(2);
  expect(screen.getByText("Date and time unknown")).toBeTruthy();
  expect(screen.getByText("Played as black")).toBeTruthy();
  const gap = screen.getByText("Uncovered opponent reply").closest("li")!;
  expect(gap.textContent).not.toMatch(/recall miss|forgetting/);
  expect(within(gap).getByText("Played")).toBeTruthy();
  expect(within(gap).getByText("Expected")).toBeTruthy();
  expect(within(gap).getByText("Repertoire")).toBeTruthy();
});

it("supplies exact original FEN/history and marks the finding ply while stepping with the real board", async () => {
  render(<GameReviewView api={fakeApi({ gameDetail: vi.fn(async () => detail({ findings: [finding("opponent_gap", 4)] })) })} />);
  await selectFirst(); await readyBoard();
  const expected = new Chess();
  for (const move of moves.slice(0, 3)) expected.move(move);
  expect(boards.at(-1)?.fen).toBe(expected.fen());
  expect(boards.at(-1)?.positionRevision).toBe(3);
  expect(document.querySelector("cg-board")).toBeTruthy();
  expect(screen.getByText("Uncovered opponent reply").closest("li")?.dataset.current).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Next" })); expected.move(moves[3]);
  expect(boards.at(-1)?.fen).toBe(expected.fen());
  expect(boards.at(-1)?.lastMove).toEqual(["e7", "e6"]);
  fireEvent.click(screen.getByRole("button", { name: "Last" }));
  for (const move of moves.slice(4)) expected.move(move);
  expect(boards.at(-1)?.fen).toBe(expected.fen());
  expect(boards.at(-1)?.readOnly).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Show position before ply 4" }));
  expect(boards.at(-1)?.positionRevision).toBe(3);
});

it("preserves custom starting FEN, castling history and refuses truncated replay", () => {
  const startingFen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 7 12";
  const history = ["e1g1", "e8c8"];
  const expected = new Chess(startingFen); for (const move of history) expected.move(move);
  const positions = gamePositions(startingFen, history);
  expect(positions[0]).toBe(startingFen);
  expect(boardPosition("custom", positions, history, 2, "black").fen).toBe(expected.fen());
  expect(() => gamePositions(startingFen, ["e1e8"])).toThrow(/incomplete or invalid/);
});

it("declines invalid history or mismatched finding positions instead of showing a partial board", async () => {
  const invalid = { ...detail(), moves: ["e1e8"] };
  render(<GameReviewView api={fakeApi({ gameDetail: vi.fn(async () => invalid) })} />);
  await selectFirst();
  expect(await screen.findByText(/Original game history is incomplete or invalid/)).toBeTruthy();
  expect(screen.queryByRole("group", { name: /Original game board/ })).toBeNull();
});

it("loads read-only with no imports or learning effects and displays empty games", async () => {
  const pending = deferred<Awaited<ReturnType<GameReviewClient["recentGames"]>>>();
  const api = fakeApi({ recentGames: vi.fn(() => pending.promise) });
  render(<GameReviewView api={api} />);
  expect(screen.getByText("Loading recent games…")).toBeTruthy();
  await act(async () => pending.resolve({ games: [], sync: idleSync }));
  expect(screen.getByText(/No imported games yet/)).toBeTruthy();
  expect(api.sync).not.toHaveBeenCalled(); expect(api.retryFeedback).not.toHaveBeenCalled(); expect(api.gameDetail).not.toHaveBeenCalled();
});

it("keeps list errors local and retries the read request", async () => {
  const api = fakeApi({ recentGames: vi.fn().mockRejectedValueOnce(new Error("Provider list unavailable")).mockResolvedValue({ games: [], sync: idleSync }) });
  render(<GameReviewView api={api} />);
  expect(await screen.findByText(/Provider list unavailable/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry recent games" }));
  expect(await screen.findByText(/No imported games yet/)).toBeTruthy();
  expect(api.recentGames).toHaveBeenCalledTimes(2);
});

it("shows detail loading/error and can retry without mutating another feature", async () => {
  const pending = deferred<GameDetail>();
  const api = fakeApi({ gameDetail: vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValue(detail()) });
  const studyBefore = useTrainingStore.getState();
  render(<GameReviewView api={api} />); await selectFirst();
  expect(screen.getByText("Loading game…")).toBeTruthy();
  await act(async () => pending.reject(new Error("Game unavailable")));
  expect(await screen.findByText(/Game unavailable/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry game" })); await readyBoard();
  expect(useTrainingStore.getState()).toBe(studyBefore);
  expect(api.sync).not.toHaveBeenCalled(); expect(api.retryFeedback).not.toHaveBeenCalled();
});

it("deduplicates double feedback submission, preserves the key after failure, and keeps an actual pending receipt pending", async () => {
  const pending = deferred<LearningReceipt>();
  const api = fakeApi({ retryFeedback: vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValue(receipt()) });
  render(<GameReviewView api={api} />); await selectFirst(); await readyBoard();
  const button = screen.getByRole("button", { name: "Retry feedback at ply 1" });
  act(() => { button.click(); button.click(); });
  expect(api.retryFeedback).toHaveBeenCalledTimes(1);
  await act(async () => pending.reject(new Error("Receiving owner temporarily unavailable")));
  expect(await screen.findByText(/Receiving owner temporarily unavailable/)).toBeTruthy();
  fireEvent.click(button);
  expect(await screen.findByText("Study feedback: pending")).toBeTruthy();
  const calls = vi.mocked(api.retryFeedback).mock.calls;
  expect(calls[0][0].operation_id).toBe(calls[1][0].operation_id);
  expect(screen.getByRole("region", { name: "Game Review" }).textContent).not.toMatch(/scheduled/);
});

it.each(["applied", "settled", "held"] as const)("renders actual %s feedback status without claiming completion", async status => {
  render(<GameReviewView api={fakeApi({ gameDetail: vi.fn(async () => detail({ receipts: [receipt(status)] })) })} />);
  await selectFirst(); await readyBoard();
  expect(screen.getByText(`Study feedback: ${status}`)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry feedback at ply 1" })).toBeNull();
});

it("shows explicit stale/conflicting owner failure", async () => {
  const stale = { ...receipt(), outcome: "stale_revision" as const, reason: "Source changed" };
  render(<GameReviewView api={fakeApi({ gameDetail: vi.fn(async () => detail({ receipts: [stale] })) })} />);
  await selectFirst(); await readyBoard();
  expect(screen.getByText("Feedback not applied: stale revision — Source changed")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry feedback at ply 1" })).toBeNull();
});

const encounterLabelCases: { name: string; fields: Partial<EncounterReceipt>; expected: readonly string[] }[] = [
  { name: "recorded importance still awaiting review admission", fields: { status: "applied", disposition: "review_priority", admission_status: "pending", deferred_reason: "active_attempt", reason: null }, expected: ["Study feedback: applied", "Encounter recorded", "Disposition: review priority", "Admission: pending", "Deferred: active attempt"] },
  { name: "paused first introduction remains pending", fields: {}, expected: ["Disposition: first introduction", "Admission: pending", "Deferred: paused"] },
  { name: "actual first-introduction admission is available", fields: { status: "applied", admission_status: "available", deferred_reason: null, reason: null }, expected: ["Disposition: first introduction", "Admission: available for Study"] },
  { name: "matched frequency has no requested admission", fields: { status: "applied", observation: "matched", disposition: "frequency_only", admission_status: "not_requested", deferred_reason: null, reason: null }, expected: ["Encounter recorded", "Disposition: frequency only", "Admission: not requested"] },
  { name: "settled review admission", fields: { status: "settled", disposition: "review_priority", admission_status: "settled", deferred_reason: null, reason: null }, expected: ["Study feedback: settled", "Disposition: review priority", "Admission: settled"] },
  { name: "unrecorded unresolved chronology", fields: { observation_recorded: false, disposition: null, deferred_reason: "unknown_chronology", reason: null }, expected: ["Encounter not recorded", "Disposition: unresolved", "Admission: pending", "Deferred: unknown chronology"] },
  { name: "held conflicting source", fields: { status: "held", outcome: "conflict", disposition: null, deferred_reason: "authority_unavailable", reason: "Source changed" }, expected: ["Feedback not applied: conflict — Source changed", "Disposition: unresolved", "Admission: pending", "Deferred: authority unavailable"] },
];
it.each(encounterLabelCases)("labels owner encounter receipt: $name", ({ fields, expected }) => {
  const value = encounterReceipt(fields);
  const label = receiptLabel(value);
  for (const text of expected) expect(label).toContain(text);
  if (value.admission_status !== "available") expect(label).not.toContain("available for Study");
  expect(label).not.toContain("scheduled");
});

it("renders the shared encounter/legacy receipt union from the HTTP detail read", async () => {
  const introduced = finding("confirmed_miss");
  const unintroduced = finding("known_unintroduced", 2);
  const pending = encounterReceipt({}, unintroduced);
  const fetchRequest = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(new Response(JSON.stringify({ games: [firstSummary], sync: idleSync }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(detail({ findings: [introduced, unintroduced], receipts: [receipt("applied"), pending] })), { status: 200 }));
  const api = createGameReviewApi({ endpoints: { recentGames: "/games", gameDetail: () => "/detail", sync: "/sync", retryFeedback: "/feedback" }, fetch: fetchRequest });
  render(<GameReviewView api={api} />); await selectFirst(); await readyBoard();
  expect(screen.getByText("Study feedback: applied")).toBeTruthy();
  expect(screen.getByText(receiptLabel(pending))).toBeTruthy();
  expect(screen.getByText("Card not introduced").closest("li")?.textContent).not.toContain("available for Study");
  expect(fetchRequest).toHaveBeenCalledTimes(2);
  expect(fetchRequest.mock.calls.every(([, options]) => options?.method === "GET")).toBe(true);
});

it("preserves refresh operation identity on an uncertain retry and isolates failure from Study", async () => {
  const pending = deferred<Awaited<ReturnType<GameReviewClient["refresh"]>>>();
  const api = fakeApi({ refresh: vi.fn().mockImplementationOnce(() => pending.promise).mockImplementation(async ({ provider, operation_id }) => refreshStatus(provider, operation_id)) });
  const studyBefore = useTrainingStore.getState();
  render(<GameReviewView api={api} />); await screen.findByRole("button", { name: /Lichess ·/ });
  const button = screen.getByRole("button", { name: "Refresh Lichess now" });
  act(() => { button.click(); button.click(); });
  expect(api.refresh).toHaveBeenCalledTimes(1);
  await act(async () => pending.reject(new Error("Provider down")));
  expect(await screen.findByText("Provider down")).toBeTruthy();
  fireEvent(window, new Event("online"));
  await waitFor(() => expect(api.refresh).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.refresh).mock.calls[0][0].operation_id).toBe(vi.mocked(api.refresh).mock.calls[1][0].operation_id);
  expect(api.sync).not.toHaveBeenCalled();
  expect(useTrainingStore.getState()).toBe(studyBefore);
});

it("ignores late game detail after selection, even when transport ignores AbortSignal", async () => {
  const first = deferred<GameDetail>();
  const api = fakeApi({ gameDetail: vi.fn().mockImplementationOnce(() => first.promise).mockResolvedValue(detail({ ...secondSummary, findings: [] })) });
  render(<GameReviewView api={api} />); await selectFirst();
  const firstSignal = vi.mocked(api.gameDetail).mock.calls[0][0].signal;
  fireEvent.click(screen.getByRole("button", { name: /Chess.com · second-game/ })); await readyBoard();
  expect(firstSignal!.aborted).toBe(true);
  await act(async () => first.resolve(detail()));
  expect(screen.getByRole("heading", { level: 2, name: "Chess.com · second-game" })).toBeTruthy();
  expect(screen.queryByText("Confirmed recall miss")).toBeNull();
  expect(boards.at(-1)?.orientation).toBe("black");
});

it("refreshing to a newer game revision replaces the selection and fences obsolete detail", async () => {
  const old = deferred<GameDetail>();
  const api = fakeApi({
    recentGames: vi.fn().mockResolvedValueOnce({ games: [firstSummary], sync: idleSync }).mockResolvedValue({ games: [{ ...firstSummary, source_revision: 2 }], sync: idleSync }),
    gameDetail: vi.fn().mockImplementationOnce(() => old.promise).mockResolvedValue(detail({ source_revision: 2, findings: [] })),
  });
  render(<GameReviewView api={api} />); await selectFirst();
  const oldSignal = vi.mocked(api.gameDetail).mock.calls[0][0].signal;
  fireEvent.click(screen.getByRole("button", { name: "Refresh games and status" }));
  await readyBoard();
  expect(oldSignal?.aborted).toBe(true);
  expect(api.gameDetail).toHaveBeenCalledTimes(2);
  await act(async () => old.resolve(detail()));
  expect(screen.queryByText("Confirmed recall miss")).toBeNull();
  expect(boards.at(-1)?.positionIdentity).toContain("2");
});

it("starts a new refresh independently of an unrelated saved-range failure", async () => {
  const api = fakeApi({ recentGames: vi.fn<GameReviewClient["recentGames"]>(async () => ({ games: [firstSummary], sync: [{ provider: "lichess", status: "failed", operation_id: "original-provider-operation", error: "Try again" }] })) });
  render(<GameReviewView api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: "Refresh Lichess now" }));
  await waitFor(() => expect(api.refresh).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.refresh).mock.calls[0][0].operation_id).not.toBe("original-provider-operation");
  expect(api.sync).not.toHaveBeenCalled();
});

it("ignores a late feedback receipt from the previous selected game", async () => {
  const feedback = deferred<LearningReceipt>();
  const api = fakeApi({ retryFeedback: vi.fn(() => feedback.promise), gameDetail: vi.fn().mockResolvedValueOnce(detail()).mockResolvedValue(detail({ ...secondSummary, findings: [] })) });
  render(<GameReviewView api={api} />); await selectFirst(); await readyBoard();
  fireEvent.click(screen.getByRole("button", { name: "Retry feedback at ply 1" }));
  fireEvent.click(screen.getByRole("button", { name: /Chess.com · second-game/ }));
  await screen.findByRole("heading", { level: 2, name: "Chess.com · second-game" });
  expect(vi.mocked(api.retryFeedback).mock.calls[0][0].signal!.aborted).toBe(true);
  await act(async () => feedback.resolve(receipt("applied")));
  expect(screen.queryByText("Study feedback: applied")).toBeNull();
  expect(boards.at(-1)?.orientation).toBe("black");
});

it("fences late detail after repertoire selection and aborts all owned work on unmount", async () => {
  const first = deferred<GameDetail>(); const second = deferred<GameDetail>();
  const api = fakeApi({ gameDetail: vi.fn().mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise) });
  const view = render(<GameReviewView api={api} repertoireId="white" />); await selectFirst();
  view.rerender(<GameReviewView api={api} repertoireId="another" />);
  await waitFor(() => expect(api.gameDetail).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.gameDetail).mock.calls[0][0].signal!.aborted).toBe(true);
  await act(async () => first.resolve(detail())); expect(screen.queryByRole("group", { name: /Original game board/ })).toBeNull();
  view.unmount();
  expect(vi.mocked(api.gameDetail).mock.calls[1][0].signal!.aborted).toBe(true);
  expect(vi.mocked(api.recentGames).mock.calls[0][0]!.signal!.aborted).toBe(true);
  await act(async () => second.resolve(detail())); expect(document.querySelector("cg-board")).toBeNull();
});

it("cancels sync and feedback on unmount and ignores late owner receipts", async () => {
  const sync = deferred<Awaited<ReturnType<GameReviewClient["refresh"]>>>(); const feedback = deferred<LearningReceipt>();
  const api = fakeApi({ refresh: vi.fn(() => sync.promise), retryFeedback: vi.fn(() => feedback.promise) });
  const view = render(<GameReviewView api={api} />); await selectFirst(); await readyBoard();
  fireEvent.click(screen.getByRole("button", { name: "Refresh Lichess now" }));
  fireEvent.click(screen.getByRole("button", { name: "Retry feedback at ply 1" }));
  view.unmount();
  expect(vi.mocked(api.refresh).mock.calls[0][0].signal!.aborted).toBe(true);
  expect(vi.mocked(api.retryFeedback).mock.calls[0][0].signal!.aborted).toBe(true);
  await act(async () => { feedback.resolve(receipt()); sync.resolve({ ...refreshStatus("lichess", "late"), status: "complete" }); });
  expect(document.querySelector("cg-board")).toBeNull();
});

it("emits only a scoped repertoire-gap navigation intent", async () => {
  const gap = finding("opponent_gap", 2); const onReviewGap = vi.fn();
  const api = fakeApi({ gameDetail: vi.fn(async () => detail({ findings: [gap] })) });
  render(<GameReviewView api={api} onReviewGap={onReviewGap} />); await selectFirst(); await readyBoard();
  fireEvent.click(screen.getByRole("button", { name: "Review repertoire gap at ply 2" }));
  expect(onReviewGap).toHaveBeenCalledWith({ kind: "review_repertoire_gap", provider: gap.provider, game_id: gap.game_id, source_revision: gap.source_revision, repertoire_id: gap.repertoire_id, repertoire_revision: gap.repertoire_revision, position_key: gap.position_key, ply: gap.ply });
  expect(api.retryFeedback).not.toHaveBeenCalled(); expect(api.sync).not.toHaveBeenCalled();
});

it("handles history keys only inside the active history region without bubbling commands", async () => {
  render(<GameReviewView api={fakeApi()} />); await selectFirst(); await readyBoard();
  const outside = vi.fn(); document.body.addEventListener("keydown", outside);
  try {
    fireEvent.keyDown(screen.getByRole("group", { name: /Original game board/ }), { key: "ArrowRight" });
    expect(boards.at(-1)?.positionRevision).toBe(1); expect(outside).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "End" });
    expect(boards.at(-1)?.positionRevision).toBe(1);
    fireEvent.keyDown(screen.getByRole("region", { name: "Original game history" }), { key: "End" });
    expect(boards.at(-1)?.positionRevision).toBe(moves.length);
    fireEvent.keyDown(screen.getByRole("button", { name: "Previous" }), { key: "Home" });
    expect(boards.at(-1)?.positionRevision).toBe(moves.length);
  } finally { document.body.removeEventListener("keydown", outside); }
});

it("validates wire responses and sends explicit mutation identities through injected endpoints", async () => {
  const fetchRequest = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ nonsense: true }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(receipt()), { status: 200 })).mockResolvedValueOnce(new Response("unavailable", { status: 503 }));
  const api = createGameReviewApi({ endpoints: { recentGames: "/games", gameDetail: game => `/games/${game.provider}/${game.game_id}`, sync: "/sync", retryFeedback: "/feedback/retry" }, fetch: fetchRequest });
  const signal = new AbortController().signal;
  await expect(api.recentGames({ signal })).rejects.toThrow();
  await expect(api.retryFeedback({ finding: finding("confirmed_miss"), operation_id: "original-operation", signal })).resolves.toEqual(receipt());
  expect(JSON.parse(String(fetchRequest.mock.calls[1][1]?.body)).operation_id).toBe("original-operation");
  expect(fetchRequest.mock.calls[1][1]?.signal).toBe(signal);
  await expect(api.gameDetail({ provider: "lichess", game_id: "game", signal })).rejects.toThrow(/HTTP 503/);
});

it("keeps the new feature dependency boundary independent from Study/Tactics controllers and storage", () => {
  const allowed = new Set(["react", "chess.js", "@tempo/board-ui", "../../../packages/board-ui/styles/board.css", "../../components/buttons/BaseButton", "../../hooks/use-board-history", "../product-api/contracts", "./api", "./state", "./game-review.css", "./use-game-refresh", "../../lib/operation-status", "../../../database/browser/adapters/games-analysis/game-sync"]);
  for (const name of ["GameReviewView.tsx", "state.ts", "api.ts", "use-game-refresh.ts"]) {
    const source = readFileSync(resolve("app/features/game-review", name), "utf8");
    const imports = [...source.matchAll(/(?:from\s+|import\s*)["']([^"']+)["']/g)].map(match => match[1]);
    expect(imports.filter(path => !allowed.has(path))).toEqual([]);
    expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|resetStudy|startStudy/);
  }
});


it("does not offer legacy lapse retry for a pending confirmed-miss encounter receipt", async () => {
  const missed = finding("confirmed_miss");
  const pending = encounterReceipt({ status: "pending", outcome: "accepted",
    admission_status: "pending", disposition: "existing_lapse", deferred_reason: null,
    reason: "existing_lapse_obligation" }, missed);
  const api = fakeApi({ gameDetail: vi.fn(async () => detail({ findings: [missed], receipts: [pending] })) });
  render(<GameReviewView api={api} />); await selectFirst(); await readyBoard();
  expect(screen.getByText(/Disposition: existing lapse · Admission: pending/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry feedback at ply 1" })).toBeNull();
  expect(api.retryFeedback).not.toHaveBeenCalled();
});
