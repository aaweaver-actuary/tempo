import { randomUUID, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
import { test, expect } from "./observability";
import { api } from "./product-fixtures";
import { assertDisposableTarget } from "./disposable-target";
import { boardBounds, navigate, prepareUI } from "./ui-fixtures";

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
function percentile(values: number[], fraction: number) {
  const ordered = values.toSorted((left, right) => left - right);
  return ordered[Math.max(0, Math.ceil(fraction * ordered.length) - 1)];
}
function upperBootstrapBound(values: number[], fraction: number) {
  let seed = 190;
  const estimates = Array.from({ length: 1000 }, () => percentile(values.map(() => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return values[seed % values.length];
  }), fraction));
  return percentile(estimates, .95);
}

test("restoration_native_review_latency_and_navigation_are_qualified_against_frozen_baseline", async ({ page, request }, testInfo) => {
  const samples = process.env.TEMPO_RESTORATION_QUALIFICATION === "1" ? 1000 : 4;
  const navigationPairs = samples === 1000 ? 50 : 2;
  test.setTimeout(samples === 1000 ? 3_600_000 : 180_000);
  assertDisposableTarget(await (await request.get(`${api}/health`)).json());
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("Owning disposable runner required");
  const identifier = `training-transition-${randomUUID()}`;
  const compose = ["compose", "-p", project, "-f", "docker-compose.postgres.test.yml"];
  const docker = (...args: string[]) => execFileSync("docker", [...compose, ...args], { encoding: "utf8", timeout: 60_000 });
  const fixture = (action: string, ...args: string[]) => JSON.parse(docker("run", "--rm", "--no-deps", "schema", "python", "/source/scripts/check_postgres_training_transition.py", action, identifier, ...args));
  const originalSettings = await (await request.get(`${api}/settings`)).json();
  const publicLines = Array.from({ length: 8268 }, (_, index) => ({ id: `public-${index}`, repertoire_id: `public-${index % 10}`,
    repertoire_name: "Public synthetic", name: `Line ${index}`, trained_color: "white", start_fen: startFen,
    moves: Array.from({ length: 8 }, () => ["g1f3", "g8f6", "f3g1", "f6g8"]).flat() }));
  const provenance = { project, fixtureSha256: createHash("sha256").update(JSON.stringify(publicLines)).digest("hex"),
    publicLines: publicLines.length, transportMoves: publicLines.reduce((total, line) => total + line.moves.length, 0),
    samples, navigationPairs, warmups: 10, quantile: "nearest rank", confidence: "95% upper bootstrap bound, 1000 resamples, fixed seed 190",
    liveServicesRemainRunning: true, sharedHost: true };
  const observations: { condition: string; reviewMs: number; visibleNextMs: number; index: number }[] = [];
  const navigations: { loaded: boolean; milliseconds: number }[] = [];
  const resourceSamples: { at: string; value: string }[] = [];
  let displaced: number[][] = [];
  let currentIndex = 0;
  let firstDelivery: { url: string; key: string; body: unknown } | undefined;
  let thresholds: { review95: number; review99: number; next95: number; next99: number } | undefined;
  const evidence = () => ({ provenance, thresholds, observations, navigations, resourceSamples });
  await page.addInitScript(() => {
    const NativeWorker = window.Worker;
    Object.assign(window, { tempoBulkProbe: { running: false, starts: 0 } });
    window.Worker = class extends NativeWorker {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        if (options?.name !== "tempo-repertoire-preparation") return;
        const probe = Reflect.get(window, "tempoBulkProbe");
        this.addEventListener("message", event => {
          probe.running = event.data.state === "running";
          if (probe.running) probe.starts++;
        });
        const originalTerminate = this.terminate.bind(this);
        this.terminate = () => { probe.running = false; originalTerminate(); };
      }
    };
  });
  await page.route("**/api/repertoire/lines", async route => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ json: { lines: [...body.lines, ...publicLines] } });
  });
  const switchMode = async (loaded: boolean) => {
    const response = await request.put(`${api}/settings`, { data: { ...originalSettings,
      study_only_mode: !loaded, repertoire_preparation_enabled: true, optional_analysis_and_imports_enabled: false } });
    expect(response.ok()).toBe(true);
    await page.evaluate(() => window.dispatchEvent(new Event("tempo:study-mode-changed")));
    await expect(page.getByText(/Study-only mode is on. Optional/)).toHaveCount(loaded ? 0 : 1);
  };
  const ensureBulkRunning = async () => {
    if (!await page.evaluate(() => Reflect.get(window, "tempoBulkProbe").running)) {
      await navigate(page, "Games");
      await navigate(page, "Train");
    }
    await expect.poll(() => page.evaluate(() => Reflect.get(window, "tempoBulkProbe").running), { timeout: 30_000 }).toBe(true);
  };
  const reviewOne = async (condition: string, measured = true) => {
    const expectedId = `${identifier}-prepared-${String(currentIndex).padStart(3, "0")}`;
    const head = await (await request.get(`${api}/queue/window?limit=1`)).json();
    expect(head.cards[0].id).toBe(expectedId);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(head.cards[0].queue_entry_id));
    const bounds = await boardBounds(page);
    await page.mouse.click(bounds.x + bounds.width * 4.5 / 8, bounds.y + bounds.height * 6.5 / 8);
    await page.mouse.click(bounds.x + bounds.width * 4.5 / 8, bounds.y + bounds.height * 4.5 / 8);
    await expect(page.getByRole("button", { name: "Correct", exact: true })).toBeEnabled();
    const confirmed = page.waitForResponse(response => response.url().endsWith("/review") && response.request().method() === "POST");
    const started = performance.now();
    await page.getByRole("button", { name: "Correct", exact: true }).click();
    const response = await confirmed;
    const payload = await response.json();
    expect(response.ok()).toBe(true);
    expect(payload.persisted).toBe(true);
    const reviewMs = performance.now() - started;
    const continuation = payload.prepared_continuation;
    expect(continuation.card_id).toBe(`${identifier}-prepared-${String(currentIndex + 1).padStart(3, "0")}`);
    await expect(page.locator("#train")).toHaveAttribute("data-queue-entry-id", String(continuation.queue_entry_id));
    await expect(page.locator(".board-frame").first()).toHaveAttribute("data-input-enabled", "true");
    if (!firstDelivery) firstDelivery = { url: response.url(), key: response.request().headers()["idempotency-key"], body: response.request().postDataJSON() };
    if (measured) observations.push({ condition, reviewMs, visibleNextMs: performance.now() - started, index: currentIndex });
    currentIndex++;
  };
  try {
    await request.put(`${api}/settings`, { data: { ...originalSettings, study_only_mode: true, optional_analysis_and_imports_enabled: false } });
    docker("stop", "background-worker", "background-scheduler");
    const today = (await (await request.get(`${api}/queue/window?limit=1`)).json()).local_date;
    displaced = fixture("seed-restoration", today, String(samples * 4 + 11)).displaced_queue_positions;
    docker("start", "background-worker", "background-scheduler");
    await prepareUI(page);
    for (let warmup = 0; warmup < 10; warmup++) await reviewOne("warmup", false);
    for (const condition of ["baseline-a", "baseline-b"]) for (let index = 0; index < samples; index++) await reviewOne(condition);
    const first = observations.filter(row => row.condition === "baseline-a");
    const second = observations.filter(row => row.condition === "baseline-b");
    const reviewNoise = first.map((row, index) => Math.abs(row.reviewMs - second[index].reviewMs));
    const nextNoise = first.map((row, index) => Math.abs(row.visibleNextMs - second[index].visibleNextMs));
    thresholds = { review95: upperBootstrapBound(reviewNoise, .95), review99: upperBootstrapBound(reviewNoise, .99),
      next95: upperBootstrapBound(nextNoise, .95), next99: upperBootstrapBound(nextNoise, .99) };
    writeFileSync(testInfo.outputPath("frozen-baseline.json"), JSON.stringify({ provenance, thresholds, observations }, null, 2));
    for (let block = 0; block < 4; block++) {
      for (const loaded of block % 2 ? [true, false] : [false, true]) {
        await switchMode(loaded);
        const ownedContainers = docker("ps", "-q").trim().split(/\s+/).filter(Boolean);
        resourceSamples.push({ at: new Date().toISOString(), value: execFileSync("docker", ["stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}", ...ownedContainers], { encoding: "utf8" }) });
        for (let index = 0; index < samples / 4; index++) {
          if (loaded) await ensureBulkRunning();
          await reviewOne(loaded ? "restored" : "control");
        }
      }
    }
    for (let pair = 0; pair < navigationPairs; pair++) for (const loaded of pair % 2 ? [true, false] : [false, true]) {
      await navigate(page, "Train"); await switchMode(loaded);
      if (loaded) await ensureBulkRunning();
      const started = performance.now();
      await navigate(page, "Games");
      await expect(page.locator(".games-page")).toBeVisible();
      navigations.push({ loaded, milliseconds: performance.now() - started });
    }
    const verify = fixture("verify");
    expect(verify.reviews).toBe(currentIndex);
    expect(verify.complete_evidence).toBe(currentIndex);
    const retained = firstDelivery!;
    expect((await request.post(retained.url, { headers: { "Idempotency-Key": retained.key }, data: retained.body })).ok()).toBe(true);
    expect(fixture("verify").reviews).toBe(currentIndex);
    const controls = observations.filter(row => row.condition === "control");
    const loaded = observations.filter(row => row.condition === "restored");
    for (const [field, limit95, limit99] of [["reviewMs", thresholds.review95, thresholds.review99], ["visibleNextMs", thresholds.next95, thresholds.next99]] as const) {
      const differences = loaded.map((row, index) => row[field] - controls[index][field]);
      if (samples >= 1000) expect(percentile(differences, .95)).toBeLessThanOrEqual(limit95);
      if (samples >= 1000) expect(percentile(differences, .99)).toBeLessThanOrEqual(limit99);
    }
    const navControls = navigations.filter(row => !row.loaded);
    const navLoaded = navigations.filter(row => row.loaded);
    const navDifferences = navLoaded.map((row, index) => row.milliseconds - navControls[index].milliseconds);
    if (navigationPairs >= 50) expect(percentile(navDifferences, .95)).toBeLessThan(50);
    expect(Math.max(...navDifferences)).toBeLessThan(500);
  } finally {
    writeFileSync(testInfo.outputPath("qualification.json"), JSON.stringify(evidence(), null, 2));
    await request.put(`${api}/settings`, { data: originalSettings });
    docker("start", "foreground-worker", "background-worker", "background-scheduler");
    fixture("cleanup", JSON.stringify(displaced));
  }
});
