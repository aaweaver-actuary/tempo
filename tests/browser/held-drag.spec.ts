import { test, expect } from "./observability";
import { heldDrag, prepareHeldDrag, dragSnapshot, heldDragStartFen } from "./held-drag-fixtures";

test("held_drag_detector_reports_mid_drag_board_cancellation", async ({ page }, testInfo) => {
  await prepareHeldDrag(page);
  const normal = await heldDrag(page);
  expect(normal.probe.sawDragging).toBe(true);
  expect(normal.probe.interrupted).toBe(false);
  expect(normal.snapshot?.sessions.at(-1)?.endReason).toBeNull();
  expect((await dragSnapshot(page))?.sessions.at(-1)?.endReason).toBe("drop");
  await page.reload();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", heldDragStartFen);
  const cancelled = await heldDrag(page, async step => {
    if (step === 12) await page.keyboard.press("f");
  });
  await testInfo.attach("held-drag-cancellation", { body: JSON.stringify(cancelled, null, 2), contentType: "application/json" });
  expect(cancelled.probe.interrupted).toBe(true);
  expect(cancelled.snapshot?.sessions.at(-1)?.endReason).toBe("board-interruption");
  expect(cancelled.snapshot?.sessions.at(-1)?.boardEvents.some(event => event.kind === "cancel-move" && event.changed.includes("orientation"))).toBe(true);
  expect(cancelled.snapshot?.sessions.at(-1)?.events.some(event => event.type.endsWith("up"))).toBe(false);
});

test("held_drag_training_phases_separate_drop_reply_review_and_readiness", async ({ page }, testInfo) => {
  const { prepareVisualUI } = await import("./visual-fixtures");
  const cards = [
    { id: "held-phase-first", queue_entry_id: 31, start_fen: heldDragStartFen,
      moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening" as const,
      repertoire_name: "Held phase first", repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const },
    { id: "held-phase-next", queue_entry_id: 32, start_fen: heldDragStartFen,
      moves: ["d2d4"], content_type: "opening" as const,
      repertoire_name: "Held phase next", repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const },
  ];
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page, false, cards);
  await page.goto("/?tempoPerformance=drag");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  await heldDrag(page);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", /4p3\/4P3/);
  await page.getByRole("button", { name: "Correct", exact: true }).click();
  await expect(page.getByText("Held phase next", { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const snapshot = await dragSnapshot(page);
    return ["drop-handling", "opponent-reply", "review-persistence", "next-card-readiness"].every(operation =>
      snapshot?.sessions.flatMap(session => session.phases).some(phase => phase.operation === operation && phase.edge === "end"));
  }).toBe(true);
  const snapshot = await dragSnapshot(page);
  await testInfo.attach("held-drag-training-phases", { body: JSON.stringify(snapshot, null, 2), contentType: "application/json" });
  const phases = snapshot!.sessions.flatMap(session => session.phases);
  for (const operation of ["drop-handling", "opponent-reply", "review-persistence", "next-card-readiness"]) {
    const start = phases.find(phase => phase.operation === operation && phase.edge === "start")!;
    expect(phases.some(phase => phase.operationId === start.operationId && phase.edge === "end")).toBe(true);
  }
});
