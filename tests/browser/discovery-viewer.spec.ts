import { test, expect, prepareUI, noPageOverflow, navigate } from "./ui-fixtures";

async function openDiscoveries(page: import("@playwright/test").Page) {
  const trigger = page.getByRole("button", { name: "Discoveries", exact: true });
  if (await trigger.getAttribute("aria-expanded") !== "true") await trigger.click();
}

const beforeReply = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const decisionFen = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2";
const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const discoveryFixture = (id: string, fingerprint = `${id}-revision`, cardId: string | null = null) => ({
  id, repertoire_id: "rep", kind: "post_gap_weakness" as const,
  status: "active" as const, fen_key: decisionFen.split(" ").slice(0, 4).join(" "),
  fen: beforeReply, decision_fen: decisionFen, decision_start_fen: startFen,
  decision_route_uci: ["e2e4", "c7c5"], accepted_moves_uci: [], card_id: cardId,
  opponent_move_uci: "c7c5", trained_color: "white" as const, score: 1,
  evidence: { supporting_games: 4 }, evidence_fingerprint: fingerprint,
  seen_at: "2026-09-24T00:00:00Z", snoozed_until: null, admission_state: null,
  admitted_card_id: null, unread: false, source_games: [], routes: ["e4 c5"],
  created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
});
const discoveryFeed = (items: ReturnType<typeof discoveryFixture>[]) => ({
  discoveries: items, total: items.length, next_offset: null, unread_count: 0,
});
const readyPreview = (id: string, fingerprint = `${id}-revision`) => ({
  state: "ready", opportunity_id: id, evidence_fingerprint: fingerprint,
  starting_fen: decisionFen, suggested_move_uci: "g1f3", candidates: [{
    move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0,
    similarity: "no supported similarity", repertoire_line_count: 0,
    exact_transposition: false, example_line_id: null, example_line_name: null,
    preview_moves_uci: ["g1f3"], engine_version: "Stockfish", network_version: "NNUE",
    depth: 14, report_id: "a".repeat(64), source_game_id: "game", source_ply: 2,
  }],
});

test("inactive discovery preview refreshes once without a retry notification and another discovery loads", async ({ page }) => {
  await page.clock.install();
  let feedReads = 0;
  let inactivePreviewReads = 0;
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed(
    ++feedReads === 1 ? [discoveryFixture("inactive"), discoveryFixture("valid")] : [discoveryFixture("valid")],
  ) }));
  await page.route("**/api/discoveries/inactive/recommendations", route => {
    inactivePreviewReads++;
    return route.fulfill({ status: 404, json: { detail: "'Active discovery not found'" } });
  });
  await page.route("**/api/discoveries/valid/recommendations", route => route.fulfill({ json: readyPreview("valid") }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  await expect.poll(() => feedReads).toBeGreaterThanOrEqual(2);
  await page.clock.fastForward(31_000);
  expect(inactivePreviewReads).toBe(1);
  await viewer.getByRole("button", { name: "Back to work" }).click();
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.locator("#notification-tray").getByText(/Active discovery not found/)).toHaveCount(0);
});

test("same-key inactive preview recovers after the retry delay without an error notification", async ({ page }) => {
  await page.clock.install();
  let previewReads = 0;
  let previewReady = false;
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("recovering"),
  ]) }));
  await page.route("**/api/discoveries/recovering/recommendations", route => {
    previewReads++;
    return previewReady
      ? route.fulfill({ json: readyPreview("recovering") })
      : route.fulfill({ status: 404, json: { detail: "'Active discovery not found'" } });
  });
  await prepareUI(page);
  await openDiscoveries(page);
  await expect.poll(() => previewReads).toBe(1);
  previewReady = true;
  await page.clock.fastForward(31_000);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  expect(previewReads).toBe(2);
  await viewer.getByRole("button", { name: "Back to work" }).click();
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.locator("#notification-tray").getByText(/Active discovery not found/)).toHaveCount(0);
});

test("repeated same-key inactive 404 previews stay bounded and silent", async ({ page }) => {
  await page.clock.install();
  let previewReads = 0;
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("still-inactive"),
  ]) }));
  await page.route("**/api/discoveries/still-inactive/recommendations", route => {
    previewReads++;
    return route.fulfill({ status: 404, json: { detail: "'Active discovery not found'" } });
  });
  await prepareUI(page);
  await openDiscoveries(page);
  await expect.poll(() => previewReads).toBe(1);
  await page.clock.fastForward(29_000);
  expect(previewReads).toBe(1);
  await page.clock.fastForward(35_000);
  await expect.poll(() => previewReads).toBeGreaterThanOrEqual(2);
  expect(previewReads).toBeLessThanOrEqual(3);
  await page.getByRole("button", { name: "Back to work", exact: true }).click();
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.locator("#notification-tray").getByText(/Active discovery not found/)).toHaveCount(0);
});

test("refresh selects the remaining review item when the first feed item is waiting", async ({ page }) => {
  await page.clock.install();
  let refreshed = false;
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed(refreshed
    ? [discoveryFixture("waiting"), discoveryFixture("B", "B-revision", "card-B")]
    : [discoveryFixture("A", "A-revision", "card-A"), discoveryFixture("B", "B-revision", "card-B")],
  ) }));
  await page.route("**/api/discoveries/waiting/recommendations", route => route.fulfill({ json: {
    state: "waiting", opportunity_id: "waiting", candidates: [], reason: "Preparing preview",
  } }));
  await page.route("**/api/repertoires/rep/opportunities/*/training-eligibility", route =>
    route.fulfill({ json: { eligible: true, reason: null } }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 2 · white to move")).toBeVisible();
  refreshed = true;
  await page.clock.fastForward(31_000);
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Train this decision" })).toBeEnabled();
  await expect(viewer.getByRole("button", { name: "Previous", exact: true })).toBeDisabled();
  await expect(viewer.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
});

for (const failure of [
  { name: "unrelated 404", status: 404, body: { detail: "Recommendation unavailable" }, message: /Recommendation unavailable/ },
  { name: "server error", status: 503, body: { detail: "Preview service unavailable" }, message: /Preview service unavailable/ },
  { name: "invalid schema", status: 200, body: { state: "ready" }, message: /Invalid continuation preview data/ },
] as const) {
  test(`genuine discovery preview ${failure.name} remains observable`, async ({ page }) => {
    await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
      discoveryFixture("failure"), discoveryFixture("valid"),
    ]) }));
    await page.route("**/api/discoveries/failure/recommendations", route =>
      route.fulfill({ status: failure.status, json: failure.body }));
    await page.route("**/api/discoveries/valid/recommendations", route => route.fulfill({ json: readyPreview("valid") }));
    await prepareUI(page);
    await openDiscoveries(page);
    const viewer = page.getByRole("dialog", { name: "Discoveries" });
    await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
    await viewer.getByRole("button", { name: "Back to work" }).click();
    await page.getByRole("button", { name: "Notifications" }).click();
    await expect(page.locator("#notification-tray p").filter({ hasText: failure.message }).first()).toBeVisible();
  });
}

test("genuine discovery preview network failure remains observable", async ({ page }) => {
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("failure"), discoveryFixture("valid"),
  ]) }));
  await page.route("**/api/discoveries/failure/recommendations", route => route.abort("failed"));
  await page.route("**/api/discoveries/valid/recommendations", route => route.fulfill({ json: readyPreview("valid") }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  await viewer.getByRole("button", { name: "Back to work" }).click();
  await page.getByRole("button", { name: "Notifications" }).click();
  await expect(page.locator("#notification-tray p").filter({ hasText: /Failed to fetch/ }).first()).toBeVisible();
});

test("late obsolete preview cannot replace a newer evidence version", async ({ page }) => {
  await page.clock.install();
  let feedReads = 0;
  let oldPreviewReads = 0;
  let releaseOldPreview: (() => void) | undefined;
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("same", ++feedReads <= 2 ? "old" : "new"),
  ]) }));
  await page.route("**/api/discoveries/same/recommendations", route => {
    if (++oldPreviewReads === 1) return route.fulfill({ json: {
      state: "waiting", opportunity_id: "same", evidence_fingerprint: "old",
      candidates: [], reason: "Preparing preview",
    } });
    if (!releaseOldPreview) return new Promise<void>(resolve => {
      releaseOldPreview = () => { void route.fulfill({ json: readyPreview("same", "old") }).then(resolve); };
    });
    return route.fulfill({ json: readyPreview("same", "new") });
  });
  await prepareUI(page);
  await openDiscoveries(page);
  await expect.poll(() => oldPreviewReads).toBe(1);
  await page.clock.fastForward(33_000);
  await expect.poll(() => Boolean(releaseOldPreview)).toBe(true);
  await page.clock.fastForward(30_100);
  await expect.poll(() => feedReads).toBeGreaterThanOrEqual(3);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  releaseOldPreview?.();
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  await expect(viewer.getByText("Preparing review-ready discoveries")).toHaveCount(0);
});

test("late removed preview cannot be reused when the discovery returns", async ({ page }) => {
  await page.clock.install();
  let feedReads = 0;
  let previewReads = 0;
  let releaseRemovedPreview: (() => void) | undefined;
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed(
    ++feedReads === 2 ? [discoveryFixture("valid")] : [discoveryFixture("removed"), discoveryFixture("valid")],
  ) }));
  await page.route("**/api/discoveries/removed/recommendations", route => {
    if (++previewReads === 1) return new Promise<void>(resolve => {
      releaseRemovedPreview = () => { void route.fulfill({ json: {
        state: "unavailable", opportunity_id: "removed", candidates: [], reason: "Old response",
      } }).then(resolve); };
    });
    return route.fulfill({ json: readyPreview("removed") });
  });
  await page.route("**/api/discoveries/valid/recommendations", route => route.fulfill({ json: readyPreview("valid") }));
  await prepareUI(page);
  await openDiscoveries(page);
  await expect.poll(() => Boolean(releaseRemovedPreview)).toBe(true);
  const tray = page.locator(".tempo-discoveries-tray");
  await expect(tray).toHaveAttribute("data-discovery-count", "2");
  const removedFeed = page.waitForResponse(response => response.url().includes("/api/discoveries?") && response.status() === 200);
  await page.clock.fastForward(30_100);
  await (await removedFeed).finished();
  await expect(tray).toHaveAttribute("data-discovery-count", "1");
  const returnedFeed = page.waitForResponse(response => response.url().includes("/api/discoveries?") && response.status() === 200);
  await page.clock.fastForward(30_100);
  await (await returnedFeed).finished();
  await expect(tray).toHaveAttribute("data-discovery-count", "2");
  releaseRemovedPreview?.();
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("2 of 2 · white to move")).toBeVisible();
  await viewer.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(viewer.getByText("1 of 2 · white to move")).toBeVisible();
  expect(previewReads).toBeGreaterThanOrEqual(2);
});

test("unsupported saved discovery explains Builder route without sending train", async ({ page }) => {
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("unsupported", "revision", "prefix-card"),
  ]) }));
  let trainPosts = 0;
  await page.route("**/api/repertoires/rep/opportunities/unsupported/train", route => {
    trainPosts++;
    return route.fulfill({ status: 409, json: { detail: "The target decision cannot be isolated from this prefix card" } });
  });
  await page.route("**/api/repertoires/rep/opportunities/unsupported/training-eligibility", route =>
    route.fulfill({ json: { eligible: false, reason: "The target decision cannot be isolated from this prefix card" } }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText(/target decision cannot be isolated/)).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Train this decision" })).toBeDisabled();
  await expect(viewer.getByRole("button", { name: "Open in Builder" })).toBeEnabled();
  expect(trainPosts).toBe(0);
});

test("supported saved discovery checks eligibility before direct training", async ({ page }) => {
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("supported", "revision", "response-card"),
  ]) }));
  let eligibilityReads = 0;
  let trainPosts = 0;
  await page.route("**/api/repertoires/rep/opportunities/supported/training-eligibility", route => {
    eligibilityReads++;
    return route.fulfill({ json: { eligible: true, reason: null } });
  });
  await page.route("**/api/repertoires/rep/opportunities/supported/train", route => {
    trainPosts++;
    return route.fulfill({ json: { card_id: "response-card", queued: true, idempotent: false } });
  });
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByRole("button", { name: "Train this decision" })).toBeEnabled();
  await viewer.getByRole("button", { name: "Train this decision" }).click();
  await expect.poll(() => trainPosts).toBe(1);
  expect(eligibilityReads).toBeGreaterThanOrEqual(2);
});

test("genuine training eligibility failure remains visible and blocks direct training", async ({ page }) => {
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: discoveryFeed([
    discoveryFixture("failure", "revision", "response-card"),
  ]) }));
  let trainPosts = 0;
  await page.route("**/api/repertoires/rep/opportunities/failure/training-eligibility", route =>
    route.fulfill({ status: 503, json: { detail: "Eligibility service unavailable" } }));
  await page.route("**/api/repertoires/rep/opportunities/failure/train", route => {
    trainPosts++;
    return route.fulfill({ json: { card_id: "response-card", queued: true, idempotent: false } });
  });
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("Eligibility service unavailable").first()).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Train this decision" })).toBeDisabled();
  expect(trainPosts).toBe(0);
});

test("eligibility recovers after an unchanged feed poll through an item retry", async ({ page }) => {
  await page.clock.install();
  let eligibilityReads = 0;
  let eligibilityReady = false;
  let feedReads = 0;
  await page.route("**/api/discoveries?**", route => {
    feedReads++;
    return route.fulfill({ json: discoveryFeed([
      discoveryFixture("recovering-eligibility", "revision", "response-card"),
    ]) });
  });
  await page.route("**/api/repertoires/rep/opportunities/recovering-eligibility/training-eligibility", route => {
    eligibilityReads++;
    return eligibilityReady
      ? route.fulfill({ json: { eligible: true, reason: null } })
      : route.fulfill({ status: 503, json: { detail: "Eligibility service unavailable" } });
  });
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("Eligibility service unavailable").first()).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Train this decision" })).toBeDisabled();
  await page.clock.fastForward(31_000);
  await expect.poll(() => feedReads).toBeGreaterThanOrEqual(2);
  eligibilityReady = true;
  await viewer.getByRole("button", { name: "Retry eligibility" }).click();
  await expect.poll(() => eligibilityReads).toBe(2);
  await expect(viewer.getByRole("button", { name: "Train this decision" })).toBeEnabled();
});

test("discovery viewer with a white decision rejects a black recommendation", async ({ page }) => {
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
    discoveries: [{ id: "wrong-turn", repertoire_id: "rep", kind: "post_gap_weakness",
      status: "active", fen_key: beforeReply.split(" ").slice(0, 4).join(" "),
      fen: beforeReply, decision_fen: decisionFen, decision_start_fen: startFen,
      decision_route_uci: ["e2e4", "c7c5"], accepted_moves_uci: [], card_id: null,
      opponent_move_uci: "c7c5", trained_color: "white", score: 1,
      evidence: { supporting_games: 1 }, evidence_fingerprint: "wrong-turn-revision",
      seen_at: null, snoozed_until: null, admission_state: null, admitted_card_id: null,
      unread: true, source_games: [], routes: ["e4 c5"],
      created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" }],
    total: 1, next_offset: null, unread_count: 1,
  } }));
  await page.route("**/api/discoveries/wrong-turn/recommendations", route => route.fulfill({ json: {
    state: "ready", opportunity_id: "wrong-turn", evidence_fingerprint: "wrong-turn-revision",
    starting_fen: decisionFen, suggested_move_uci: "g8f6",
    candidates: [{ move_uci: "g8f6", score: { cp: 0, mate: null }, loss_cp: 0,
      similarity: "no supported similarity", repertoire_line_count: 0,
      exact_transposition: false, example_line_id: null, example_line_name: null,
      preview_moves_uci: ["g8f6"], engine_version: "Stockfish", network_version: "NNUE",
      depth: 14, report_id: "a".repeat(64), source_game_id: "game", source_ply: 2 }],
  } }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("No discoveries ready for review")).toBeVisible();
  await expect(viewer.getByText(/Discovery position and recommendation disagree/)).toBeVisible();
  await expect(viewer.getByRole("button", { name: /Suggested|Choose|Add and train/ })).toHaveCount(0);
  await expect(viewer.locator("svg.cg-shapes > g > g[cgHash]")).toHaveCount(0);
});

test("black repertoire discovery keeps black at the bottom through route and preview", async ({ page }) => {
  const blackDecisionFen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
    discoveries: [{ id: "black", repertoire_id: "rep", kind: "post_gap_weakness",
      status: "active", fen_key: blackDecisionFen.split(" ").slice(0, 4).join(" "),
      fen: blackDecisionFen, decision_fen: blackDecisionFen, decision_start_fen: startFen,
      decision_route_uci: ["e2e4"], accepted_moves_uci: [], card_id: null,
      opponent_move_uci: "e2e4", trained_color: "black", score: 1,
      evidence: { supporting_games: 1 }, evidence_fingerprint: "black-revision",
      seen_at: "2026-09-24T00:00:00Z", snoozed_until: null, admission_state: null,
      admitted_card_id: null, unread: false, source_games: [], routes: ["e4"],
      created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" }],
    total: 1, next_offset: null, unread_count: 0,
  } }));
  await page.route("**/api/discoveries/black/recommendations", route => route.fulfill({ json: {
    state: "ready", opportunity_id: "black", evidence_fingerprint: "black-revision",
    starting_fen: blackDecisionFen, suggested_move_uci: "g8f6",
    candidates: [{ move_uci: "g8f6", score: { cp: -10, mate: null }, loss_cp: 0,
      similarity: "no supported similarity", repertoire_line_count: 0,
      exact_transposition: false, example_line_id: null, example_line_name: null,
      preview_moves_uci: ["g8f6"], engine_version: "Stockfish", network_version: "NNUE",
      depth: 14, report_id: "a".repeat(64), source_game_id: "game", source_ply: 1 }],
  } }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  const board = viewer.locator(".board-frame");
  await expect(viewer.getByText("1 of 1 · black to move")).toBeVisible();
  await expect(board).toHaveAttribute("data-orientation", "black");
  await viewer.getByRole("button", { name: "Previous move" }).click();
  await expect(board).toHaveAttribute("data-fen", startFen);
  await expect(board).toHaveAttribute("data-orientation", "black");
  await viewer.getByRole("button", { name: "Next move" }).click();
  await expect(board).toHaveAttribute("data-fen", blackDecisionFen);
  await viewer.getByRole("button", { name: "Next move" }).click();
  await expect(board).toHaveAttribute("data-orientation", "black");
  await expect(board).toHaveAttribute("data-fen", /5n2/);
});

for (const viewport of [{ width: 390, height: 844 }, { width: 1470, height: 836 }]) {
  test(`discovery viewer compares one learner decision and returns from Builder ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
      discoveries: [{ id: "gap", repertoire_id: "rep", kind: "missing_response",
        status: "active", fen_key: beforeReply.split(" ").slice(0, 4).join(" "),
        fen: beforeReply, decision_fen: decisionFen, decision_start_fen: startFen,
        decision_route_uci: ["e2e4", "c7c5"],
        accepted_moves_uci: [], card_id: null, opponent_move_uci: "c7c5", trained_color: "white",
        score: 1, evidence: { supporting_games: 4 }, evidence_fingerprint: "gap-revision",
        seen_at: "2026-09-24T00:00:00Z", snoozed_until: null, admission_state: null,
        admitted_card_id: null, unread: false, source_games: [], routes: ["e4 c5"],
        created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" }],
      total: 1, next_offset: null, unread_count: 0,
    } }));
    await page.route("**/api/discoveries/gap/recommendations", route => route.fulfill({ json: {
      state: "ready", opportunity_id: "gap", evidence_fingerprint: "gap-revision", starting_fen: decisionFen,
      accepted_moves_uci: [], suggested_move_uci: "g1f3", suggestion_reason: "same move in a comparable repertoire position",
      candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null },
        loss_cp: 0, similarity: "same move in a comparable repertoire position",
        repertoire_line_count: 3, exact_transposition: false, example_line_id: "line",
        example_line_name: null, preview_moves_uci: ["g1f3"], engine_version: "Stockfish",
        network_version: "NNUE", depth: 14, report_id: "a".repeat(64),
        source_game_id: "coverage:node", source_ply: 2 },
      { move_uci: "d2d4", score: { cp: 10, mate: null }, loss_cp: 10,
        similarity: "same move in a comparable repertoire position",
        repertoire_line_count: 2, exact_transposition: false, example_line_id: "line-2",
        example_line_name: null, preview_moves_uci: ["d2d4"], engine_version: "Stockfish",
        network_version: "NNUE", depth: 14, report_id: "a".repeat(64),
        source_game_id: "coverage:node", source_ply: 2 }],
      engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 },
        { move_uci: "d2d4", score: { cp: 10, mate: null }, loss_cp: 10, depth: 14 }],
    } }));
    await prepareUI(page);
    await openDiscoveries(page);
    const viewer = page.getByRole("dialog", { name: "Discoveries" });
    await expect(viewer).toBeVisible();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
    await expect(viewer.getByText(/Suggested Nf3/)).toBeVisible();
    const comparison = viewer.getByRole("region", { name: "Discovery move comparison" });
    await expect(comparison).toContainText("3 comparable repertoire lines");
    await expect(comparison).toContainText("2 comparable repertoire lines");
    await expect(comparison).toContainText("10 cp from best");
    await expect(viewer.locator("svg.cg-shapes > g > g[cgHash]")).toHaveCount(2);
    await page.keyboard.press("ArrowLeft");
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", beforeReply.replace(" e3 ", " - "));
    await viewer.getByRole("button", { name: "Next move" }).click();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await page.keyboard.press("ArrowRight");
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", /5N2/);
    await viewer.getByRole("button", { name: "Previous move" }).click();
    await expect(viewer.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen);
    await expect(viewer.locator("svg.cg-shapes > g > g[cgHash]")).toHaveCount(2);
    await viewer.getByRole("button", { name: "Nf3", exact: true }).click();
    await expect(viewer.getByText("Nf3 selected.", { exact: false })).toBeVisible();
    await expect(viewer.getByRole("button", { name: "Add and train" })).toBeEnabled();
    await noPageOverflow(page);
    await viewer.getByRole("button", { name: "Open in Builder" }).click();
    await expect(page.getByRole("button", { name: "Return to discovery" })).toBeVisible();
    await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", decisionFen.replace(" c6 ", " - "));
    await expect(page.locator(".analysis-moves")).toContainText("e4");
    await expect(page.locator(".analysis-moves")).toContainText("c5");
    await page.getByRole("button", { name: "Return to discovery" }).click();
    await expect(viewer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(viewer).toHaveCount(0);
  });
}

test("confirmed Add and train clears the completed item after advancing before the save responds", async ({ page }) => {
  await page.setViewportSize({ width: 1470, height: 836 });
  const discovery = (id: string) => ({ id, repertoire_id: "rep", kind: "missing_response",
    status: "active", fen_key: beforeReply.split(" ").slice(0, 4).join(" "),
    fen: beforeReply, decision_fen: decisionFen, decision_start_fen: startFen,
    decision_route_uci: ["e2e4", "c7c5"], accepted_moves_uci: [], card_id: null,
    opponent_move_uci: "c7c5", trained_color: "white", score: 1,
    evidence: { supporting_games: 4 }, evidence_fingerprint: `${id}-revision`,
    seen_at: "2026-09-24T00:00:00Z", snoozed_until: null, admission_state: null,
    admitted_card_id: null, unread: false, source_games: [], routes: ["e4 c5"],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" });
  await page.route("**/api/discoveries?**", route => route.fulfill({ json: {
    discoveries: [discovery("first"), discovery("second")], total: 2,
    next_offset: null, unread_count: 0,
  } }));
  await page.route("**/api/discoveries/*/recommendations", route => {
    const id = route.request().url().includes("first") ? "first" : "second";
    return route.fulfill({ json: {
      state: "ready", opportunity_id: id, evidence_fingerprint: `${id}-revision`,
      starting_fen: decisionFen, accepted_moves_uci: [], suggested_move_uci: "g1f3",
      suggestion_reason: "no supported similarity",
      candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0,
        similarity: "no supported similarity", example_line_id: null, example_line_name: null,
        repertoire_line_count: 0, exact_transposition: false,
        preview_moves_uci: ["g1f3"], engine_version: "Stockfish", network_version: "NNUE",
        depth: 14, report_id: "a".repeat(64), source_game_id: "coverage:node", source_ply: 2 }],
      engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 }],
    } });
  });
  let finishSave: (() => void) | undefined;
  await page.route("**/api/discoveries/first/accept", route => new Promise<void>((resolve) => {
    finishSave = () => { void route.fulfill({ status: 202, json: {
      status: "preparing", intent_id: "intent",
    } }).then(resolve); };
  }));
  await page.route("**/api/discovery-admissions/intent", route => route.fulfill({ json: {
    state: "queued", error: null,
  } }));
  await prepareUI(page);
  await openDiscoveries(page);
  const viewer = page.getByRole("dialog", { name: "Discoveries" });
  await expect(viewer.getByText("1 of 2 · white to move")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Add and train" })).toBeEnabled();
  await viewer.getByRole("button", { name: "Add and train" }).click();
  await expect(viewer.getByText("2 of 2 · white to move")).toBeVisible();
  await expect.poll(() => Boolean(finishSave)).toBe(true);
  finishSave?.();
  await expect(viewer.getByText("1 of 1 · white to move")).toBeVisible();
  await expect(viewer.getByRole("button", { name: "Previous", exact: true })).toBeDisabled();
  await expect(viewer.getByRole("button", { name: "Add and train" })).toBeEnabled();
});


test("Repertoire Opportunities stale Train submits displayed evidence and preserves the current revision", async ({ page }) => {
  let currentFingerprint = "shown-A";
  let submittedFingerprint: unknown;
  let newerEvidenceHandled = false;
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{
    id: "rep", name: "Revision safety", source_name: "Regression", line_count: 1, card_count: 1,
    due_count: 0, trained_color: "white", integrity_status: "clean", integrity_issue_count: 0,
  }] } }));
  await page.route("**/api/repertoires/rep/opportunities", route => route.fulfill({ json: {
    opportunities: [discoveryFixture("repertoire-stale", "shown-A", "target")],
  } }));
  await page.route("**/api/repertoires/rep/opportunities/repertoire-stale/train", route => {
    submittedFingerprint = route.request().postDataJSON()?.evidence_fingerprint;
    if (submittedFingerprint !== currentFingerprint)
      return route.fulfill({ status: 409, json: { detail: "Discovery evidence changed; refresh before training" } });
    newerEvidenceHandled = true;
    return route.fulfill({ json: { card_id: "target", queued: true, idempotent: false } });
  });
  await prepareUI(page);
  await navigate(page, "Repertoire");
  await page.getByRole("button", { name: "Opportunities", exact: true }).click();
  const panel = page.locator(".opportunities-panel");
  const train = panel.getByRole("button", { name: "Train this decision", exact: true });
  await expect(train).toBeEnabled();
  currentFingerprint = "newer-B";
  await train.click();
  await expect(page.getByText("Discovery evidence changed; refresh before training", { exact: true })).toBeVisible();
  expect(submittedFingerprint).toBe("shown-A");
  expect(newerEvidenceHandled).toBe(false);
  await expect(train).toBeEnabled();
  await expect(panel.getByText("In training queue", { exact: true })).toHaveCount(0);
});

test("hidden discovery speculation stays paused and viewer demand starts bounded look-ahead", async ({ page }, testInfo) => {
  await page.clock.install();
  await page.addInitScript(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () =>
      Reflect.get(window, "discoveryFixtureVisible") ? "visible" : "hidden" });
  });
  const { prepareVisualUI } = await import("./visual-fixtures");
  await prepareVisualUI(page, false);
  let feedReads = 0;
  const requested: string[] = [];
  const releases = new Map<string, () => Promise<void>>();
  const items = Array.from({ length: 100 }, (_, index) => discoveryFixture(`finding-${index}`));
  await page.route("**/api/discoveries?**", route => {
    feedReads++; return route.fulfill({ json: discoveryFeed(items) });
  });
  await page.route("**/api/discoveries/*/recommendations", async route => {
    const id = route.request().url().match(/discoveries\/([^/]+)\/recommendations/)![1];
    requested.push(id);
    await new Promise<void>(resolve => releases.set(id, async () => {
      releases.delete(id); await route.fulfill({ json: readyPreview(id) }); resolve();
    }));
  });
  try {
    await page.reload();
    await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
    await expect(page.locator(".tempo-discoveries-tray")).toBeVisible();
    await page.clock.fastForward(60_000);
    expect(feedReads).toBe(0); expect(requested).toEqual([]);
    await page.evaluate(() => {
      Reflect.set(window, "discoveryFixtureVisible", true);
      for (let wake = 0; wake < 10; wake++) document.dispatchEvent(new Event("visibilitychange"));
    });
    // The training attempt still owns the foreground after visibility returns.
    await page.clock.fastForward(30_000);
    expect(feedReads).toBe(0); expect(requested).toEqual([]);
    await openDiscoveries(page);
    await expect.poll(() => requested.length).toBe(2);
    expect(requested).toEqual(["finding-0", "finding-1"]);
    await releases.get("finding-0")!();
    await expect(page.getByText("1 of 1 · white to move")).toBeVisible();
    await expect.poll(() => requested.length).toBe(3);
    expect(requested[2]).toBe("finding-2");
    const diagnostics = await page.locator(".tempo-discoveries-tray").evaluate(element =>
      (Reflect.get(element, "discoveryPreviewDiagnostics") as () => { maximumActive: number; queued: number })());
    expect(diagnostics.maximumActive).toBe(2);
    expect(requested).not.toContain("finding-99");
    await testInfo.attach("discovery-demand", { body: JSON.stringify({ feedReads, requested, diagnostics }), contentType: "application/json" });
  } finally { await Promise.all([...releases.values()].map(release => release())); }
});
