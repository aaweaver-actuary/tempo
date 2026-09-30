import { expect, type Page } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";
import type { tempoDragDiagnostics } from "../../app/lib/performance";

export type DragSnapshot = ReturnType<typeof tempoDragDiagnostics>;
export type DragProbe = {
  samples: Array<{ atMs: number; callbackAtMs: number; dragging: boolean; gapMs: number | null; rAFGapMs: number | null; displacementCssPx: number | null }>;
  startedAtMs: number; endedAtMs: number; sawDragging: boolean; interrupted: boolean;
};
export const heldDragFixtureVersion = "held-drag-v3-callback-cadence-blocked-sw";
export const heldDragStartFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

export async function prepareHeldDrag(page: Page, enabled = true, beforeNavigate?: () => Promise<void>) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page, false);
  await beforeNavigate?.();
  await page.goto(enabled ? "/?tempoPerformance=drag" : "/");
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  await expect(page.locator("cg-board piece.white.pawn").first()).toBeVisible();
}

export async function dragSnapshot(page: Page): Promise<DragSnapshot | null> {
  return page.evaluate(() => {
    const capture = Reflect.get(window, "tempoPerformance") as { snapshot: () => DragSnapshot } | undefined;
    return capture?.snapshot() ?? null;
  });
}

// A separate bounded DOM probe validates continuity even when app instrumentation
// is disabled. It reads geometry once and transforms per frame, never per move.
export async function heldDrag(page: Page, during?: (step: number) => Promise<void>, release = true) {
  const bounds = (await page.locator("cg-board").boundingBox())!;
  const orientation = await page.locator(".board-frame").getAttribute("data-orientation");
  const white = orientation === "white";
  const origin = { x: bounds.x + (white ? 4.5 : 3.5) * bounds.width / 8, y: bounds.y + (white ? 6.5 : 1.5) * bounds.height / 8 };
  const target = { x: origin.x, y: bounds.y + (white ? 4.5 : 3.5) * bounds.height / 8 };
  await page.evaluate(({ geometry }) => {
    const probe: DragProbe = { samples: [], startedAtMs: performance.now(), endedAtMs: 0, sawDragging: false, interrupted: false };
    let pointer: { x: number; y: number } | undefined;
    let stopped = false;
    let frame = 0;
    const move = (event: PointerEvent) => { pointer = { x: event.clientX, y: event.clientY }; };
    document.addEventListener("pointermove", move);
    const sample = (atMs: number) => {
      if (stopped) return;
      const callbackAtMs = performance.now();
      const piece = document.querySelector<HTMLElement>("cg-board piece.dragging:not(.ghost)");
      if (piece) probe.sawDragging = true;
      else if (probe.sawDragging) probe.interrupted = true;
      const transform = piece?.style.transform.match(/^translate\(\s*(-?[\d.]+)px,\s*(-?[\d.]+)px\s*\)$/);
      const gapMs = probe.samples.length ? callbackAtMs - probe.samples.at(-1)!.callbackAtMs : null;
      const rAFGapMs = probe.samples.length ? atMs - probe.samples.at(-1)!.atMs : null;
      const displacementCssPx = transform && pointer ? Math.hypot(
        geometry.x + Number(transform[1]) + geometry.width / 16 - pointer.x,
        geometry.y + Number(transform[2]) + geometry.height / 16 - pointer.y,
      ) : null;
      if (probe.samples.length < 512) probe.samples.push({ atMs, callbackAtMs, dragging: !!piece, gapMs, rAFGapMs, displacementCssPx });
      frame = requestAnimationFrame(sample);
    };
    frame = requestAnimationFrame(sample);
    Object.assign(window, { tempoHeldProbe: probe, tempoStopHeldProbe: () => {
      stopped = true; cancelAnimationFrame(frame); document.removeEventListener("pointermove", move);
      probe.endedAtMs = performance.now(); return probe;
    } });
  }, { geometry: bounds });
  try {
    await page.mouse.move(origin.x + 3, origin.y + 2);
    await page.mouse.down();
    // Cross Chessground's drag threshold before observing interventions.
    await page.mouse.move(origin.x + 12, origin.y - 12);
    await expect(page.locator("cg-board piece.dragging:not(.ghost)")).toHaveCount(1);
    for (let step = 0; step < 40; step++) {
      const progress = (step + 1) / 40;
      await page.mouse.move(origin.x + Math.sin(progress * Math.PI * 4) * bounds.width / 16,
        origin.y + (target.y - origin.y) * progress);
      await page.waitForTimeout(20);
      await during?.(step);
    }
    // Sample while still held. Never infer success from the drop destination.
    await page.waitForTimeout(60);
    const probe = await page.evaluate(() => (Reflect.get(window, "tempoStopHeldProbe") as () => DragProbe)());
    return { probe, snapshot: await dragSnapshot(page) };
  } finally {
    await page.evaluate(() => (Reflect.get(window, "tempoStopHeldProbe") as (() => DragProbe) | undefined)?.()).catch(() => {});
    if (release) await page.mouse.up();
  }
}

export async function installHeldDiscovery(page: Page) {
  const discoveries = [{ id: "held-preview", repertoire_id: "visual-repertoire", kind: "missing_response", status: "active",
    fen_key: heldDragStartFen.split(" ").slice(0, 4).join(" "), fen: heldDragStartFen,
    decision_fen: heldDragStartFen, decision_start_fen: heldDragStartFen, decision_route_uci: [],
    accepted_moves_uci: [], card_id: null, opponent_move_uci: null, trained_color: "white", score: 1,
    evidence: { supporting_games: 4 }, evidence_fingerprint: "held-v1", seen_at: "2026-09-18T00:00:00Z",
    snoozed_until: null, admission_state: null, admitted_card_id: null, unread: false, source_games: [], routes: [],
    created_at: "2026-09-18T00:00:00Z", updated_at: "2026-09-18T00:00:00Z" }];
  let release: (() => void) | undefined;
  let started = false;
  let completed = false;
  const responseHold = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
    discoveries, total: 1, next_offset: null, unread_count: 0,
  } }));
  await page.route("**/api/discoveries/*/recommendations", async route => {
    started = true;
    await responseHold;
    await route.fulfill({ json: { state: "ready", opportunity_id: "held-preview", repertoire_id: "visual-repertoire",
      evidence_fingerprint: "held-v1", starting_fen: heldDragStartFen, accepted_moves_uci: [],
      candidates: [{ move_uci: "e2e4", score: { cp: 25, mate: null }, loss_cp: 0, similarity: "same",
        repertoire_line_count: 1, exact_transposition: false, example_line_id: "held-line", example_line_name: "Held line",
        preview_moves_uci: ["e2e4", "e7e5", "g1f3"], engine_version: "fixture", network_version: "fixture",
        depth: 12, report_id: "held-report", source_game_id: "held-game", source_ply: 0 }] } });
    completed = true;
  });
  return { release: () => release?.(), started: () => started, completed: () => completed };
}
