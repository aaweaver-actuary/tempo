import { preparePromotionStudy, dragStudyKnightPromotion } from "./study-promotion-fixtures";
import { test, expect } from "./observability";
import { readFileSync } from "node:fs";
import { prefixSourceSchema } from "../../app/domain/prefix-comparison";
import { prefixTransitionPlanSchema, transitionSummary } from "../../app/domain/prefix-transition";
import { navigate, noPageOverflow } from "./ui-fixtures";
import { prepareVisualUI } from "./visual-fixtures";
import { expectedPieces, renderedPieces, playMove } from "./keyboard-fixtures";

const structuralFixture = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/structural.json", "utf8")) as typeof import("../fixtures/prefix-comparison/structural.json");
const transitionFixture = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/transition.json", "utf8")) as typeof import("../fixtures/prefix-comparison/transition.json");

// API route interception must remain deterministic after the app registers its shell worker.
test.use({ serviceWorkers: "block" });

test("issue81_open_deletion_dialog_observes_native_cross_tab_reservation across browser engines", async ({ page, context }) => {
  const transition = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/transition.json", "utf8"));
  const structural = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/structural.json", "utf8"));
  const summary = transitionSummary(prefixTransitionPlanSchema.parse(transition.ready), prefixSourceSchema.parse(structural.source), "Combined Black");
  await prepareVisualUI(page);
  let deletions = 0;
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [
    { id: "rep", name: "Combined Black", source_name: "opening.pgn", line_count: 2, card_count: 5, due_count: 0 },
  ] } }));
  await page.route("**/api/repertoires/rep?**", route => {
    if (route.request().method() === "DELETE") deletions++;
    return route.fulfill({ json: { deleted: true, id: "rep" } });
  });
  await page.goto("/"); await navigate(page, "Repertoire");
  await page.locator(".repertoire-card details.card-menu summary").click();
  await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /Delete “Combined Black”/ });
  const confirm = dialog.getByRole("button", { name: "Delete repertoire", exact: true });
  await expect(confirm).toBeEnabled();
  const writer = await context.newPage();
  await writer.route("**/structural-storage-writer.html", route => route.fulfill({ contentType: "text/html", body: "<title>Structural storage fixture</title>" }));
  await writer.goto("/structural-storage-writer.html");
  await writer.evaluate(async operation => {
    await navigator.locks.request("tempo-pending-prefix-transition-v1:reservation", () => {
      localStorage.setItem("tempo-pending-prefix-transition-v1:rep", JSON.stringify(operation));
    });
  }, { version: 1, repertoireId: "rep", operationId: "browser-prefix", request: transition.request, summary, state: "pending" });
  await expect(confirm).toBeDisabled();
  await expect(dialog.getByRole("alert")).toContainText("Resolve the prefix transition");
  await expect(dialog.getByLabel("Keep learned cards")).toBeChecked();
  expect(deletions).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("tempo-pending-repertoire-delete-v1"))).toBeNull();
  await writer.close();
});

test("prepared_next_card_advances_after_durable_review_without_queue_refresh across browser engines", async ({ page }) => {
  const cards = ["Prepared A", "Prepared B", "Prepared C"].map((name, index) => ({ id: `prepared-${index}`,
    queue_entry_id: 9101 + index, revision: 1, cycle: 0, content_type: "opening", repertoire_name: name, repertoire_source: "synthetic",
    start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4", "e7e5", "g1f3"] }));
  let releaseAncillary!: () => void;
  const held = new Promise<void>(resolve => { releaseAncillary = resolve; });
  let queueReads = 0;
  const today = new Date().toLocaleDateString("en-CA");
  await page.route("**/api/queue/window**", async route => {
    if (++queueReads > 1) await held;
    await route.fulfill({ json: { cards, count: 3, local_date: today } });
  });
  await page.route("**/api/cards/*/review", route => route.fulfill({ json: { persisted: true, prepared_continuation: {
    queue_entry_id: 9102, card_id: "prepared-1", revision: 1, cycle: 0, local_date: today,
  } } }));
  await page.route("**/api/repertoire/lines", async route => { await held; await route.fulfill({ json: { lines: [] } }); });
  try {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Prepared A", exact: true })).toBeVisible();
    await playMove(page, page.locator(".board-frame").first(), "e2", "e4");
    await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Correct", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Prepared B", exact: true })).toBeVisible();
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    await expect.poll(() => renderedPieces(page.locator(".board-frame").first())).toEqual(expectedPieces(cards[1].start_fen));
    await playMove(page, page.locator(".board-frame").first(), "e2", "e4");
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4P3/);
    expect(queueReads).toBeGreaterThan(1);
  } finally { releaseAncillary(); }
});

test("critical navigation, board input, split and dialogs work across browser engines", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await prepareVisualUI(page);
  await navigate(page, "Builder");
  const board = page.locator(".board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const surface = await page.locator(".cg-wrap").boundingBox();
  if (!surface) throw new Error("Board has no visible surface");
  for (const [file, rank] of [
    [4, 6],
    [4, 4],
  ])
    await page.mouse.click(
      surface.x + ((file + 0.5) * surface.width) / 8,
      surface.y + ((rank + 0.5) * surface.height) / 8,
    );
  await expect(board).toHaveAttribute("data-fen", /4P3/);
  const separator = page.getByRole("separator", { name: "Board size" });
  await separator.focus();
  await page.keyboard.press("ArrowRight");
  await expect(separator).toHaveAttribute("aria-valuenow", "48");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  const note = page.getByRole("textbox", { name: "Position comment" });
  await note.fill("Remember this position.");
  await page.getByRole("tab", { name: "Compare", exact: true }).click();
  await page.getByRole("tab", { name: "Notes", exact: true }).click();
  await expect(note).toHaveValue("Remember this position.");
  await navigate(page, "Repertoire");
  await noPageOverflow(page);
  const importButton = page.getByRole("button", { name: /Import PGN/ });
  await importButton.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(importButton).toBeFocused();
  await navigate(page, "Games");
  await page.getByRole("tab", { name: "Library", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "Filter source" }),
  ).toBeVisible();
  await noPageOverflow(page);
});


test("tablet menu Escape restores the visible navigation trigger", async ({page}) => {
  await page.setViewportSize({width:768,height:1024}); await prepareVisualUI(page);
  const menu=page.locator(".tablet-navigation"); await menu.click();
  await page.locator("#workspace-menu").getByRole("button",{name:"Games",exact:true}).focus();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await expect(page.locator("#workspace-menu")).toHaveCount(0);
});

test("WebKit desktop prepared queue outage preserves the position and blocks grading until Retry", async ({ page }) => {
  const startingFen = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1";
  const card = { id: "webkit-live", queue_entry_id: 8123, start_fen: startingFen,
    moves: ["d7d5", "c2c4"], content_type: "opening", repertoire_name: "WebKit live card",
    repertoire_source: "PGN", trained_color: "black" };
  let liveRequestFails = false;
  let preparedRequests = 0;
  await page.route("**/api/queue/prepared?**", (route) => {
    preparedRequests += 1;
    return route.fulfill({ status: 500, body: "Desktop must not prepare a phone queue" });
  });
  let queueRequests = 0;
  await page.route("**/api/queue/window**", (route) => {
    queueRequests += 1;
    return liveRequestFails
    ? route.abort("failed")
    : route.fulfill({ json: { cards: [card], count: 1 } });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "WebKit live card" })).toBeVisible();
  await page.evaluate(async (savedCard) => {
    await new Promise<void>((resolve, reject) => {
      const opened = indexedDB.open("tempo-offline-training", 2);
      opened.onupgradeneeded = () => opened.result.createObjectStore("training");
      opened.onerror = () => reject(opened.error);
      opened.onsuccess = () => {
        const transaction = opened.result.transaction("training", "readwrite");
        transaction.objectStore("training").put({
          localDate: new Date().toLocaleDateString("en-CA"), preparedAt: new Date().toISOString(),
          cards: [savedCard], attempts: [], nextTemporaryId: -1,
        }, "prepared-daily-queue");
        transaction.oncomplete = () => { opened.result.close(); resolve(); };
        transaction.onerror = () => reject(transaction.error);
      };
    });
  }, card);
  const originalFen = await page.locator(".board-frame").getAttribute("data-fen");
  const requestsBeforeFailure = queueRequests;
  await page.bringToFront();
  liveRequestFails = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => queueRequests).toBeGreaterThan(requestsBeforeFailure);
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-fen", originalFen!);
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "false");
  await expect(page.getByRole("button", { name: "Correct" })).toBeDisabled();
  await expect(page.getByText("Offline queue", { exact: true })).toHaveCount(0);
  liveRequestFails = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".board-frame")).toHaveAttribute("data-input-enabled", "true");
  expect(preparedRequests).toBe(0);
});

test("capture modal keeps incomplete setup draggable and restores focus across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Tactics");
  const launch = page.getByRole("button", { name: "Capture tactic", exact: true });
  await launch.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Place R", exact: true }).click();
  const board = dialog.locator(".board-frame");
  await board.scrollIntoViewIfNeeded();
  const surface = await board.locator(".cg-wrap").boundingBox();
  if (!surface) throw new Error("Capture board has no surface");
  const x = surface.x + surface.width / 16;
  await page.mouse.click(x, surface.y + 15 * surface.height / 16);
  await dialog.getByRole("button", { name: "Move pieces", exact: true }).click();
  await board.scrollIntoViewIfNeeded();
  await page.mouse.move(x, surface.y + 15 * surface.height / 16); await page.mouse.down();
  await page.mouse.move(x, surface.y + 9 * surface.height / 16, { steps: 8 }); await page.mouse.up();
  await expect(board).toHaveAttribute("data-fen", "8/8/8/8/R7/8/8/8 w - - 0 1");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0); await expect(launch).toBeFocused();
});

test("capture accepts SAN from Black's perspective across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Tactics");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog"), board = dialog.locator(".board-frame");
  await dialog.getByLabel("FEN", { exact: true }).fill("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1");
  await dialog.getByRole("button", { name: "Solution", exact: true }).click();
  await dialog.getByLabel("SAN moves").fill("1...e5");
  await dialog.getByLabel("SAN moves").press("Enter");
  await expect(board).toHaveAttribute("data-orientation", "black");
  await expect(board).toHaveAttribute("data-fen", "rnbqkbnr/pppp1ppp/8/4p3/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2");
  await expect(dialog.getByLabel("SAN moves")).toBeFocused();
  await dialog.getByLabel("SAN moves").fill("Nf3");
  await dialog.getByRole("button", { name: "Add moves", exact: true }).click();
  await expect(board).toHaveAttribute("data-fen", "rnbqkbnr/pppp1ppp/8/4p3/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 2");
  await expect(board).toHaveAttribute("data-orientation", "black");
});

test("Chess.com puzzle import starts at the solver position across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Tactics");
  await page.getByRole("button", { name: "Capture tactic", exact: true }).click();
  const dialog = page.getByRole("dialog"), board = dialog.locator(".board-frame");
  await dialog.getByRole("button", { name: "Paste Chess.com puzzle PGN" }).click();
  await dialog.getByLabel("Chess.com puzzle PGN").fill(readFileSync("tests/fixtures/chesscom-puzzle-rush.pgn", "utf8"));
  await dialog.getByRole("button", { name: "Load PGN", exact: true }).click();
  const startingFen = "r1b2rk1/ppq2p1p/2np1Qp1/2b5/2B1Pp2/1P6/P1PP2PP/R1B1K1NR b KQ - 0 1";
  await expect(board).toHaveAttribute("data-fen", startingFen);
  await expect(board).toHaveAttribute("data-orientation", "black");
  await board.click();
  await page.keyboard.press("End");
  await expect(board).toHaveAttribute("data-fen", "r1b2rk1/ppq2p1p/3p2p1/8/2BnPp2/1P6/P1PP2PP/R1B1K1NR w KQ - 0 3");
  await page.keyboard.press("Home");
  await expect(board).toHaveAttribute("data-fen", startingFen);
  await expect(board).toHaveAttribute("data-orientation", "black");
});

test("contextual board keys and nested popup Escape work across browser engines", async ({ page }) => {
  await prepareVisualUI(page); await navigate(page, "Builder");
  // Initial repertoire binding changes the board identity and cancels selection.
  await expect(page.getByRole("combobox", { name: "Active repertoire" })).toHaveValue("visual-repertoire");
  const board = page.locator(".board-frame");
  const root = await board.getAttribute("data-fen");
  await playMove(page, board, "e2", "e4");
  await expect(board).not.toHaveAttribute("data-fen", root!);
  const decision = await board.getAttribute("data-fen");
  await page.keyboard.press("ArrowLeft"); await expect(board).toHaveAttribute("data-fen", root!);
  await page.keyboard.press("End"); await expect(board).toHaveAttribute("data-fen", decision!);
  await page.keyboard.press("f"); await expect(board).toHaveAttribute("data-orientation", "black");
  await page.keyboard.press("Home"); await page.keyboard.press("r");
  await expect(board).toHaveAttribute("data-orientation", "white");
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(decision!));
  await page.getByRole("tab", { name: "Repertoire", exact: true }).click();
  const search = page.getByRole("button", { name: /Position search/ });
  await search.click(); await page.keyboard.press("Escape"); await expect(search).toBeFocused();
  await navigate(page, "Tactics");
  const launch = page.getByRole("button", { name: "Capture tactic", exact: true });
  await launch.click();
  const capture = page.getByRole("dialog", { name: "Capture tactic" });
  const background = page.locator(".persistent-board-shell .board-frame");
  const backgroundOrientation = await background.getAttribute("data-orientation");
  const popupBoard = capture.locator(".board-frame");
  const helpButton = capture.getByRole("button", { name: "Keyboard shortcuts", exact: true });
  await popupBoard.locator("..").focus();
  await page.keyboard.press("f");
  await expect(popupBoard).toHaveAttribute("data-orientation", "black");
  await expect(background).toHaveAttribute("data-orientation", backgroundOrientation!);
  await helpButton.click();
  const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(help).toBeVisible(); await page.keyboard.press("Escape");
  await expect(help).toHaveCount(0); await expect(capture).toBeVisible(); await expect(helpButton).toBeFocused();
  await page.keyboard.press("Escape"); await expect(capture).toHaveCount(0); await expect(launch).toBeFocused();
});

test("queue retirement during a held opening drag preserves active board and accepts one drop", async ({ page }) => {
  await prepareVisualUI(page, false);
  const board = page.locator(".board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const originalFen = await board.getAttribute("data-fen");
  const surface = (await page.locator(".cg-wrap").boundingBox())!;
  const origin = { x: surface.x + 4.5 * surface.width / 8, y: surface.y + 6.5 * surface.height / 8 };
  const destination = { x: origin.x, y: surface.y + 4.5 * surface.height / 8 };
  await page.mouse.move(origin.x, origin.y); await page.mouse.down();
  await page.mouse.move(origin.x + 20, origin.y - 25, { steps: 4 });
  await expect(page.locator("piece.dragging")).toHaveCount(1);
  await page.locator("piece.dragging").evaluate(element => element.setAttribute("data-original-drag", "true"));
  let refreshed = false;
  await page.route("**/api/queue/window?**", route => {
    refreshed = true;
    return route.fulfill({ json: { count: 1, cards: [{ id: "future-after-limit", queue_entry_id: 901,
      start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      moves: ["d2d4"], content_type: "opening", repertoire_name: "Future card", repertoire_source: "PGN" }] } });
  });
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => refreshed).toBe(true);
  await expect(page.locator(".session-count strong")).toHaveText("2");
  await expect(page.locator('piece.dragging[data-original-drag="true"]')).toHaveCount(1);
  await expect(board).toHaveAttribute("data-fen", originalFen!);
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await page.mouse.move(destination.x, destination.y, { steps: 4 }); await page.mouse.up();
  await expect(board).toHaveAttribute("data-fen", /4p3\/4P3/);
  await expect(page.getByText("Spanish opening", { exact: true }).first()).toBeVisible();
});

test("phone opening identity and move input work across browser engines", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepareVisualUI(page);
  await expect(page.locator(".phone-study-heading h2")).toHaveText("Spanish opening");
  const board = page.locator(".persistent-board-shell .board-frame");
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  await playMove(page, board, "e2", "e4");
  await expect(board).toHaveAttribute("data-fen", / b KQkq /);
  await expect.poll(() => renderedPieces(board)).toEqual(expectedPieces(await board.getAttribute("data-fen") ?? ""));
  const menu = page.locator(".phone-study-actions");
  await menu.locator("summary").click();
  await menu.getByRole("button", { name: /Restart/ }).click();
  await expect(menu).not.toHaveAttribute("open", "");
  await expect(menu.locator("summary")).toBeFocused();
  await noPageOverflow(page);
});

import { crossTabOpeningCompletion } from "./opening-evidence-cross-tab-fixture";
for (const fallback of [false, true]) test(`cross-tab opening completion wakes reconciliation through ${fallback ? "BroadcastChannel fallback" : "storage events"}`, async ({ page, context }) => {
  await crossTabOpeningCompletion(page, context, fallback);
});

test("Study board promotion places the selected knight across browser engines", async ({ page }) => {
  const { fen, move } = await preparePromotionStudy(page);
  await dragStudyKnightPromotion(page, fen, move);
});


test("prefix journal native Web Locks preserve one same-origin identity across browser engines", async ({ context, page }) => {
  const { openJournalHarness, reserveJournal } = await import("./support/prefix-transition-journal-browser");
  const secondTab = await context.newPage(); await openJournalHarness(page); await openJournalHarness(secondTab);
  expect(await page.evaluate(() => typeof navigator.locks?.request)).toBe("function");
  const operations = await Promise.all([reserveJournal(page), reserveJournal(secondTab)]);
  expect(operations[0].operationId).toBe(operations[1].operationId);
});
test("prefix journal unavailable Web Locks fail closed across browser engines", async ({ page }) => {
  const { openJournalHarness, persistedJournal } = await import("./support/prefix-transition-journal-browser");
  await openJournalHarness(page);
  await page.evaluate(() => Object.defineProperty(navigator, "locks", { value: undefined, configurable: true }));
  const refusal = await page.evaluate(async () => {
    try { await window.prefixJournal.beginPrefixTransition("rep", window.prefixJournal.request, window.prefixJournal.summary); return "unsafe reservation"; }
    catch (failure) { return (failure as Error).message; }
  });
  expect(refusal).toMatch(/Web Locks.*HTTPS or localhost/); expect(await persistedJournal(page)).toBeNull();
});

test("audit187_storage_event_preserves_prefix_identity_and_guard across browser engines", async ({ page, context }) => {
  await prepareVisualUI(page);
  const operationId = "audit187-cross-browser-original";
  const record = { version: 1, repertoireId: "rep", operationId, state: "blocked", request: transitionFixture.request,
    summary: transitionSummary(prefixTransitionPlanSchema.parse(transitionFixture.ready), prefixSourceSchema.parse(structuralFixture.source), "Combined Black") };
  await page.addInitScript(saved => localStorage.setItem("tempo-pending-prefix-transition-v1:rep", JSON.stringify(saved)), record);
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{ id: "rep", name: "Combined Black", source_name: "Combined Black.pgn", line_count: 4, card_count: 5, due_count: 0, trained_color: "black" }] } }));
  const methods: string[] = [];
  await page.route("**/api/operations/**", route => {
    methods.push(route.request().method());
    return route.fulfill({ json: { operation_id: operationId, state: "blocked" } });
  });
  await page.goto("/"); await navigate(page, "Repertoire");
  await expect(page.getByRole("button", { name: "Resume prefix transition" })).toBeVisible();
  const storageWriter = await context.newPage();
  try {
    // A same-origin fixture document emits real storage events without mounting the product.
    await storageWriter.route("**/audit187-storage-writer.html", route => route.fulfill({ contentType: "text/html", body: "<title>Storage-event fixture</title>" }));
    await storageWriter.goto("/audit187-storage-writer.html");
    await storageWriter.evaluate(() => localStorage.removeItem("tempo-pending-prefix-transition-v1:rep"));
    await expect(page.getByText(/browser recovery record is missing/i)).toBeVisible();
    await page.locator("details.card-menu summary").click();
    await expect(page.getByRole("menuitem", { name: "Rename", exact: true })).toBeDisabled();
    await page.locator("details.card-menu summary").click();
    await page.getByRole("button", { name: "Resume prefix transition" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: "Restore saved recovery record" })).toBeEnabled();
    await dialog.getByRole("button", { name: "Restore saved recovery record" }).click();
    await expect.poll(() => page.evaluate(() => {
      const restoredRecord = localStorage.getItem("tempo-pending-prefix-transition-v1:rep");
      return restoredRecord === null ? null : JSON.parse(restoredRecord).operationId;
    })).toBe(operationId);
    expect(methods.length).toBeGreaterThan(0); expect(methods.every(method => method === "GET")).toBe(true);
  } finally { await storageWriter.close(); }
});
