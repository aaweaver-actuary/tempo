import { test, expect } from "./observability";
import { prepareVisualUI, type VisualRepertoireLine } from "./visual-fixtures";
import { navigate, prepareUI } from "./ui-fixtures";
import { test as productTest, api, move, Chess } from "./product-fixtures";
import { readFileSync, mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";

type WorkerEvent = { id: number; kind: string; state: string; at: number; resource?: string };
type ProbeWindow = Window & { workerInvestigation: { events: WorkerEvent[]; navigateDuringIndex: boolean; navigationAt?: number } };
type PhaseSample = { operationId: number; operation: string; edge: string; atMs: number };
type PerformanceProbeWindow = Window & { tempoPerformance?: { snapshot: () => { sessions: Array<{ phases: PhaseSample[] }> } } };
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

async function captureStudyPhases(page: Page) {
  return page.evaluate(() => {
    const phases = (window as unknown as PerformanceProbeWindow).tempoPerformance?.snapshot().sessions.flatMap(session => session.phases) ?? [];
    return [...new Map(phases.map(phase => [`${phase.operationId}:${phase.edge}`, phase])).values()];
  });
}

async function enableStudyTiming(page: Page) {
  await page.addInitScript(() => {
    const url = new URL(location.href);
    url.searchParams.set("tempoPerformance", "drag");
    history.replaceState({}, "", url);
  });
}

async function dragExpectedMoveWithTiming(page: Page, from: string, to: string) {
  const board = page.locator(".board-frame").first();
  await expect(board).toHaveAttribute("data-input-enabled", "true");
  const bounds = (await board.locator(".cg-wrap").boundingBox())!;
  const black = await board.getAttribute("data-orientation") === "black";
  const center = (square: string) => {
    const file = square.charCodeAt(0) - 97;
    const rank = Number(square[1]) - 1;
    return { x: bounds.x + ((black ? 7 - file : file) + 0.5) * bounds.width / 8,
      y: bounds.y + ((black ? rank : 7 - rank) + 0.5) * bounds.height / 8 };
  };
  const origin = center(from);
  const destination = center(to);
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down();
  await page.mouse.move(destination.x, destination.y, { steps: 4 });
  await expect(board.locator("piece.dragging")).toHaveCount(1);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await page.mouse.up();
  await expect(board.locator("piece.dragging")).toHaveCount(0);
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
  await enableStudyTiming(page);
  const cards = Array.from({ length: uiSamples > 1 ? uiSamples + 4 : 4 }, (_, index) => ["e2e4", "d2d4", "g1f3", "c2c4"][index % 4]).map((uci, index) => ({ id: `contention-card-${index}`, queue_entry_id: index + 1, start_fen: startingFen, moves: [uci], content_type: "opening" as const, repertoire_name: `Contention card ${index}`, repertoire_source: "PGN", first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const }));
  await prepareVisualUI(page, false, cards, repertoireLines);
  await expect.poll(() => page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events.some(event => event.kind === "repertoireAlternatives" && event.state === "reply")), { timeout: sourceFile ? 120_000 : 30_000 }).toBe(true);
  const samples = [];
  const moveSamples = [];
  const phasesByReview = [];
  for (let index = 1; index < cards.length; index++) {
    const moveStartedAt = await page.evaluate(() => performance.now());
    await page.evaluate(() => performance.clearMeasures("tempo:move-to-paint"));
    const expectedMove = cards[index - 1].moves[0];
    await move(page, expectedMove.slice(0, 2), expectedMove.slice(2, 4));
    await expect(page.locator(".opening-title h2")).toHaveText(cards[index].repertoire_name);
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    const completedAt = await page.evaluate(() => new Promise<number>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now())))));
    samples.push(completedAt - moveStartedAt);
    const moveToPaint = await page.evaluate(() => performance.getEntriesByName("tempo:move-to-paint").at(-1)?.duration);
    expect(moveToPaint).toBeDefined();
    moveSamples.push(moveToPaint!);
    phasesByReview.push((await captureStudyPhases(page)).filter(phase => phase.atMs >= moveStartedAt));
  }
  const events = await page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events);
  expect(events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(1);
  saveEvidence("study-worker-ordinary-progression-with-moves.json", { samples, measuredSamples: uiSamples > 1 ? samples.slice(3) : samples, moveSamples, measuredMoveSamples: uiSamples > 1 ? moveSamples.slice(3) : moveSamples, phasesByReview, measurementBoundary: "Existing tempo:move-to-paint callback-to-rAF proxy; helper-to-next-card includes the product's 750ms feedback pause. Existing review-persistence / next-card-readiness spans remain separate.", excludedWarmups: uiSamples > 1 ? 3 : 0, events, reviews: cards.length - 1 });
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
  await enableStudyTiming(page);
  await prepareUI(page);
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true", { timeout: 30_000 });
  await expect.poll(() => page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events.some(event => event.kind === "repertoireAlternatives" && event.state === "reply"))).toBe(true);
  const initialQueue = (await (await request.get(`${api}/queue/window?limit=20`)).json()).cards as Array<{ start_fen: string; moves: string[]; queue_entry_id: number }>;
  const displayedFen = await page.locator(".board-frame").first().getAttribute("data-fen");
  const activeFixture = initialQueue.find(card => card.start_fen === displayedFen);
  expect(activeFixture).toBeDefined();
  const responsePromise = page.waitForResponse(response => response.url().includes("/review") && response.request().method() === "POST");
  const startedAt = await page.evaluate(() => performance.now());
  const fixtureBoard = new Chess(activeFixture!.start_fen);
  for (const [moveIndex, expectedMove] of activeFixture!.moves.entries()) {
    const playerMove = fixtureBoard.turn() === "w";
    fixtureBoard.move({ from: expectedMove.slice(0, 2), to: expectedMove.slice(2, 4), promotion: expectedMove[4] });
    if (playerMove) await dragExpectedMoveWithTiming(page, expectedMove.slice(0, 2), expectedMove.slice(2, 4));
    if (!playerMove || moveIndex === activeFixture!.moves.length - 1)
      await expect(page.locator(".board-frame").first()).toHaveAttribute("data-fen", fixtureBoard.fen());
  }
  const response = await responsePromise;
  expect(response.ok(), await response.text()).toBe(true);
  await expect(page.getByText("Result saved.", { exact: true })).toHaveCount(0);
  await expect.poll(async () => (await captureStudyPhases(page)).some(phase => phase.operation === "next-card-readiness" && phase.edge === "end" && phase.atMs >= startedAt)).toBe(true);
  await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
  const events = await page.evaluate(() => (window as unknown as ProbeWindow).workerInvestigation.events);
  expect(events.filter(event => event.kind === "repertoireAlternatives" && event.state === "sent")).toHaveLength(1);
  const finishedAt = await page.evaluate(() => performance.now());
  saveEvidence("study-worker-postgres-review.json", { lineHelperToNextInteractiveMs: finishedAt - startedAt, phases: (await captureStudyPhases(page)).filter(phase => phase.atMs >= startedAt), events, responseStatus: response.status(), fixtureMoveCount: activeFixture!.moves.length, fixturePreparation: "Import receipt confirmed and explicit per-repertoire quota enabled before measuring; real expected moves follow authoritative queue contents; no direct database seeding" });
  await navigate(page, "Games");
});
