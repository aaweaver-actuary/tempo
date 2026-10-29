import type { Locator } from "@playwright/test";
import { test, expect, prepareControlledUI, startFen } from "./fixtures";
import { navigate } from "../browser/ui-fixtures";
import { Chess } from "chess.js";

const afterE4 = new Chess(); afterE4.move("e4");
const study = { id: "appearance-study", title: "Synthetic authored study", description: "Appearance fixture", archived: 0 };
const chapter = { id: "appearance-chapter", study_id: study.id, title: "Synthetic chapter", description: "", position: 0 };
const recommendation = { id: "appearance-rec", snapshot_id: "appearance-snapshot", repertoire_id: "appearance-repertoire", kind: "shared_trunk", decisions_before: 8,
  decisions_after: 4, decisions_avoided: 4, additional_starts: 1, segment_count: 1 };

async function appearance(surfaces: Locator, expectedPieces?: Record<string, string>) {
  const result: Record<string, string> = { ...expectedPieces };
  expect(await surfaces.count()).toBeGreaterThan(0);
  for (const surface of await surfaces.all()) {
    await expect(surface).toHaveClass(/tempo-theme-blue/);
    await expect(surface).toHaveClass(/tempo-pieces-merida/);
    const rendered = await surface.evaluate(node => {
      const css = getComputedStyle(node);
      const board = node.querySelector("cg-board")!.getBoundingClientRect();
      return { light: css.getPropertyValue("--tempo-light").trim(), dark: css.getPropertyValue("--tempo-dark").trim(),
        pieces: [...node.querySelectorAll("piece:not(.ghost)")].map(piece => {
          const style = getComputedStyle(piece), bounds = piece.getBoundingClientRect();
          const id = ["white", "black"].find(color => piece.classList.contains(color)) + ":" +
            ["pawn", "knight", "bishop", "rook", "queen", "king"].find(role => piece.classList.contains(role));
          return { id, image: style.backgroundImage, inside: bounds.left >= board.left - 1 && bounds.top >= board.top - 1 && bounds.right <= board.right + 1 && bounds.bottom <= board.bottom + 1 };
        }) };
    });
    expect(rendered.light).toBe("#dee3e6"); expect(rendered.dark).toBe("#6f8fa8");
    for (const piece of rendered.pieces) {
      expect(piece.image, piece.id).not.toBe("none");
      if (result[piece.id]) expect(piece.image === result[piece.id], `${piece.id} uses the canonical asset`).toBe(true);
      else result[piece.id] = piece.image;
      expect(piece.inside, `${piece.id} stays inside the board`).toBe(true);
    }
  }
  return result;
}

async function seedLook(page: Parameters<typeof prepareControlledUI>[0]) {
  await page.addInitScript(() => {
    localStorage.setItem("tempo-board-theme", "blue"); localStorage.setItem("tempo-piece-set", "merida");
    localStorage.setItem("tempo-move-sound", "false");
  });
}

test("shared_board_appearance_actual_consumers_previews_and_phone_share_colors_and_pieces", async ({ page }, testInfo) => {
  const api = await prepareControlledUI(page, [], false);
  await seedLook(page);
  const get = (path: string, json: unknown, query: Record<string, string> = {}) => api.register({ method: "GET", path, query }, route => route.fulfill({ json }));
  get("/api/defense-exercises/appearance-candidate", { candidate_id: "appearance-candidate", card_id: "appearance-defense", exercise_revision: 1,
    rubric_version: 3, recognition_required: true, proposed_move_uci: "a2a3", proposed_move_san: "a3",
    preview_fen: "4k3/8/8/8/1n6/P7/8/R3K3 b Q - 0 1", fork_move_san: "Nc2+" });
  get(`/api/studies/${study.id}/exercises/appearance-exercise/present`, { id: "appearance-exercise", revision: 1, type: "move_line", mode: "single", prompt: "Appearance exercise", hint: "", fen: startFen });
  get("/api/studies", { studies: [study] }); get(`/api/studies/${study.id}`, { study, chapters: [chapter] });
  get(`/api/studies/${study.id}/chapters/${chapter.id}`, { chapter,
    sources: [{ id: "appearance-source", filename: "Synthetic.pgn", record_index: 0, valid: 1, diagnostics_json: "[]", version: 1, source_group_id: "appearance-source" }],
    positions: [{ id: "appearance-position", source_id: "appearance-source", parent_id: null, child_index: 0, fen: startFen, move_uci: null,
      comment: "", starting_comment: "", nags_json: "[]", arrows_json: "[]", squares_json: "[]", valid: 1 }], exercises: [], links: [] });
  const prefix = { moves_uci: [], san: "", ending_fen: startFen, revision: 0 };
  get("/api/repertoires/appearance-repertoire/canonical-prefix", prefix);
  api.register({ method: "POST", path: "/api/repertoires/appearance-repertoire/canonical-prefix/preview", query: {}, validate: request => {
    expect(request.body).toEqual({ movetext: "" });
  } }, route => route.fulfill({ json: { ...prefix, preview_id: "appearance-prefix", state: "checking" } }));
  get("/api/repertoires/appearance-repertoire/canonical-prefix/preview/appearance-prefix", { ...prefix, preview_id: "appearance-prefix", state: "ready", error: null, suggestion: null, conflicts: [], conflict_count: 0, next_cursor: null });
  get("/api/repertoires/appearance-repertoire/segmentation", { version: 1, preview_only: true, state: "ready", content_version: 1, graph_generation: 1,
    error: null, invalidated_pins: 0, recommendations: [recommendation] });
  get("/api/repertoires/appearance-repertoire/segmentation/appearance-rec", { version: 1, preview_only: true, content_version: 1, graph_generation: 1,
    source_fingerprint: "appearance-sources", snapshot_id: "appearance-snapshot", recommendation, rationale: "Synthetic shared opening", estimate_basis: "structural count",
    routes: [{ id: "appearance-route", name: "Synthetic route" }], next_route: null, next_segment: null,
    segments: [{ id: "appearance-segment", role: "shared_trunk", starting_fen: startFen, ending_fen: afterE4.fen(), moves: ["e2e4"], tested_decisions: 1, trained_color: "white" }] }, { snapshot_id: "appearance-snapshot" });
  get("/api/repertoires/appearance-repertoire/integrity", { repertoire_id: "appearance-repertoire", status: "needs_repair", issue_count: 1, first_issue_id: "appearance-issue",
    scan_status: "idle", scan_generation: null, scan_progress: { completed: 1, total: 1 }, last_scan_error: null,
    issues: [{ id: "appearance-issue", kind: "multiple_responses", fen_key: "appearance-position", fen: startFen, trained_color: "white", signature: "appearance-signature",
      moves: [{ uci: "e2e4", line_count: 1, card_count: 0, review_count: 0 }, { uci: "d2d4", line_count: 1, card_count: 0, review_count: 0 }], sources: [] }] });
  get("/api/games/position-summary", { moves: [] }, { fen: startFen });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto("/board-appearance-proof.html");
  const surfaces = page.locator("#appearance-consumer-root .tempo-board-surface:visible");
  await expect(surfaces).toHaveCount(1);
  const canonical = await appearance(surfaces);
  expect(Object.keys(canonical)).toHaveLength(12);
  for (const name of ["opening", "defense", "exercise", "tactics", "endgames", "studies", "card-editor", "capture", "tree", "canonical", "segmentation", "integrity", "games", "repertoire"]) {
    await test.step(name, async () => {
      await page.locator("#appearance-consumer").selectOption(name);
      if (name === "capture") await page.getByLabel("FEN", { exact: true }).fill(startFen);
      if (name === "segmentation") await page.getByRole("button", { name: "Preview shared opening" }).click();
      if (name === "games") await page.getByRole("button", { name: /Lichess · synthetic-game/ }).click();
      if (name === "studies") {
        await page.getByRole("combobox", { name: "Study", exact: true }).selectOption(study.id);
        await expect(page.getByRole("combobox", { name: "Chapter", exact: true })).toHaveValue(chapter.id);
      }
      await expect(surfaces.first()).toBeVisible();
      await expect(page.locator("#appearance-consumer-root .tempo-board-surface:visible piece:not(.ghost)").first()).toBeVisible();
      await expect(page.locator("#appearance-consumer-root piece.anim")).toHaveCount(0);
      await appearance(surfaces, canonical);
      if (name === "repertoire") {
        await page.getByRole("button", { name: "Choose a2a3" }).click(); await appearance(surfaces, canonical);
        for (const tab of ["Missing replies", "Lines & positions"]) { await page.getByRole("tab", { name: tab }).click(); await appearance(surfaces, canonical); }
      }
    });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  for (const name of ["opening", "capture", "segmentation", "games"]) {
    await page.locator("#appearance-consumer").selectOption(name);
    if (name === "capture") await page.getByLabel("FEN", { exact: true }).fill(startFen);
    if (name === "segmentation") await page.getByRole("button", { name: "Preview shared opening" }).click();
    if (name === "games") await page.getByRole("button", { name: /Lichess · synthetic-game/ }).click();
    await expect(surfaces.first()).toBeVisible();
    await appearance(surfaces, canonical);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`shared-board-${name}-390.png`), fullPage: true });
  }
});

test("shared_board_appearance_real_workspace_navigation_reload_and_held_input_preserve_the_board", async ({ page }) => {
  await seedLook(page); const api = await prepareControlledUI(page);
  api.register({ method: "GET", path: "/api/endgames/templates", query: {} }, route => route.fulfill({ json: { templates: [] } }));
  const surfaces = page.locator(".tempo-board-surface:visible");
  await expect(surfaces.first()).toBeVisible(); const canonical = await appearance(surfaces);
  for (const tab of ["Tactics", "Endgames", "Train"]) { await navigate(page, tab); await expect(surfaces.first()).toBeVisible(); await appearance(surfaces, canonical); }
  await page.reload(); await expect(surfaces.first()).toBeVisible(); await appearance(surfaces, canonical);
  await page.goto("/board-appearance-proof.html");
  await page.locator("#appearance-consumer").selectOption("opening");
  const board = page.locator("#appearance-consumer-root .cg-wrap");
  await expect(board).toBeVisible();
  const bounds = (await board.boundingBox())!;
  const start = { x: bounds.x + bounds.width * 4.5 / 8, y: bounds.y + bounds.height * 6.5 / 8 };
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(start.x + 12, start.y - bounds.height / 8, { steps: 6 });
  await expect(board.locator("piece.dragging")).toHaveCount(1);
  await page.evaluate(() => (document.getElementById("fixture-change-look") as HTMLButtonElement).click());
  await expect(page.locator(".tempo-theme-green.tempo-pieces-cburnett")).toBeVisible();
  await expect(board.locator("piece.dragging")).toHaveCount(1);
  await page.mouse.move(start.x, bounds.y + bounds.height * 4.5 / 8, { steps: 6 }); await page.mouse.up();
  await expect(page.locator("#fixture-move-count")).toHaveText("1");
});
