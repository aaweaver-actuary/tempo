import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { STANDARD_FEN } from "../../app/const";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { clearNotificationHistory, notifications, notificationToasts } from "../../app/lib/notifications";
import { enqueuePendingDiscoveryAdmission, flushPendingDiscoveryAdmissions, pendingDiscoveryAdmissions,
  recoverUnacknowledgedDiscoveryAdmissions } from "../../app/lib/discovery-admission-outbox";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div data-testid="board" /> }));
beforeEach(() => { localStorage.clear(); clearNotificationHistory(); useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  useTrainingStore.setState({ cardsLeft: 1, currentFenString: asFenString(STANDARD_FEN) }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("phone accepted discovery reload confirms the original intent without readmission", async () => {
  enqueuePendingDiscoveryAdmission({ opportunityId: "gap", selectedMoveUci: "f3e5", evidenceFingerprint: "revision" });
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ intent_id: "original-intent" }))
    .mockResolvedValueOnce(Response.json({ state: "queued" }));
  vi.stubGlobal("fetch", fetcher);
  await flushPendingDiscoveryAdmissions();
  const accepted = pendingDiscoveryAdmissions()[0];
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toEqual(accepted);
  await flushPendingDiscoveryAdmissions();
  expect(fetcher.mock.calls[1][0]).toContain("/api/discovery-admissions/original-intent");
  expect(fetcher.mock.calls[1][1]?.method).not.toBe("POST");
  expect(pendingDiscoveryAdmissions()).toEqual([]);
});

it("phone pending review uses inline confirmation status and no error popup", () => {
  const retry = vi.fn();
  const props = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1,
    card: { id: asCardId("pending-save"), kind: "opening" as const, title: "Prep", subtitle: "",
      startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const },
    boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
    handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), onMove: vi.fn(), useSharedBoard: true,
    reviewPersistenceIdentity: { backendId: "card", queueEntryId: 42, attemptId: "original-attempt" }, retryReviewSave: retry };
  const mounted = render(<TrainingView {...props} reviewPersistenceState="saving" />);
  // Pending is a separate persistence state, not a confirmed failure.
  mounted.rerender(<TrainingView {...props} reviewPersistenceState="pendingConfirmation" />);
  expect(screen.getByText("Waiting for the computer to confirm this result.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Check save" })); expect(retry).toHaveBeenCalledOnce();
  expect(notificationToasts()).toEqual([]);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0]).toMatchObject({ severity: "info", resolvedAt: null });
  mounted.rerender(<TrainingView {...props} reviewPersistenceState="saved" />);
  expect(notifications()[0].resolvedAt).not.toBeNull();
});

it("phone discovery projection does not count one stored failure as repeated incidents", async () => {
  const { reportDiscoveryAdmissionErrors } = await import("../../app/lib/discovery-admission-outbox");
  const failed = { opportunityId: "stale", selectedMoveUci: "f3e5", evidenceFingerprint: "revision",
    operationId: "original", state: "failed" as const, error: "Active discovery not found" };
  for (let projection = 0; projection < 10; projection++) reportDiscoveryAdmissionErrors([failed]);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0]).toMatchObject({ occurrenceCount: 1, details: { error: "Active discovery not found", operationId: "original" } });
});

it("phone stale discovery failure with retained intent repairs by confirming that intent", () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{ opportunityId: "stale",
    selectedMoveUci: "f3e5", evidenceFingerprint: "revision", operationId: "original", intentId: "accepted-intent",
    state: "failed", error: "Active discovery not found" }]));
  recoverUnacknowledgedDiscoveryAdmissions();
  expect(pendingDiscoveryAdmissions()[0]).toMatchObject({ operationId: "original", intentId: "accepted-intent", state: "accepted" });
});

it("phone review diagnostics retain raw errors without publishing a second incident", async () => {
  const { clearDebugErrors, debugErrors, reportDebugError } = await import("../../app/lib/debug-reporting");
  clearDebugErrors();
  reportDebugError(new Error("Save is still pending (operation review-attempt:original-attempt)."),
    { source: "training-review-replay", attemptId: "original-attempt", notify: false });
  expect(debugErrors()[0]).toMatchObject({ message: expect.stringContaining("review-attempt:original-attempt"), context: { attemptId: "original-attempt" } });
  expect(notifications()).toEqual([]);
});

it("phone passive queue refresh leaves review delivery to bounded receipt recovery", async () => {
  const { fetchAndInitializeQueue } = await import("../../app/views/fetchAndInitializeQueue");
  const { enqueuePendingReview, pendingReviews } = await import("../../app/lib/review-outbox");
  enqueuePendingReview({ backendId: "retained", queueEntryId: 81, attemptId: "passive", outcome: "correct", guided: false });
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ cards: [], count: 0 }));
  vi.stubGlobal("fetch", fetcher);
  await fetchAndInitializeQueue(false, { preparePhoneQueue: false, replaySavedReviews: false });
  expect(fetcher.mock.calls.every(call => !String(call[0]).includes("/review") && !String(call[0]).includes("/operations/"))).toBe(true);
  expect(pendingReviews()).toHaveLength(1);
});
