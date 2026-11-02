import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import Home from "../../app/views/home_view";
import TrainingView from "../../app/views/training_view";
import { mapQueueCardToPracticeCard, queueCardsFromPayload } from "../../app/domain/adapters/practice-card-adapters";
import { attemptEntryKey, initialTrainingState, positionStudyUnavailable, trainingAttemptId } from "../../app/domain/attempt";
import { canonicalFenKey } from "../../app/utils/canonical-line";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import * as journal from "../../app/lib/opening-evidence-journal";
import { studyIntentStore } from "../../database/browser/study-intent-storage";

vi.mock("../../app/views/fetchAndInitializeQueue", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/views/fetchAndInitializeQueue")>(), fetchAndInitializeQueue: vi.fn(),
}));
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));

const italian = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"];
function issuedCard({ moves = italian, start = 2, color = "white", id = "italian", entry = 42 } = {}) {
  const board = new Chess();
  const originalFen = board.fen();
  moves.slice(0, start).forEach(uci => board.move(uci));
  const startingFen = board.fen();
  const decisions: { move_offset: number; position_key: string; expected_uci: string }[] = [];
  const manifestDecisions: { decision_id: string; decision_index: number; move_offset: number; fen: string; expected_uci: string }[] = [];
  moves.slice(start).forEach((uci, move_offset) => {
    if (board.turn() === (color === "black" ? "b" : "w")) {
      decisions.push({ move_offset, position_key: canonicalFenKey(board.fen()), expected_uci: uci });
      manifestDecisions.push({ decision_id: String(manifestDecisions.length + 1).repeat(64), decision_index: manifestDecisions.length, move_offset, fen: board.fen(), expected_uci: uci });
    }
    board.move(uci);
  });
  return {
    id, queue_entry_id: entry, revision: 3, start_fen: originalFen, moves,
    content_type: "opening", repertoire_name: "Italian", repertoire_source: "PGN", trained_color: color,
    opening_decision_manifest: {
      manifest_version: 1, policy_version: 1, manifest_id: "f".repeat(64), presentation_snapshot_id: 1,
      repertoire_id: "italian-repertoire", card_id: id, card_revision: 3, trained_color: color, decisions: manifestDecisions,
    },
    position_study_mode: "position",
    position_presentation: {
      version: 1, plan_id: "a".repeat(64), original_card_id: id, original_card_revision: 3,
      queue_entry_id: entry, attempt_id: `issued-attempt-${entry}`, owner_epoch: "epoch-1", route_id: "authored-route",
      target_ply: start, starting_fen: startingFen, moves_uci: moves.slice(start), decision_slots: decisions,
      source_digest: "source-1", local_day: "2026-11-02", skipped_prefix_proof_ids: start ? ["whole-clean-proof"] : [],
    },
  };
}

beforeEach(() => {
  Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("requestIdleCallback", vi.fn(() => 1));
  vi.stubGlobal("cancelIdleCallback", vi.fn());
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ states: [], lines: [], repertoires: [] })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("issued position suffix browser consumer", () => {
  it.each([false, true])("Italian queue order reversed=%s uses each authorized line without browser prefix selection", reversed => {
    const full = issuedCard({ start: 0, entry: 41 });
    const suffix = issuedCard({ start: 4, entry: 42 });
    const raw = reversed ? [suffix, full] : [full, suffix];
    const cards = queueCardsFromPayload({ cards: raw });
    expect(cards.map(card => card.moves)).toEqual(reversed ? [["Bc4", "Bc5"], ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]] : [["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], ["Bc4", "Bc5"]]);
    cards.forEach((card, index) => {
      expect(card.startingFen).toBe(raw[index].position_presentation.starting_fen);
      expect(card.backendId).toBe("italian");
      expect(card.queueEntryId).toBe(raw[index].queue_entry_id);
      expect(card.revision).toBe(3);
      expect(trainingAttemptId(card)).toBe(raw[index].position_presentation.attempt_id);
      expect(card.positionPresentation?.moves_uci).toEqual(raw[index].position_presentation.moves_uci);
      expect(Object.isFrozen(card.positionPresentation)).toBe(true);
    });
    expect(suffix.moves).toEqual(italian);
  });

  it("plays only the authorized leading opponent cue and retains black orientation", () => {
    const raw = issuedCard({ color: "black", start: 2 });
    const card = mapQueueCardToPracticeCard(raw);
    const start = initialTrainingState(card);
    const expected = new Chess(raw.position_presentation.starting_fen); expected.move("g1f3");
    expect(card.orientation).toBe("black");
    expect(card.moves).toEqual(["Nf3", "Nc6", "Bc4", "Bc5"]);
    expect(start).toMatchObject({ fen: expected.fen(), step: 1, lastMove: ["g1", "f3"] });
    useTrainingStore.getState().initializeCardState(card);
    render(<TrainingView dateLabel="Today" serviceError="" refreshDatabaseQueue={vi.fn()} cardsLeft={1} card={card}
      boardTheme="brown" pieceSet="cburnett" rateCard={vi.fn()} handleAttemptFailure={vi.fn()} resetCardAttempt={vi.fn()} onMove={vi.fn()} useSharedBoard />);
    expect(useBoardShellStore.getState().board).toMatchObject({ fen: expected.fen(), orientation: "black", expectedSan: "Nc6", interactionMode: "legal" });
    expect(screen.getByText("2.")).toBeTruthy();
    expect(screen.getByText("Nf3")).toBeTruthy();
  });

  it("reload keeps the exact issued suffix and immutable identity; a changed plan fences stale callbacks", () => {
    const raw = issuedCard();
    const first = mapQueueCardToPracticeCard(raw);
    const reloaded = mapQueueCardToPracticeCard(JSON.parse(JSON.stringify(raw)));
    expect(attemptEntryKey(reloaded)).toBe(attemptEntryKey(first));
    expect(trainingAttemptId(reloaded)).toBe(trainingAttemptId(first));
    useTrainingStore.getState().initializeCardState(first);
    expect(useTrainingStore.getState().attempt.attemptId).toBe("issued-attempt-42");
    useTrainingStore.getState().initializeCardState(reloaded);
    expect(useTrainingStore.getState().attempt.attemptId).toBe("issued-attempt-42");
    expect(reloaded.positionPresentation).toEqual(first.positionPresentation);
    const replacement = mapQueueCardToPracticeCard({ ...raw, position_presentation: { ...raw.position_presentation, plan_id: "b".repeat(64), attempt_id: "replacement" } });
    expect(attemptEntryKey(replacement)).not.toBe(attemptEntryKey(first));
  });

  it.each(["missing", "held", "identity", "illegal", "slot", "manifest"])("holds %s plans without falling back to the physical full line", kind => {
    const raw = issuedCard();
    const input = kind === "missing" ? { ...raw, position_presentation: null }
      : kind === "held" ? { ...raw, position_study_hold: "owner_unavailable" }
      : kind === "manifest" ? { ...raw, opening_decision_manifest: undefined }
      : { ...raw, position_presentation: { ...raw.position_presentation,
        ...(kind === "identity" ? { queue_entry_id: 99 } : kind === "illegal" ? { starting_fen: new Chess().fen() } : { decision_slots: [raw.position_presentation.decision_slots[0]] }),
      } };
    const card = queueCardsFromPayload({ cards: [input, issuedCard({ entry: 99 })] })[0];
    expect(card.queueEntryId).toBe(42);
    expect(card.moves).toEqual([]);
    expect(card.positionStudyMode).toBe("position");
    expect(positionStudyUnavailable(card)).toMatch(/unavailable/);
  });

  it("offline position boards and grading are unavailable while legacy practice remains playable", () => {
    const card = mapQueueCardToPracticeCard(issuedCard());
    useTrainingStore.getState().initializeCardState(card);
    const props = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1, card,
      boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(), handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), onMove: vi.fn(), useSharedBoard: true, offlineQueue: true };
    const view = render(<TrainingView {...props} />);
    expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
    expect(screen.getByText(/Position study requires a connection/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Show move/ }) as HTMLButtonElement).disabled).toBe(true);
    const { position_study_mode: _mode, position_presentation: _plan, ...legacyRaw } = issuedCard();
    const legacy = mapQueueCardToPracticeCard(legacyRaw);
    useTrainingStore.getState().initializeCardState(legacy);
    view.rerender(<TrainingView {...props} card={legacy} />);
    expect(positionStudyUnavailable(legacy, true)).toBeUndefined();
    expect(legacy.moves).toEqual(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]);
    expect(useBoardShellStore.getState().board.interactionMode).toBe("legal");
  });

  it("actual Home records only displayed learner offsets and retains wrong/hint/guided correction without physical failure writes", async () => {
    const card = mapQueueCardToPracticeCard(issuedCard());
    const response = vi.fn(), assistance = vi.fn(), manualFailure = vi.fn();
    vi.spyOn(journal, "beginOpeningAttempt").mockReturnValue({ response, assistance, manualFailure } as unknown as journal.OpeningAttemptJournal);
    vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => { useTrainingStore.getState().hydrateLocalQueue([card], true); return "installed"; });
    render(<Home />);
    await waitFor(() => expect(useTrainingStore.getState().getCard().positionPresentation?.attempt_id).toBe("issued-attempt-42"));
    expect(useBoardShellStore.getState().board.expectedSan).toBe("Nf3");
    await act(async () => useBoardShellStore.getState().board.onMove?.("g1", "g4"));
    expect(response).toHaveBeenCalledWith(0, "g1g4", "illegal");
    expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
    act(() => useBoardShellStore.getState().board.onHintExposure?.(useTrainingStore.getState().currentFenString));
    expect(assistance).toHaveBeenLastCalledWith(0, "revealed");
    await act(async () => useBoardShellStore.getState().board.onMove?.("g1", "f3"));
    await waitFor(() => expect(useTrainingStore.getState().step).toBe(2));
    expect(response.mock.calls).toEqual([[0, "g1g4", "illegal"], [0, "g1f3", "expected"]]);
    expect(useBoardShellStore.getState().board.expectedSan).toBe("Bc4");
    expect(useTrainingStore.getState().attempt.phase).toBe("guided");
    expect((await (await studyIntentStore()).list("failure"))).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /Restart/ }));
    expect(useTrainingStore.getState().step).toBe(0);
    expect(useTrainingStore.getState().attempt.attemptId).toBe("issued-attempt-42");
    act(() => useBoardShellStore.getState().board.onHintExposure?.(useTrainingStore.getState().currentFenString));
    expect(assistance).toHaveBeenLastCalledWith(0, "guided");
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    act(() => window.dispatchEvent(new Event("offline")));
    expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
    const count = response.mock.calls.length;
    await act(async () => useBoardShellStore.getState().board.onMove?.("g1", "f3"));
    expect(response).toHaveBeenCalledTimes(count);
  });
});
