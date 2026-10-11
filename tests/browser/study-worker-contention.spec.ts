import { test, expect } from "./observability";
import { prepareVisualUI, type VisualRepertoireLine } from "./visual-fixtures";
import { navigate, prepareUI } from "./ui-fixtures";
import { test as productTest, api, move } from "./product-fixtures";
import { readFileSync, mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";

type WorkerEvent = { id: number; kind: string; state: string; at: number; resource?: string };
type ProbeWindow = Window & { workerInvestigation: { events: WorkerEvent[]; navigateDuringIndex: boolean; navigationAt?: number } };
const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const sourceFile = process.env.TEMPO_INVESTIGATION_SNAPSHOT;
const uiSamples = Number(process.env.TEMPO_INVESTIGATION_UI_SAMPLES ?? 1);
const repertoireLines: VisualRepertoireLine[] = sourceFile
  ? JSON.parse(readFileSync(sourceFile, "utf8")).lines
  : Array.from({ length: 1000 }, (_, index) => ({ id: `contention-${index}`, repertoire_id: "visual-repertoire", repertoire_name: "Contention", name: "Contention", trained_color: "white" as const, start_fen: startingFen, moves: ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6"] }));

async function observeWorker(page: Page, navigateDuringIndex = false) {
  await page.addInitScript(({ navigateDuringIndex }) => {
    const target = window as unknown as ProbeWindow;
    const probe: ProbeWindow["workerInvestigation"] = target.workerInvestigation = { events: [], navigateDuringIndex };
    const NativeWorker = window.Worker;
    const taskKinds = new Map<number, { kind: string; resource?: string }>();
    class ObservedWorker extends NativeWorker {
      constructor(...args: ConstructorParameters<typeof Worker>) {
        super(...args);
        this.addEventListener("message", event => {
          const task = taskKinds.get(event.data.id);
          if (!task) return;
          const state = event.data.state === "running" ? "running" : "reply";
          probe.events.push({ id: event.data.id, ...task, state, at: performance.now() });
          if (state === "running" && task.kind === "repertoireAlternatives" && probe.navigateDuringIndex) {
            probe.navigationAt = performance.now();
            probe.navigateDuringIndex = false;
            const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('nav[aria-label="Primary navigation"] button'));
            buttons.find(button => button.textContent?.trim() === "Games")?.click();
          }
        });
      }
      postMessage(message: { id: number; task: { kind: string; url?: string } }) {
        const task = { kind: message.task.kind, resource: message.task.url };
        taskKinds.set(message.id, task);
        probe.events.push({ id: message.id, ...task, state: "sent", at: performance.now() });
        super.postMessage(message);
      }
    }
    window.Worker = ObservedWorker;
  }, { navigateDuringIndex });
}

function saveEvidence(name: string, value: unknown) {
  const directory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, name), `${JSON.stringify(value, null, 2)}\n`);
}

test("study_worker_navigation_cancels_publication_but_foreground_games_waits_for_the_real_index", async ({ page }) => {
  test.setTimeout((sourceFile ? 180_000 : 60_000) * Math.max(1, uiSamples + 3));
  await observeWorker(page, true);
  await prepareVisualUI(page, false, undefined, repertoireLines);
  let currentLines = repertoireLines;
  await page.route("**/api/repertoire/lines", route => route.fulfill({ json: { lines: currentLines } }));
  const samples = [];
  for (let iteration = uiSamples > 1 ? -3 : 0; iteration < uiSamples; iteration++) {
   for (const contended of uiSamples > 1 ? (iteration % 2 ? [false, true] : [true, false]) : [true]) {
    currentLines = contended ? repertoireLines : [];
    if (uiSamples > 1) await page.reload();
  await expect(page.getByRole("heading", { name: "Games", exact: true })).toBeVisible({ timeout: sourceFile ? 120_000 : 30_000 });
  await expect(page.getByText("Loading games…", { exact: true })).toHaveCount(0, { timeout: sourceFile ? 120_000 : 30_000 });
  await expect.poll(() => page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events.some(event => event.kind === "repertoireAlternatives" && event.state === "reply")), { timeout: sourceFile ? 120_000 : 30_000 }).toBe(true);
  const evidence = await page.evaluate(() => ({ ...((window as unknown as ProbeWindow).workerInvestigation), usableAt: performance.now() }));
  const running = evidence.events.find(event => event.kind === "repertoireAlternatives" && event.state === "running")!;
  const reply = evidence.events.find(event => event.id === running.id && event.state === "reply")!;
  const foreground = evidence.events.find(event => event.state === "sent" && event.resource?.includes("/games/summary") && event.at >= running.at);
  expect(foreground).toBeDefined();
  const foregroundRunning = evidence.events.find(event => event.id === foreground!.id && event.state === "running")!;
  if (contended) {
    expect(foreground!.at).toBeLessThan(reply.at);
    expect(foregroundRunning.at).toBeGreaterThanOrEqual(reply.at);
  }
  expect(evidence.navigationAt).toBeGreaterThanOrEqual(running.at);
  const sample = { ...evidence, iteration, warmup: iteration < 0, contended, navigationToUsableMs: evidence.usableAt - evidence.navigationAt! };
  samples.push(sample);
  saveEvidence("study-worker-navigation.json", samples);
  const directory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
  appendFileSync(join(directory, "study-worker-navigation.jsonl"), `${JSON.stringify(sample)}\n`);
   }
  }
});

test("study_worker_ordinary_reviews_reuse_the_accepted_repertoire_snapshot", async ({ page }) => {
  test.setTimeout(sourceFile ? 180_000 : 60_000);
  await observeWorker(page);
  const cards = Array.from({ length: uiSamples > 1 ? uiSamples + 4 : 4 }, (_, index) => ["e2e4", "d2d4", "g1f3", "c2c4"][index % 4]).map((uci, index) => ({ id: `contention-card-${index}`, queue_entry_id: index + 1, start_fen: startingFen, moves: [uci], content_type: "opening" as const, repertoire_name: `Contention card ${index}`, repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const }));
  await prepareVisualUI(page, false, cards, repertoireLines);
  await expect.poll(() => page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events.some(event => event.kind === "repertoireAlternatives" && event.state === "reply")), { timeout: sourceFile ? 120_000 : 30_000 }).toBe(true);
  const samples = [];
  const moveSamples = [];
  for (let index = 1; index < cards.length; index++) {
    const moveStartedAt = await page.evaluate(() => performance.now());
    const expectedMove = cards[index - 1].moves[0];
    await move(page, expectedMove.slice(0, 2), expectedMove.slice(2, 4));
    await expect(page.locator(".board-frame").first()).not.toHaveAttribute("data-fen", startingFen);
    const moveVisibleAt = await page.evaluate(() => new Promise<number>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now())))));
    moveSamples.push(moveVisibleAt - moveStartedAt);
    const startedAt = await page.evaluate(() => performance.now());
    await page.getByRole("button", { name: "Correct", exact: true }).click();
    await expect(page.locator(".opening-title h2")).toHaveText(cards[index].repertoire_name);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    const completedAt = await page.evaluate(() => new Promise<number>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now())))));
    samples.push(completedAt - startedAt);
  }
  const events = await page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events);
  expect(events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(1);
  saveEvidence("study-worker-ordinary-progression-with-moves.json", { samples, measuredSamples: uiSamples > 1 ? samples.slice(3) : samples, moveSamples, measuredMoveSamples: uiSamples > 1 ? moveSamples.slice(3) : moveSamples, measurementBoundary: "Playwright move helper start to changed board and two animation frames; includes helper assertions and pointer interaction", excludedWarmups: uiSamples > 1 ? 3 : 0, events, reviews: cards.length - 1 });
});

test("study_worker_alternative_moves_use_accepted_membership_without_worker_reconstruction", async ({ page }) => {
  await observeWorker(page);
  const card = { id: "membership-card", queue_entry_id: 88001, start_fen: startingFen, moves: ["e2e4"], content_type: "opening" as const, repertoire_id: "visual-repertoire", repertoire_name: "Membership", repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const };
  const alternative = { id: "membership-line", repertoire_id: "visual-repertoire", repertoire_name: "Membership", name: "Alternative", trained_color: "white" as const, start_fen: startingFen, moves: ["d2d4"] };
  await prepareVisualUI(page, false, [card], [alternative]);
  await expect.poll(() => page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events.some(event => event.kind === "repertoireAlternatives" && event.state === "reply"))).toBe(true);
  const samples = [];
  for (let iteration = uiSamples > 1 ? -3 : 0; iteration < uiSamples; iteration++) {
    const startedAt = await page.evaluate(() => performance.now());
    await move(page, "d2", "d4");
    await expect(page.getByText(/Valid repertoire move · follow the arrow/)).toBeVisible();
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", startingFen);
    const completedAt = await page.evaluate(() => new Promise<number>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now())))));
    samples.push({ iteration, warmup: iteration < 0, durationMs: completedAt - startedAt });
  }
  const events = await page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events);
  expect(events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(1);
  saveEvidence("study-worker-alternative-moves.json", { samples, events, scope: "public single-line repertoire; accepted snapshot, repeated native pointer moves; includes helper assertions and two animation frames" });
});

productTest("study_worker_real_postgres_review_refreshes_without_rebuilding_repertoire_evidence", async ({ page, request }) => {
  productTest.setTimeout(120_000);
  const imported = await request.post(`${api}/imports/pgn`, { multipart: { file: { name: "study-worker-investigation.pgn", mimeType: "application/x-chess-pgn", buffer: Buffer.from('[Event "Investigation"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 *') }, trained_color: "white", initial_depth: "2" } });
  expect(imported.ok(), await imported.text()).toBe(true);
  let importResult = await imported.json();
  if (imported.status() === 202) {
    await expect.poll(async () => {
      const receipt = await (await request.get(`${api}/operations/${importResult.operation_id}`)).json();
      if (receipt.state === "complete") importResult = receipt.response;
      return receipt.state;
    }, { timeout: 30_000 }).toBe("complete");
  }
  const quota = await request.put(`${api}/repertoires/${importResult.repertoire_id}/settings`, { data: { new_cards_per_day: 2 } });
  expect(quota.ok(), await quota.text()).toBe(true);
  if (quota.status() === 202) {
    const { operation_id: operationId } = await quota.json();
    await expect.poll(async () => (await (await request.get(`${api}/operations/${operationId}`)).json()).state, { timeout: 30_000 }).toBe("complete");
  }
  // Establish fixture publication before measuring a browser workflow. Loading
  // during a pending import tests queue recovery rather than worker contention.
  await expect.poll(async () => {
    const queue = await request.get(`${api}/queue/window?limit=20`, { headers: { "X-Tempo-Work-Class": "background" } });
    if (!queue.ok()) return 0;
    return (await queue.json()).cards.length;
  }, { timeout: 30_000, message: "Imported study fixture is published before browser measurement" }).toBeGreaterThan(0);
  await observeWorker(page);
  await prepareUI(page);
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true", { timeout: 30_000 });
  await expect.poll(() => page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events.some(event => event.kind === "repertoireAlternatives" && event.state === "reply"))).toBe(true);
  await move(page, "e2", "e4");
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /4p3\/4P3/);
  const responsePromise = page.waitForResponse(response => response.url().includes("/review") && response.request().method() === "POST");
  const startedAt = await page.evaluate(() => performance.now());
  await move(page, "g1", "f3");
  const response = await responsePromise;
  expect(response.ok(), await response.text()).toBe(true);
  await expect(page.getByText("Result saved.", { exact: true })).toHaveCount(0);
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", /2n5/);
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
  const events = await page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events);
  expect(events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(1);
  const finishedAt = await page.evaluate(() => performance.now());
  saveEvidence("study-worker-postgres-review.json", { finalMoveHelperToNextInteractiveMs: finishedAt - startedAt, events, responseStatus: response.status(), fixturePreparation: "Import receipt confirmed and explicit per-repertoire quota enabled before measuring; no direct database seeding" });
  await navigate(page, "Games");
});
