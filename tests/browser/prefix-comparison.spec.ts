import { test, expect, api, nav } from "./product-fixtures";
import { prefixSourceSchema, prefixComparisonSchema } from "../../app/domain/prefix-comparison";
import { prefixTransitionPlanSchema } from "../../app/domain/prefix-transition";
import type { APIRequestContext, Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const structuralFixture = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/structural.json", "utf8")) as typeof import("../fixtures/prefix-comparison/structural.json");
const transitionFixture = JSON.parse(readFileSync("tests/fixtures/prefix-comparison/transition.json", "utf8")) as typeof import("../fixtures/prefix-comparison/transition.json");

for (const width of [390, 1280]) test(`issue78_phone_and_desktop_comparison_remain_usable ${width}`, async ({ page, request }, testInfo) => {
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: `prefix-comparison-${width}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
      '[Event "Caro advance"]\n\n1. e4 c6 2. d4 d5 3. e5 c5 *\n\n[Event "Caro knight"]\n\n1. e4 c6 2. d4 d5 3. Nf3 Bg4 *\n\n[Event "QGD"]\n\n1. d4 d5 2. c4 e6 3. Nf3 Nf6 *\n\n[Event "Transposed incoming"]\n\n1. d4 d5 2. e4 c6 3. e5 c5 *') },
    trained_color: "black", initial_depth: "3",
  } });
  expect(imported.ok()).toBeTruthy();
  const { repertoire_id: repertoireId } = await imported.json();
  await expect.poll(async () => {
    const response = await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`);
    if (response.ok()) return (await response.json()).graph_generation;
    // Import publication is asynchronous; wait for its authoritative graph, not a wall-clock delay.
    expect(["evaluation_busy", "graph_not_ready"]).toContain((await response.json()).detail.code);
    return 0;
  }, { timeout: 30_000 }).toBeGreaterThan(0);
  let queueBefore: { cards: unknown[]; projection: { state: string; refresh_pending: number } };
  await expect.poll(async () => {
    const response = await request.get(`${api}/queue/today`);
    expect(response.ok()).toBe(true);
    queueBefore = await response.json();
    return { cards: queueBefore!.cards.length, state: queueBefore!.projection.state, pending: queueBefore!.projection.refresh_pending };
  }, { message: "Imported repertoire queue is published before immutability baseline", timeout: 30_000 })
    .toEqual({ cards: 2, state: "ready", pending: 0 });
  await page.setViewportSize({ width, height: 844 });
  await page.goto("/"); await nav(page, "Repertoire");
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: `prefix-comparison-${width}`, exact: true }) });
  await card.locator("details.card-menu summary").click();
  const sourceRead = page.waitForResponse(response => response.url().endsWith(`/repertoires/${repertoireId}/prefix-evaluation/source`));
  const trigger = card.getByRole("menuitem", { name: "Compare prefix depths", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: /Compare prefix depths/ });
  let sourceResponse = await sourceRead;
  await expect.poll(async () => {
    if (sourceResponse.ok()) return true;
    expect((await sourceResponse.json()).detail.code).toBe("evaluation_busy");
    expect(sourceResponse.headers()["retry-after"]).toBe("1");
    await expect(dialog.getByText("Study work is active", { exact: true })).toBeVisible();
    const retryResponse = page.waitForResponse(response => response.url().endsWith(`/repertoires/${repertoireId}/prefix-evaluation/source`));
    await dialog.getByRole("button", { name: "Refresh source", exact: true }).click();
    sourceResponse = await retryResponse;
    return sourceResponse.ok();
  }, { timeout: 15_000, intervals: [1000] }).toBe(true);
  const source = prefixSourceSchema.parse(await sourceResponse.json());
  await expect(dialog.getByText("Selected source lines: 0")).toBeVisible();
  const observedRequests: string[] = [];
  page.on("request", incoming => { if (incoming.url().includes("/api/") && incoming.method() !== "GET" && !incoming.url().endsWith("/system/browser-activity")) observedRequests.push(incoming.method() + " " + incoming.url()); });
  const startingPosition = dialog.getByLabel("Starting position and trained color");
  await startingPosition.selectOption(JSON.stringify([source.lines[0].start_fen, "black"]));
  await dialog.getByRole("combobox", { name: "Move 1", exact: true }).selectOption("e2e4");
  await dialog.getByRole("combobox", { name: "Move 2", exact: true }).selectOption("c7c6");
  await expect(dialog.getByText("Selected source lines: 2")).toBeVisible();
  const unselected = source.lines.filter(line => line.moves[0] === "d2d4");
  for (const line of unselected) await expect(dialog.getByRole("checkbox", { name: new RegExp(line.id) })).not.toBeChecked();
  await dialog.getByLabel("Candidate learner-decision depths").fill("2, 4");
  const evaluations: ReturnType<typeof prefixComparisonSchema.parse>[] = [];
  page.on("response", async response => {
    if (response.url().endsWith(`/repertoires/${repertoireId}/prefix-evaluation/evaluate`) && response.ok())
      evaluations.push(prefixComparisonSchema.parse(await response.json()));
  });
  await expect.poll(async () => {
    evaluations.length = 0;
    await dialog.getByRole("button", { name: "Compare depths", exact: true }).click();
    await expect.poll(async () => (await dialog.getByRole("region", { name: "Candidate depth 4", exact: true }).count())
      + (await dialog.getByRole("alert").count())).toBe(1);
    if (await dialog.getByRole("alert").count()) {
      await expect(dialog.getByRole("alert")).toContainText("Study work is active");
      return false;
    }
    return true;
  }, { timeout: 15_000, intervals: [1000] }).toBe(true);
  await expect(dialog.getByRole("region", { name: "Candidate depth 4", exact: true })).toBeVisible();
  await expect.poll(() => evaluations.length).toBe(2);
  for (const result of evaluations) {
    expect(result.selected_line_ids).toEqual(source.lines.filter(line => line.moves[0] === "e2e4").map(line => line.id).sort());
    for (const line of unselected) {
      const steps = (kind: "current" | "proposed") => result.whole_repertoire[kind].steps.filter(step => step.line_id === line.id);
      expect(steps("proposed")).toEqual(steps("current"));
    }
  }
  expect(observedRequests.every(incoming => incoming.startsWith("POST ") && incoming.endsWith("/prefix-evaluation/evaluate"))).toBe(true);
  expect((await (await request.get(`${api}/queue/today`)).json()).cards).toEqual(queueBefore!.cards);
  await expect.poll(async () => {
    const response = await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`);
    if (!response.ok()) {
      // Docker health reads can briefly hold foreground admission between UI and audit reads.
      expect(response.status()).toBe(503);
      expect((await response.json()).detail).toEqual({ code: "evaluation_busy", message: "Study work is active. Retry the diagnostic when study is idle." });
      expect(response.headers()["retry-after"]).toBe("1");
      return null;
    }
    return prefixSourceSchema.parse(await response.json());
  }, { timeout: 5000, intervals: [1000] }).toEqual(source);
  await expect(dialog.getByRole("button", { name: /apply|save|recommend/i })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await dialog.getByRole("heading", { name: "Candidate depth 2", exact: true }).evaluate(heading => heading.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: testInfo.outputPath(`prefix-comparison-${width}.png`) });
  await testInfo.attach(`prefix-comparison-${width}`, { path: testInfo.outputPath(`prefix-comparison-${width}.png`), contentType: "image/png" });
  const wholeScope = dialog.getByRole("region", { name: "Candidate depth 2", exact: true }).getByRole("region", { name: "Whole repertoire", exact: true });
  await wholeScope.getByRole("heading", { name: "Whole repertoire", exact: true }).evaluate(heading => heading.scrollIntoView({ block: "start" }));
  await expect(wholeScope.getByRole("row", { name: /Distinct cards/ })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath(`prefix-comparison-whole-${width}.png`) });
  await testInfo.attach(`prefix-comparison-whole-${width}`, { path: testInfo.outputPath(`prefix-comparison-whole-${width}.png`), contentType: "image/png" });
  await dialog.getByLabel("Candidate learner-decision depths").focus();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(card.locator("details.card-menu summary")).toBeFocused();
});

async function openShortening(page: Page, repertoireName: string, source: typeof structuralFixture.source) {
  const card = page.locator(".repertoire-card").filter({ has: page.getByRole("heading", { name: repertoireName, exact: true }) });
  await card.locator("details.card-menu summary").click();
  await card.getByRole("menuitem", { name: "Compare prefix depths", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /Compare prefix depths/ });
  await expect(dialog.getByText("Selected source lines: 0", { exact: true })).toBeVisible();
  await dialog.getByLabel("Starting position and trained color").selectOption(JSON.stringify([source.lines[0].start_fen, "black"]));
  await dialog.getByRole("combobox", { name: "Move 1", exact: true }).selectOption("e2e4");
  await dialog.getByRole("combobox", { name: "Move 2", exact: true }).selectOption("c7c6");
  if (source.lines.some(line => line.id === "alias")) await dialog.getByRole("checkbox", { name: /^alias ·/ }).uncheck();
  await dialog.getByLabel("Candidate learner-decision depths").fill("2");
  const candidate = dialog.getByRole("region", { name: "Candidate depth 2", exact: true });
  await expect.poll(async () => {
    await dialog.getByRole("button", { name: "Compare depths", exact: true }).click();
    // Evaluation and its freshness read both wait for foreground admission.
    // Observe their outcome rather than imposing the default five-second assertion.
    await expect.poll(async () => await candidate.count() + await dialog.getByRole("alert").count(),
      { timeout: 30_000, message: "Comparison finishes or reports an actionable diagnostic error" }).toBe(1);
    if (await dialog.getByRole("alert").count()) {
      await expect(dialog.getByRole("alert")).toContainText("Study work is active");
      return false;
    }
    return true;
  }, { timeout: 40_000, intervals: [1000] }).toBe(true);
  await dialog.getByRole("button", { name: "Review shortening", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Confirm shortening", exact: true })).toBeVisible({ timeout: 25_000 });
  return { dialog, card };
}

async function importTransitionRepertoire(request: APIRequestContext, label: string) {
  const imported = await request.post(`${api}/imports/pgn`, { multipart: {
    file: { name: `${label}.pgn`, mimeType: "application/x-chess-pgn", buffer: Buffer.from(
      '[Event "Caro advance"]\n\n1. e4 c6 2. d4 d5 3. e5 c5 *\n\n[Event "Caro knight"]\n\n1. e4 c6 2. d4 d5 3. Nf3 Bg4 *\n\n[Event "QGD"]\n\n1. d4 d5 2. c4 e6 3. Nf3 Nf6 *') },
    trained_color: "black", initial_depth: "3",
  } });
  expect(imported.ok()).toBe(true);
  const repertoireId = (await imported.json()).repertoire_id as string;
  let source: ReturnType<typeof prefixSourceSchema.parse>;
  await expect.poll(async () => {
    const response = await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`);
    if (!response.ok()) {
      expect(["evaluation_busy", "graph_not_ready"]).toContain((await response.json()).detail.code); return false;
    }
    source = prefixSourceSchema.parse(await response.json());
    const queue = await request.get(`${api}/queue/today`);
    expect(queue.ok()).toBe(true); const projection = (await queue.json()).projection;
    return projection.state === "ready" && projection.refresh_pending === 0;
  }, { timeout: 30_000, message: "Import graph and queue settle before transition confirmation" }).toBe(true);
  return { repertoireId, source: source! };
}

for (const width of [390, 1280]) test(`issue81_confirm_waits_for_published_completion and issue81_double_click_creates_one_operation ${width}`, async ({ page, request }, testInfo) => {
  const label = `issue81-publication-${width}`;
  const { repertoireId, source } = await importTransitionRepertoire(request, label);
  await page.setViewportSize({ width, height: 844 }); await page.goto("/"); await nav(page, "Repertoire");
  const plans: ReturnType<typeof prefixTransitionPlanSchema.parse>[] = [];
  const deliveries: { operationId: string; body: unknown }[] = [];
  page.on("response", async response => {
    if (response.url().endsWith("/prefix-transition/plan") && response.ok()) plans.push(prefixTransitionPlanSchema.parse(await response.json()));
  });
  page.on("request", incoming => {
    if (incoming.url().endsWith("/prefix-transition/apply")) deliveries.push({ operationId: incoming.headers()["idempotency-key"], body: incoming.postDataJSON() });
  });
  const { dialog } = await openShortening(page, label, source);
  await expect(dialog.getByRole("button", { name: "Confirm shortening", exact: true })).toBeEnabled();
  await expect.poll(() => plans.length).toBe(1);
  const selectedIds = source.lines.filter(line => line.moves[0] === "e2e4").map(line => line.id).sort();
  expect(plans[0].selected_line_ids).toEqual(selectedIds);
  const counts = dialog.locator(".prefix-transition-counts");
  for (const [label, classification] of [["Unchanged", "unchanged"], ["Reused", "reuse_existing"], ["Added", "new_replacement"], ["Retired", "retired"], ["Retained shared", "retained_shared"]])
    await expect(counts.locator("div").filter({ has: page.getByText(label, { exact: true }) }).locator("dd"))
      .toHaveText(String(plans[0].cards.filter(card => card.classification === classification).length));
  await dialog.getByRole("heading", { name: "Confirm shorter prefix transition" }).evaluate(heading => heading.scrollIntoView({ block: "start" }));
  const screenshot = testInfo.outputPath(`issue81-confirmation-${width}.png`);
  await page.screenshot({ path: screenshot }); await testInfo.attach(`confirmation-${width}`, { path: screenshot, contentType: "image/png" });
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).dblclick();
  await expect(dialog.getByText(/Prefix transition published\. Graph, integrity and daily queue are confirmed\./).first()).toBeVisible({ timeout: 40_000 });
  await expect(dialog.getByText("No change from saved depths.", { exact: true })).toBeVisible({ timeout: 20_000 });
  expect(deliveries).toHaveLength(1); expect(plans).toHaveLength(2);
  const status = await request.get(`${api}/operations/${deliveries[0].operationId}`); expect(status.ok()).toBe(true);
  const receipt = await status.json(); expect(receipt.state).toBe("complete"); expect(receipt.transition.state).toBe("complete");
  expect(receipt.response.plan_id).toBe(plans[0].plan_id); expect(receipt.response.operation_id).toBe(deliveries[0].operationId);
  const latestResponse = await request.get(`${api}/repertoires/${repertoireId}/prefix-evaluation/source`);
  expect(latestResponse.ok()).toBe(true); const latest = prefixSourceSchema.parse(await latestResponse.json());
  for (const line of latest.lines) expect(line.saved_depth).toBe(selectedIds.includes(line.id) ? 2 : 3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("issue81_lost_response_reload_recovers_original_operation", async ({ page, request }, testInfo) => {
  const label = "issue81-lost-response";
  const { repertoireId, source } = await importTransitionRepertoire(request, label);
  let operationId = ""; let deliveries = 0;
  await page.route("**/prefix-transition/apply", async route => {
    ++deliveries; operationId = route.request().headers()["idempotency-key"];
    await route.fetch(); // Deliver to the real durable PostgreSQL command, then lose only its HTTP response.
    await route.abort("failed");
  });
  await page.route("**/api/operations/*", route => route.abort("failed"));
  await page.goto("/"); await nav(page, "Repertoire"); const { dialog } = await openShortening(page, label, source);
  // Permit the initial authoritative missing-receipt read, then lose subsequent status responses.
  await page.unroute("**/api/operations/*");
  let statusReads = 0;
  await page.route("**/api/operations/*", route => ++statusReads === 1 ? route.continue() : route.abort("failed"));
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).click();
  await expect.poll(() => deliveries).toBe(1);
  await expect(dialog.getByRole("heading", { name: "Delivery unconfirmed" })).toBeVisible();
  expect(await page.evaluate(id => JSON.parse(localStorage.getItem(`tempo-pending-prefix-transition-v1:${encodeURIComponent(id)}`)!).operationId, repertoireId)).toBe(operationId);
  const screenshot = testInfo.outputPath("issue81-delivery-unconfirmed.png"); await page.screenshot({ path: screenshot });
  await testInfo.attach("delivery-unconfirmed", { path: screenshot, contentType: "image/png" });
  await page.unroute("**/api/operations/*"); await page.reload(); await nav(page, "Repertoire");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Resume prefix transition", exact: true }).click();
  await expect(page.getByText(/Prefix transition published\. Graph, integrity and daily queue are confirmed\./).first()).toBeVisible({ timeout: 40_000 });
  await expect(page.getByText("No change from saved depths.", { exact: true })).toBeVisible({ timeout: 20_000 });
  expect(deliveries).toBe(1); const status = await request.get(`${api}/operations/${operationId}`);
  expect((await status.json()).state).toBe("complete");
});

async function mockTransitionUI(page: Page, kind: "ready" | "blocked" = "ready") {
  const control = { state: "complete", admitted: false, stale: false, concurrent: false, planReads: 0, deliveries: 0, operationId: "", retries: 0 };
  await page.route("**/api/repertoires", route => route.fulfill({ json: { repertoires: [{ id: "rep", name: "Combined Black", source_name: "Combined Black.pgn",
    line_count: 4, card_count: 5, due_count: 0, trained_color: "black" }] } }));
  await page.route("**/api/system/foreground-active", route => route.fulfill({ json: { active: false } }));
  await page.route("**/api/repertoires/rep/prefix-evaluation/**", route => route.fulfill({ json: route.request().method() === "GET"
    ? control.admitted && control.state === "complete" ? transitionFixture.fresh_source : structuralFixture.source
    : control.admitted && control.state === "complete" ? transitionFixture.fresh_comparison : structuralFixture.comparisons["a,b:2"] }));
  await page.route("**/api/repertoires/rep/prefix-transition/plan", route => {
    ++control.planReads;
    if (control.concurrent && control.planReads > 1) return route.fulfill({ status: 409, json: { detail: { code: "stale_snapshot", message: "Source changed concurrently. Refresh and review again." } } });
    return route.fulfill({ json: control.stale && control.planReads > 1 ? { ...transitionFixture[kind], plan_id: "changed" } : transitionFixture[kind] });
  });
  await page.route("**/api/repertoires/rep/prefix-transition/apply", route => {
    ++control.deliveries; control.admitted = true; control.operationId = route.request().headers()["idempotency-key"];
    return route.fulfill({ status: 202, json: { operation_id: control.operationId, state: "pending" } });
  });
  await page.route("**/api/operations/**", route => {
    if (route.request().url().endsWith("/retry")) { ++control.retries; control.state = "complete"; return route.fulfill({ status: 202, json: { state: "blocked" } }); }
    const operationId = route.request().url().split("/").at(-1)!;
    return route.fulfill({ json: { operation_id: operationId, state: control.admitted ? control.state : "unknown",
      transition: { state: control.state === "complete" ? "complete" : control.state === "blocked" ? "recovery_required" : "staging",
        graph_generation: 2, queue_date: control.state === "complete" ? transitionFixture.result.queue_date : null,
        recovery: control.state === "blocked" ? "The activated graph remains committed. Retry publication with this operation." : null },
      response: { ...transitionFixture.result, operation_id: operationId },
      error: control.state === "failed" ? { detail: { code: "staging_failed", message: "Staging failed before activation. Review a fresh plan." } } : undefined } });
  });
  await page.goto("/"); await nav(page, "Repertoire");
  return { control, ...await openShortening(page, "Combined Black", structuralFixture.source) };
}

for (const scenario of ["stale", "concurrent"] as const) test(scenario === "stale" ? "issue81_stale_plan_never_mutates" : "issue81_concurrent_source_change_rejects_confirmation", async ({ page }) => {
  const { control, dialog } = await mockTransitionUI(page); control[scenario] = true;
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Stale transition");
  await expect(dialog.getByRole("button", { name: "Confirm shortening", exact: true })).toBeDisabled();
  expect(control.deliveries).toBe(0);
});
test("issue81_unsupported_shortening_preserves_original_repertoire", async ({ page }) => {
  const { control, dialog } = await mockTransitionUI(page, "blocked");
  await expect(dialog.getByRole("alert")).toContainText("original repertoire remains available for study");
  await expect(dialog.getByRole("button", { name: "Confirm shortening", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Back to comparison" }).click();
  await expect(dialog.getByRole("region", { name: "Candidate depth 2", exact: true })).toBeVisible();
  expect(control.deliveries).toBe(0);
});
test("issue81_pending_survives_remount", async ({ page }) => {
  const { control, dialog, card } = await mockTransitionUI(page); control.state = "pending";
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Prefix transition in progress" })).toBeVisible();
  const operationId = control.operationId; await dialog.getByRole("button", { name: "Close window" }).click();
  await expect(page.getByRole("button", { name: "Resume prefix transition", exact: true })).toBeVisible();
  await card.locator("details.card-menu summary").click();
  for (const name of ["Rename", "Canonical prefix…", "Delete"]) await expect(card.getByRole("menuitem", { name, exact: true })).toBeDisabled();
  await card.locator("details.card-menu summary").click();
  await page.getByRole("button", { name: "Resume prefix transition", exact: true }).click();
  await expect(dialog.getByText("Selected source routes: 2", { exact: false })).toBeVisible();
  await expect(dialog.getByLabel("Candidate learner-decision depths")).toHaveCount(0);
  expect(control.operationId).toBe(operationId); expect(control.deliveries).toBe(1);
});
test("issue81_terminal_failure_offers_actionable_recovery", async ({ page }) => {
  const { control, dialog } = await mockTransitionUI(page); control.state = "failed";
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Prefix transition failed" })).toBeVisible();
  await expect(dialog.getByText(/Staging failed before activation/)).toBeVisible();
  await dialog.getByRole("button", { name: "Review a fresh comparison" }).click();
  await expect(dialog.getByText("Selected source lines: 0", { exact: true })).toBeVisible();
  expect(control.deliveries).toBe(1);
});
test("issue81_blocked_publication_retries_the_original_operation", async ({ page }) => {
  const { control, dialog } = await mockTransitionUI(page); control.state = "blocked";
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).click();
  await expect(dialog.getByText(/activated graph remains committed/)).toBeVisible(); const operationId = control.operationId;
  await dialog.getByRole("button", { name: "Retry publication recovery" }).click();
  await expect(dialog.getByText("No change from saved depths.", { exact: true })).toBeVisible();
  expect(control.retries).toBe(1); expect(control.deliveries).toBe(1); expect(control.operationId).toBe(operationId);
});
test("issue81_mobile_confirmation_and_recovery_remain_usable and issue81_confirmation_matches_exact_scope_and_counts", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 }); const { dialog } = await mockTransitionUI(page);
  await expect(dialog.getByRole("region", { name: "Exact transition consequences" })).toContainText("Selected source routes: 2");
  await expect(dialog.locator(".prefix-transition-counts")).toContainText("Unchanged4Reused0Added1Retired1Retained shared0");
  await expect(dialog.getByText(/b · learner-decision depth 3 → 2/)).toBeVisible();
  await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).scrollIntoViewIfNeeded();
  const button = await dialog.getByRole("button", { name: "Confirm shortening", exact: true }).boundingBox(); expect(button!.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  const screenshot = testInfo.outputPath("issue81-mobile-confirmation.png"); await page.screenshot({ path: screenshot });
  await testInfo.attach("mobile-confirmation", { path: screenshot, contentType: "image/png" });
  await page.keyboard.press("Escape"); await expect(dialog).toHaveCount(0);
});
