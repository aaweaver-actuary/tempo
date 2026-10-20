import { studyIntentStore } from "../../app/lib/study-intent-storage";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { Chess, type Square } from "chess.js";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Home from "../../app/views/legacy-home";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import * as study from "../../app/lib/background-study";
import * as canonical from "../../app/utils/canonical-line";
import * as journal from "../../app/lib/opening-evidence-journal";
import type { PracticeCard } from "../../app/types";
import type { RepertoireAlternativeSnapshot } from "../../app/lib/repertoire-alternative-index";
import { buildRepertoireAlternativeSnapshot } from "../../app/lib/repertoire-alternative-index";
import { computeStudyTask } from "../../app/lib/study-computation";
import * as local from "../../app/utils/local";
import * as workspace from "../../app/lib/workspace-data";
import * as priority from "../../app/lib/study-priority";
import { demoCards } from "../../app/samples";

// Main ignores the result; PR #172 explicitly consumes successful installation.
const initializeQueueMock = vi.hoisted(() => vi.fn<() => Promise<"installed">>());
vi.mock("../../app/views/fetchAndInitializeQueue", () => ({ fetchAndInitializeQueue: initializeQueueMock, invalidateTrainingQueueCache: vi.fn() }));
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
let importCallback: () => Promise<void>;
let editorCallbacks: { onSave: (card: PracticeCard) => void; onDelete: (card: PracticeCard) => Promise<void> };
let reconcileCallback: (repertoireId: string) => void;
let pasteCallback: (affected: string[], conflicting: string[]) => void;
let openPaste: () => void;
let repertoireChanged: () => Promise<void>;
vi.mock("../../app/ImportDialogBox", () => ({ ImportDialogBox: (props: { onDatabaseUpdated: () => Promise<void> }) => {
  importCallback = props.onDatabaseUpdated; return <div />;
} }));
vi.mock("../../app/views/card_editor", () => ({ default: (props: typeof editorCallbacks) => {
  editorCallbacks = props; return <div />;
} }));
vi.mock("../../app/components/integrity-repair-status", () => ({ IntegrityRepairStatus: (props: { onReconcile: typeof reconcileCallback }) => {
  reconcileCallback = props.onReconcile; return <div />;
} }));
vi.mock("../../app/views/repertoire_view", () => ({ default: (props: { onPaste: typeof openPaste; onQueueChanged: typeof repertoireChanged }) => {
  openPaste = props.onPaste; repertoireChanged = props.onQueueChanged; return <div />;
} }));
vi.mock("../../app/AnalysisPasteDialog", () => ({ AnalysisPasteDialog: (props: { onSaved: typeof pasteCallback }) => {
  pasteCallback = props.onSaved; return <div />;
} }));

const startFen = new Chess().fen();
const card = mapQueueCardToPracticeCard({ id: "expected-card", revision: 1, queue_entry_id: 3601,
  start_fen: startFen, moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
  repertoire_id: "issue36", repertoire_name: "Index", repertoire_source: "PGN", trained_color: "white" });
const otherCard = mapQueueCardToPracticeCard({ id: "other-card", revision: 1, queue_entry_id: 3602,
  start_fen: startFen, moves: ["d2d4"], content_type: "opening",
  repertoire_id: "issue36", repertoire_name: "Index", repertoire_source: "PGN", trained_color: "white" });
function line(id: string, moves: string[] = ["d2d4"], repertoire = "issue36", fen = startFen) {
  return { id, repertoire_id: repertoire, repertoire_name: "Index", name: id, trained_color: "white", start_fen: fen, moves };
}
let sourceLines: ReturnType<typeof line>[];
let lineRequests: number;
let nextLineRead: Promise<Response> | undefined;
let responseJournal: ReturnType<typeof vi.fn>;
let studyOnlyMode: boolean;
let membershipRead: Promise<Response> | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  sourceLines = [line("alternative")]; lineRequests = 0; nextLineRead = undefined;
  studyOnlyMode = false;
  membershipRead = undefined;
  // Existing direct board-callback cases have no browser input event.
  vi.spyOn(priority, "studyInteractionActive").mockReturnValue(false);
  responseJournal = vi.fn();
  vi.spyOn(journal, "recoverOpeningEvidence").mockResolvedValue({ moreWork: false });
  vi.spyOn(journal, "beginOpeningAttempt").mockReturnValue({ response: responseJournal,
    assistance: vi.fn(), decision: vi.fn() } as unknown as journal.OpeningAttemptJournal);
  initializeQueueMock.mockImplementation(async () => {
    if (!useTrainingStore.getState().isDatabaseQueueActive || !useTrainingStore.getState().practiceCards.length)
      useTrainingStore.getState().hydrateLocalQueue([card, otherCard], true);
    useTrainingStore.setState({ queueReadiness: "ready" });
    return "installed";
  });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/api/settings")) return Response.json({ study_only_mode: studyOnlyMode });
    if (String(input).includes("/move-membership?")) return membershipRead ?? Response.json({ verified: true, next_cursor: null, source_token: "saved-source-1" });
    if (String(input).includes("/repertoire/lines")) {
      lineRequests++;
      const heldRead = nextLineRead; nextLineRead = undefined;
      return heldRead ?? Response.json({ lines: sourceLines });
    }
    return Response.json({ providers: [], states: [], repertoires: [], discoveries: [], annotations: [], attempt_failed: true });
  }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("study_only_mode_pauses_bulk_preparation_preserves_expected_moves_and_resumes", async () => {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  studyOnlyMode = true;
  render(<Home />);
  await waitFor(() => expect(screen.getByText(/Study-only mode is on. Optional/)).toBeTruthy());
  expect(lineRequests).toBe(0);
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  await play("e2e4");
  expect(useTrainingStore.getState().getCard().backendId).toBe(card.backendId);
  expect(responseJournal).toHaveBeenCalled();
  studyOnlyMode = false;
  act(() => window.dispatchEvent(new Event("tempo:study-mode-changed")));
  await waitFor(() => expect(lineRequests).toBe(1));
});

it("study_only_mode_verifies_non_expected_move_from_saved_sources_without_bulk_preparation", async () => {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  studyOnlyMode = true;
  render(<Home />);
  await waitFor(() => expect(screen.getByText(/Study-only mode is on. Optional/)).toBeTruthy());
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  await play("d2d4");
  await waitFor(() => expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "alternate"));
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
  expect(lineRequests).toBe(0);
  expect(screen.getByText(/Valid repertoire move/)).toBeTruthy();
});

it.each(["wrong", "unavailable", "cancelled"])("study_only_move_verification_preserves_grading_and_rejects_obsolete_results (%s)", async scenario => {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  studyOnlyMode = true;
  let finishRead!: (response: Response) => void;
  membershipRead = scenario === "cancelled" ? new Promise(resolve => { finishRead = resolve; })
    : Promise.resolve(scenario === "unavailable" ? new Response("unavailable", { status: 503 })
      : Response.json({ verified: false, next_cursor: null, source_token: "current-source" }));
  render(<Home />);
  await waitFor(() => expect(screen.getByText(/Study-only mode is on. Optional/)).toBeTruthy());
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  if (scenario === "cancelled") {
    act(() => { void useBoardShellStore.getState().board.onMove?.("c2", "c4"); });
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/move-membership?'))).toBe(true));
    act(() => useTrainingStore.getState().hydrateLocalQueue([otherCard], true));
    await act(async () => finishRead(Response.json({ verified: false, next_cursor: null, source_token: "current-source" })));
    expect(responseJournal).not.toHaveBeenCalled();
  } else {
    await play("c2c4");
    expect(responseJournal).toHaveBeenLastCalledWith(0, "c2c4", scenario === "wrong" ? "wrong" : "unverified");
  }
  expect(useTrainingStore.getState().isAttemptFailed).toBe(scenario === "wrong");
  expect(lineRequests).toBe(0);
});

async function readyHome() {
  const prepare = vi.spyOn(study, "runStudyTask");
  const mounted = render(<Home />);
  await waitFor(() => expect(prepare.mock.calls.some(([task]) => task.kind === "index" || task.kind === "repertoirePreparation")).toBe(true));
  await act(async () => {
    const indexCall = prepare.mock.calls.findIndex(([task]) => task.kind === "index" || task.kind === "repertoirePreparation");
    const prepared = await prepare.mock.results[indexCall].value;
    expect(Array.isArray(prepared) ? prepared.length : prepared.positions.length).toBeGreaterThan(0);
  });
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe(card.backendId));
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  return mounted;
}
async function play(uci: string) {
  await act(async () => useBoardShellStore.getState().board.onMove?.(uci.slice(0, 2) as Square, uci.slice(2, 4) as Square));
  // The board callback starts durable failure persistence without returning it.
  // Finish that write before the fixture closes its store and the next case opens one.
  if (useTrainingStore.getState().isAttemptFailed && useTrainingStore.getState().isDatabaseQueueActive)
    await waitFor(async () => expect((await (await studyIntentStore()).list("failure")).length).toBeGreaterThan(0));
}
async function finishPreparation(count: number) {
  const prepare = vi.mocked(study.runStudyTask);
  await waitFor(() => expect(prepare.mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(count));
  const preparationCalls = prepare.mock.calls.flatMap(([task], index) => task.kind === "repertoirePreparation" ? [index] : []);
  await act(async () => { await prepare.mock.results[preparationCalls.at(-1)!].value; });
}
async function mutate(kind: string) {
  if (kind === "import" || kind === "re-import") {
    act(() => useTrainingStore.getState().setShowImport(true));
    await waitFor(() => expect(importCallback).toBeTypeOf("function"));
    await act(async () => { await importCallback(); useTrainingStore.getState().setShowImport(false); });
  } else if (kind === "edit" || kind === "delete") {
    act(() => useTrainingStore.getState().setEditorCard(kind === "edit" ? card : otherCard));
    await waitFor(() => expect(editorCallbacks?.onSave).toBeTypeOf("function"));
    await act(async () => {
      if (kind === "edit") editorCallbacks.onSave(card);
      else await editorCallbacks.onDelete(otherCard);
      useTrainingStore.getState().setEditorCard(null);
    });
  } else act(() => reconcileCallback("issue36"));
}

it("issue36_home_query_canonicalizes_once_instead_of_scanning_positions", async () => {
  sourceLines = Array.from({ length: 200 }, (_, index) => line(`line-${index}`, ["e2e4"]));
  await readyHome();
  const canonicalize = vi.spyOn(canonical, "canonicalFenKey");
  await play("d2d4");
  expect(useTrainingStore.getState().feedback).toBe("wrong");
  // Failure annotations also canonicalize once; membership itself is covered
  // separately with an exact one-call assertion and a forbidden record read.
  expect(canonicalize.mock.calls.length).toBeGreaterThan(0);
  expect(canonicalize.mock.calls.length).toBeLessThanOrEqual(2);
});

it.each(["import", "re-import", "edit", "delete", "reconcile"])("issue36_authoritative_changes_invalidate_previous_alternatives (%s)", async kind => {
  await readyHome();
  let finishRead!: (response: Response) => void;
  nextLineRead = new Promise(resolve => { finishRead = resolve; });
  sourceLines = [line("replacement", ["e2e4"])];
  await mutate(kind);
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "unverified");
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
  expect(screen.getByText(/Cannot verify another repertoire move until lines load/)).toBeTruthy();
  await waitFor(() => expect(lineRequests).toBe(2));
  await act(async () => { finishRead(Response.json({ lines: sourceLines })); });
  // Wait for worker preparation, without a wall-clock sleep or a grading probe.
  await finishPreparation(2);
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "wrong");
  expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
});

it.each([
  ["e2e4", "expected", "correct", false],
  ["d2d4", "alternate", "branch", false],
  ["c2c4", "wrong", "wrong", true],
  ["e2e5", "illegal", "wrong", true],
] as const)("issue36_move_classification_preserves_attempt_grading (%s)", async (uci, classification, feedback, failed) => {
  await readyHome();
  const prepareCalls = vi.mocked(study.runStudyTask).mock.calls.filter(([task]) => task.kind === "repertoirePreparation").length;
  await play(uci);
  expect(responseJournal).toHaveBeenLastCalledWith(0, uci, classification);
  expect(useTrainingStore.getState()).toMatchObject({ feedback, isAttemptFailed: failed });
  await waitFor(async () => expect((await (await studyIntentStore()).list("failure")).length > 0).toBe(failed));
  expect(vi.mocked(study.runStudyTask).mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(prepareCalls);
  if (classification !== "expected") expect(useTrainingStore.getState().currentFenString).toBe(startFen);
});

it("issue36_failed_loading_never_grades_unverified_moves_wrong", async () => {
  nextLineRead = Promise.resolve(new Response("Unavailable", { status: 503 }));
  const prepare = vi.spyOn(study, "runStudyTask");
  render(<Home />);
  await screen.findByRole("button", { name: "Retry loading lines" });
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe(card.backendId));
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "unverified");
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
  expect(localStorage.getItem("tempo-pending-training-failures-v1")).toBeNull();
  // Expected input remains usable while alternative evidence is unavailable.
  await play("e2e4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "e2e4", "expected");
  expect(useTrainingStore.getState().feedback).toBe("correct");
  await act(async () => { screen.getByRole("button", { name: "Retry loading lines" }).click(); });
  await finishPreparation(1);
  expect(prepare.mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Retry loading lines" })).toBeNull();
});

it.each(["fetch", "preparation"] as const)("issue36_late_loads_cannot_replace_current_evidence (%s)", async stage => {
  const dispatch = vi.spyOn(window, "dispatchEvent");
  const lineReadyEvents = () => dispatch.mock.calls.filter(([event]) => event instanceof CustomEvent &&
    event.type === "tempo-workspace-data" && event.detail.state === "ready" && event.detail.url.endsWith("/api/repertoire/lines"));
  let releaseOld!: (value: Response | RepertoireAlternativeSnapshot) => void;
  const heldOld = new Promise<Response | RepertoireAlternativeSnapshot>(resolve => { releaseOld = resolve; });
  const originalRun = study.runStudyTask;
  let heldGeneration = 0;
  if (stage === "fetch") nextLineRead = heldOld as Promise<Response>;
  const prepare = vi.spyOn(study, "runStudyTask").mockImplementation((task, signal) => {
    if (stage === "preparation" && task.kind === "repertoirePreparation" && !heldGeneration) {
      heldGeneration = task.generation;
      return heldOld;
    }
    return originalRun(task, signal);
  });
  render(<Home />);
  await waitFor(() => expect(lineRequests).toBe(1));
  if (stage === "preparation") await waitFor(() => expect(heldGeneration).toBeGreaterThan(0));
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe(card.backendId));
  const obsoleteLines = [...sourceLines];
  sourceLines = [line("new-generation", ["e2e4"])];
  await mutate("edit");
  await finishPreparation(stage === "fetch" ? 1 : 2);
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "wrong");
  const currentReadyCount = lineReadyEvents().length;
  await act(async () => {
    if (stage === "fetch") releaseOld(Response.json({ lines: obsoleteLines }));
    else releaseOld(buildRepertoireAlternativeSnapshot(heldGeneration,
      computeStudyTask({ kind: "index", lines: [{ id: "old", repertoireId: "issue36", repertoireName: "Index", title: "old",
        side: "white", startingFen: startFen, moves: ["d2d4"] }] as import("../../app/types").AnalysisLine[] }) as import("../../app/lib/position-similarity").IndexedPosition[]));
    await heldOld;
  });
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "wrong");
  expect(lineReadyEvents()).toHaveLength(currentReadyCount);
  expect(prepare.mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(stage === "fetch" ? 1 : 2);
});

it("issue36_obsolete_preparation_failure_cannot_hide_a_new_success", async () => {
  let rejectOld!: (error: Error) => void;
  const obsoletePreparation = new Promise((_, reject) => { rejectOld = reject; });
  const originalRun = study.runStudyTask;
  let held = false;
  vi.spyOn(study, "runStudyTask").mockImplementation((task, signal) => {
    if (task.kind === "repertoirePreparation" && !held) { held = true; return obsoletePreparation; }
    return originalRun(task, signal);
  });
  render(<Home />);
  await waitFor(() => expect(held).toBe(true));
  await mutate("edit");
  await finishPreparation(2);
  await act(async () => { rejectOld(new Error("Obsolete preparation failed")); });
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "alternate");
  expect(screen.queryByText(/Obsolete preparation failed/)).toBeNull();
});

it("issue36_navigation_rejects_an_obsolete_fetch_and_return_acquires_fresh_evidence", async () => {
  await readyHome();
  let releaseOld!: (response: Response) => void;
  const obsoleteRead = new Promise<Response>(resolve => { releaseOld = resolve; });
  nextLineRead = obsoleteRead;
  await mutate("edit");
  await waitFor(() => expect(lineRequests).toBe(2));
  act(() => screen.getByRole("button", { name: "Repertoire" }).click());
  await waitFor(() => expect(openPaste).toBeTypeOf("function"));
  sourceLines = [line("changed-while-away", ["e2e4"])];
  const trainingButtons = await screen.findAllByRole("button", { name: "Train" });
  act(() => trainingButtons[0].click());
  await finishPreparation(2);
  await act(async () => { releaseOld(Response.json({ lines: [line("obsolete")] })); await obsoleteRead; });
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "wrong");
  expect(lineRequests).toBe(3);
});

it("issue36_unmount_cancels_preparation_and_ignores_its_late_failure", async () => {
  let rejectOld!: (error: Error) => void;
  const obsoletePreparation = new Promise((_, reject) => { rejectOld = reject; });
  const originalRun = study.runStudyTask;
  let heldSignal: AbortSignal | undefined;
  vi.spyOn(study, "runStudyTask").mockImplementation((task, signal) => {
    if (task.kind === "repertoirePreparation") { heldSignal = signal; return obsoletePreparation; }
    return originalRun(task, signal);
  });
  const mounted = render(<Home />);
  await waitFor(() => expect(heldSignal).toBeDefined());
  mounted.unmount();
  expect(heldSignal!.aborted).toBe(true);
  await act(async () => { rejectOld(new Error("Obsolete after unmount")); });
});

it("issue36_pasted_lines_and_repertoire_mutations_reload_on_return_to_training", async () => {
  await readyHome();
  act(() => screen.getByRole("button", { name: "Repertoire" }).click());
  await waitFor(() => expect(openPaste).toBeTypeOf("function"));
  act(() => openPaste());
  await waitFor(() => expect(pasteCallback).toBeTypeOf("function"));
  sourceLines = [line("pasted", ["e2e4"])];
  await act(async () => { pasteCallback(["issue36"], []); await repertoireChanged(); });
  act(() => screen.getAllByRole("button", { name: "Train" })[0].click());
  await finishPreparation(2);
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "wrong");
});

it("issue36_ordinary_queue_and_card_updates_do_not_rebuild_evidence", async () => {
  await readyHome();
  await act(async () => { await fetchAndInitializeQueue(false); });
  act(() => useTrainingStore.getState().setReviewed(1));
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "alternate");
  expect(vi.mocked(study.runStudyTask).mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(1);
});

it("issue36_loading_evicts_only_lines_and_review_refresh_keeps_workspace_invalidation", async () => {
  const invalidate = vi.spyOn(workspace, "invalidateWorkspaceData");
  const originalFetch = fetch;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/repertoire/lines"))
      expect(invalidate.mock.lastCall).toEqual([String(input)]);
    return originalFetch(input, init);
  }));
  await readyHome();
  invalidate.mockClear();
  const queueReads = vi.mocked(fetchAndInitializeQueue).mock.calls.length;
  act(() => useTrainingStore.getState().setOfflineQueue(true));
  act(() => screen.getByRole("button", { name: "Retry sync" }).click());
  await waitFor(() => expect(vi.mocked(fetchAndInitializeQueue).mock.calls.length).toBeGreaterThan(queueReads));
  expect(invalidate.mock.calls).toEqual([[]]);
  expect(vi.mocked(study.runStudyTask).mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(1);
});

it("issue36_offline_missing_evidence_keeps_the_existing_practice_card_fallback", async () => {
  nextLineRead = new Promise(() => {});
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe(card.backendId));
  act(() => useTrainingStore.getState().setOfflineQueue(true));
  await play("d2d4");
  expect(responseJournal).toHaveBeenLastCalledWith(0, "d2d4", "alternate");
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
});

it("issue36_demo_keeps_demo_branch_membership_without_loading_an_index", async () => {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(false);
  initializeQueueMock.mockResolvedValue("installed");
  const prepare = vi.spyOn(study, "runStudyTask");
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().getCard().id).toBe(demoCards[0].id));
  const blackDemo = { ...demoCards[0], orientation: "black" as const };
  act(() => {
    useTrainingStore.getState().setPracticeCards([blackDemo]);
    useTrainingStore.getState().resetTrainingLine(blackDemo);
  });
  await waitFor(() => expect(useTrainingStore.getState().step).toBe(1));
  await play("e7e6");
  expect(useTrainingStore.getState()).toMatchObject({ feedback: "branch", isAttemptFailed: false });
  expect(prepare.mock.calls.filter(([task]) => task.kind === "repertoirePreparation")).toHaveLength(0);
});

it("pr285_teaching_cross_tab_committed_facts_update_the_mounted_home", async () => {
  const { openIntentStore } = await import("@tempo/browser-intents");
  const { API_URL } = await import("../../app/const");
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  render(<Home />);
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  const other = await openIntentStore({ workspace: `${new URL(API_URL || "/", window.location.href).origin}:study` });
  try {
    await act(async () => {
      await other.addTeachingFact({ key: "cross-tab-card:1:0", cardId: "cross-tab-card", revision: 1, ply: 0, kind: "seen" });
      await other.addTeachingFact({ key: "first-clean:cross-tab-card", cardId: "cross-tab-card", revision: 1, kind: "first-clean" });
    });
    await waitFor(() => expect([...useTrainingStore.getState().seenMoves]).toContain("cross-tab-card:1:0"));
    await waitFor(() => expect(useTrainingStore.getState().firstCleanPasses.has("cross-tab-card")).toBe(true));
  } finally { other.close(); }
});

it("pr285_teaching_cross_tab_reload_keeps_indexeddb_only_facts_after_home_startup", async () => {
  const { rememberTeachingFact } = await import("../../app/lib/study-intent-storage");
  await rememberTeachingFact({ key: "offline-only:3:5", cardId: "offline-only", revision: 3, ply: 5, kind: "seen" });
  await rememberTeachingFact({ key: "first-clean:offline-only", cardId: "offline-only", revision: 3, kind: "first-clean" });
  expect(localStorage.getItem("tempo-seen-moves")).toBeNull(); expect(localStorage.getItem("tempo-first-clean-passes")).toBeNull();
  const mounted = await readyHome();
  expect([...useTrainingStore.getState().seenMoves]).toContain("offline-only:3:5");
  mounted.unmount();
  const { closeStudyIntentStorage, initializeStudyIntentStorage } = await import("../../app/lib/study-intent-storage");
  closeStudyIntentStorage(); await initializeStudyIntentStorage();
  await readyHome();
  expect([...useTrainingStore.getState().seenMoves]).toContain("offline-only:3:5");
  expect(useTrainingStore.getState().firstCleanPasses.has("offline-only")).toBe(true);
});
it("pr285_teaching_cross_tab_stale_server_snapshot_cannot_erase_committed_seen_fact", async () => {
  const { rememberTeachingFact } = await import("../../app/lib/study-intent-storage");
  const key = `${card.backendId}:1:0`;
  await rememberTeachingFact({ key, cardId: String(card.backendId), revision: 1, ply: 0, kind: "seen" });
  let release!: (response: Response) => void, entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; }); const response = new Promise<Response>(resolve => { release = resolve; });
  const originalFetch = fetch;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith(`/api/cards/${card.backendId}/teaching`)) { entered(); return response; }
    return originalFetch(input, init);
  }));
  render(<Home />); await started;
  await act(async () => { release(Response.json({ states: [] })); await response; });
  expect([...useTrainingStore.getState().seenMoves]).toContain(key);
});

it("study_priority_non_opening_wrong_move_does_not_request_opening_membership_or_fabricate_evidence", async () => {
  vi.spyOn(local, "usesLocalApi").mockReturnValue(true);
  studyOnlyMode = true;
  const tactic = mapQueueCardToPracticeCard({id:"priority-tactic",revision:1,queue_entry_id:3699,
    start_fen:startFen,moves:["e2e4"],content_type:"tactic",repertoire_id:"unused",repertoire_name:"Tactic",repertoire_source:"Tactic",trained_color:"white"});
  initializeQueueMock.mockImplementation(async()=>{
    useTrainingStore.getState().hydrateLocalQueue([tactic],true);
    useTrainingStore.setState({queueReadiness:"ready"}); return "installed";
  });
  render(<Home />);
  await waitFor(()=>expect(useBoardShellStore.getState().board.onMove).toBeTypeOf("function"));
  expect(useTrainingStore.getState().getCard().kind).toBe("puzzle");
  await play("d2d4");
  expect(vi.mocked(fetch).mock.calls.some(([url])=>String(url).includes('/move-membership?'))).toBe(false);
  expect(responseJournal).not.toHaveBeenCalled();
});

it('study_priority_opening_membership_remains_essential_while_optional_index_is_paused', async () => {
  vi.spyOn(local, 'usesLocalApi').mockReturnValue(true);
  nextLineRead = new Promise(() => {});
  render(<Home />);
  await waitFor(() => expect(lineRequests).toBe(1));
  await waitFor(() => expect(useBoardShellStore.getState().board.onMove).toBeTypeOf('function'));
  vi.mocked(priority.studyInteractionActive).mockReturnValue(true);
  await play('d2d4');
  await waitFor(() => expect(responseJournal).toHaveBeenLastCalledWith(0, 'd2d4', 'alternate'));
  expect(useTrainingStore.getState().isAttemptFailed).toBe(false);
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/move-membership?'))).toBe(true);
});
