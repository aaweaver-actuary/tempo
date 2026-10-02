import type { Page } from "@playwright/test";
import { prepareVisualUI } from "./visual-fixtures";

export const repairStartFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
export async function prepareRepairUI(page: Page) {
  await prepareVisualUI(page, true, [
    { id: "study-one", queue_entry_id: 11, start_fen: repairStartFen, moves: ["e2e4"], content_type: "opening",
      repertoire_name: "First study card", repertoire_source: "study.pgn", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" },
    { id: "study-two", queue_entry_id: 12, start_fen: repairStartFen, moves: ["d2d4","d7d5","c2c4"], content_type: "opening",
      repertoire_name: "Second study card", repertoire_source: "study.pgn", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" },
  ]);
  const issue = { id: "repair-issue", signature: "signature", kind: "multiple_responses", fen: repairStartFen,
    fen_key: repairStartFen.split(" ").slice(0,4).join(" "), trained_color: "white", sources: [],
    moves: [{ uci: "e2e4", line_count: 1, card_count: 1, review_count: 3 },
      { uci: "d2d4", line_count: 1, card_count: 1, review_count: 2 }] };
  const admission = { repertoire_id: "repair-repertoire", issue_id: issue.id, task_id: "repair-graph", task_generation: 2, state: "queued" };
  const control = { confirmed: false, accepted: false, operationIds: [] as string[], receiptIds: [] as string[] };
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{ id: "repair-repertoire",
    name: "Repair repertoire", source_name: "repair.pgn", trained_color: "white", new_cards_per_day: null,
    effective_new_cards_per_day: 10, line_count: 2, card_count: 3, active_prefix_count: 3, due_count: 2,
    integrity_status: control.confirmed ? "clean" : "needs_repair", integrity_issue_count: control.confirmed ? 0 : 1,
    blocked_due_count: control.confirmed ? 0 : 1 }] } }));
  await page.route("**/api/repertoires/repair-repertoire/integrity", route => route.fulfill({ json: {
    repertoire_id: "repair-repertoire", status: "needs_repair", issue_count: 1, first_issue_id: issue.id,
    scan_status: "idle", scan_generation: "scan:1", scan_progress: { completed: 2, total: 2 }, last_scan_error: null, issues: [issue],
  } }));
  await page.route("**/api/repertoires/repair-repertoire/integrity/issues/repair-issue/recommendations**", route => route.fulfill({ json: {
    state: "ready", repertoire_id: "repair-repertoire", issue_id: issue.id, signature: issue.signature,
    starting_fen: repairStartFen, trained_color: "white", route_start_fen: repairStartFen, route_uci: [],
    suggested_move_uci: "e2e4", suggestion_reason: "Exact transposition to a familiar repertoire line", reason: null,
    engine_lines: [{ move_uci: "e2e4", score: { cp: 25, mate: null }, loss_cp: 0, depth: 14 }],
    candidates: [{ move_uci: "e2e4", score: { cp: 25, mate: null }, loss_cp: 0, similarity: "exact transposition",
      repertoire_line_count: 1, exact_transposition: true, example_line_id: "one", example_line_name: "Main line",
      engine_version: "stockfish", network_version: "nnue", depth: 14, report_id: "report", source_type: "line",
      source_id: "one", source_ply: 0, preview_moves_uci: ["e2e4","e7e5","g1f3"] }],
  } }));
  await page.route("**/api/games/position-summary?**", route => route.fulfill({ json: { moves: [] } }));
  await page.route("**/api/operations/*", route => {
    control.receiptIds.push(new URL(route.request().url()).pathname.split("/").at(-1)!);
    return route.fulfill({ json: control.accepted ? { state: "complete", response: admission } : { state: "unknown" } });
  });
  await page.route("**/api/repertoires/repair-repertoire/integrity/issues/repair-issue/resolve", route => {
    control.operationIds.push(route.request().headers()["idempotency-key"]);
    control.accepted = true;
    // The edit succeeds but its response is lost; receipt recovery must win after reload.
    return route.abort("failed");
  });
  await page.route("**/api/repertoires/repair-repertoire/integrity/repairs/repair-graph?**", route => route.fulfill({ json: {
    task_id: "repair-graph", task_generation: 3, state: control.confirmed ? "complete" : "waiting",
    issue_count: control.confirmed ? 0 : 1, reason: null, retry_task_id: null,
  } }));
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  return control;
}
