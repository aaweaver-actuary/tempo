import { build } from "esbuild";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { BrowserContext, CDPSession, Page, TestInfo, APIRequestContext } from "@playwright/test";
import { expect } from "./observability";
import { api } from "./product-fixtures";
import type { PendingReview } from "../../app/lib/review-outbox";

declare global {
  interface Window {
    tempoReviewOutbox: typeof import("../../app/lib/review-outbox");
    pauseReviewWrite: boolean;
    pauseReviewWriteState?: string;
    reviewWriteObservations: { replacement: PendingReview[]; before: PendingReview[] }[];
  }
}
export const reviewStorageKey = "tempo-pending-training-reviews-v1";
const consumers = ["background-worker", "background-scheduler", "defense-engine", "maia-worker"];
export type Issued = { queue_entry_id: number; card_id: string; revision: number; cycle: number; local_date: string };
export type History = { cards: unknown[][]; reviews: unknown[][]; receipts: unknown[][]; operations: unknown[][]; origins: unknown[][] };

export async function reviewFixture(request: APIRequestContext) {
  const project = process.env.TEMPO_TEST_COMPOSE_PROJECT;
  if (!project || !/^tempo-pg-regressions-\d+-[a-f0-9]+$/.test(project)) throw new Error("An owning disposable PostgreSQL runner is required");
  const identifier = `training-transition-${randomUUID()}`;
  const docker = (...arguments_: string[]) => promisify(execFile)("docker", ["compose", "-p", project,
    "-f", "docker-compose.postgres.test.yml", ...arguments_], { encoding: "utf8", timeout: 60_000 });
  const fixture = async (action: string, ...arguments_: string[]) => (await docker("run", "--rm", "--no-deps", "schema",
    "python", "/source/scripts/check_postgres_review_outbox.py", action, identifier, ...arguments_)).stdout.trim().split("\n").filter(line => line.startsWith("{" )).map(line => JSON.parse(line));
  const waitForRestoredReadiness = async () => {
    await expect.poll(async () => {
      try { return (await (await request.get(`${api}/health`)).json()).test_instance; }
      catch { return false; }
    }, { timeout: 60_000, message: "Owned fixture must restore actual disposable API readiness" }).toBe(true);
  };
  await waitForRestoredReadiness();
  const health = await request.get(`${api}/health`);
  expect((await health.json()).test_instance).toBe(true);
  const date = (await (await request.get(`${api}/queue/window?limit=20`)).json()).local_date;
  await docker("stop", ...consumers);
  let displaced: number[][] = [];
  try {
    const seeded = await fixture("seed", date);
    displaced = seeded[0].displaced_queue_positions;
    const issued = seeded[1].issued as Issued[];
    return { project, identifier, issued,
      retireSecond: async () => { await fixture("retire-second"); },
      observe: async () => (await fixture("observe"))[0] as History,
      cleanup: async () => {
        try { await fixture("cleanup", JSON.stringify(displaced)); }
        finally { await docker("start", ...consumers); await waitForRestoredReadiness(); }
      } };
  } catch (error) {
    try { await fixture("cleanup", JSON.stringify(displaced)); }
    finally { await docker("start", ...consumers); await waitForRestoredReadiness(); }
    throw error;
  }
}

export function envelope(identity: Issued, outcome: "again" | "correct" = "correct"): PendingReview {
  return { backendId: identity.card_id, queueEntryId: identity.queue_entry_id, expectedRevision: identity.revision,
    attemptId: randomUUID(), outcome, guided: false, completedAt: new Date().toISOString() };
}

let harnessBundle: Promise<string> | undefined;
export async function openOutboxHarness(page: Page) {
  harnessBundle ??= build({ entryPoints: ["app/lib/review-outbox.ts"], bundle: true, write: false, format: "iife",
    globalName: "tempoReviewOutbox", platform: "browser", define: { __TEMPO_API_URL__: '""' } })
    .then(result => result.outputFiles[0].text);
  // The actual application module and dependencies run against native storage;
  // this avoids duplicating its mutation rules or adding a production test API.
  await page.route("**/__review-outbox-audit", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Review outbox audit</title>" }));
  await page.goto("/__review-outbox-audit");
  await page.addScriptTag({ content: await harnessBundle });
}

export async function instrumentReviewWrites(page: Page) {
  await page.addInitScript(storageKey => {
    const nativeSetItem = Storage.prototype.setItem;
    window.reviewWriteObservations = [];
    Storage.prototype.setItem = function (key, replacement) {
      if (key === storageKey) {
        // Arguments have already been computed by the real read-modify-write.
        // debugger suspends only execution, never supplies a fabricated read.
        if (window.pauseReviewWrite && (!window.pauseReviewWriteState || JSON.parse(replacement).some((review: PendingReview) => review.state === window.pauseReviewWriteState))) { window.pauseReviewWrite = false; debugger; }
        window.reviewWriteObservations.push({ replacement: JSON.parse(replacement), before: JSON.parse(localStorage.getItem(key) ?? "[]") });
      }
      return nativeSetItem.call(this, key, replacement);
    };
  }, reviewStorageKey);
}

type Pause = { callFrames: { callFrameId: string; functionName: string; url: string }[] };
export async function storageBarrier(context: BrowserContext, page: Page) {
  const debuggerSession: CDPSession = await context.newCDPSession(page);
  await debuggerSession.send("Debugger.enable");
  let waiting: ((pause: Pause) => void) | undefined;
  let pending: Pause | undefined;
  debuggerSession.on("Debugger.paused", (pause: Pause) => {
    if (waiting) { const resolve = waiting; waiting = undefined; resolve(pause); }
    else pending = pause;
  });
  return {
    arm: async (state?: string) => { await page.evaluate(state => { window.pauseReviewWriteState = state; window.pauseReviewWrite = true; }, state); },
    capture: async () => {
      const pause = await new Promise<Pause>((resolve, reject) => {
        const deadline = setTimeout(() => { waiting = undefined; reject(new Error("Independent native storage pause was not reached within 10s; ordering is inconclusive")); }, 10_000);
        const deliver = (value: Pause) => { clearTimeout(deadline); resolve(value); };
        if (pending) { const value = pending; pending = undefined; deliver(value); }
        else waiting = deliver;
      });
      const captured = await debuggerSession.send("Debugger.evaluateOnCallFrame", { callFrameId: pause.callFrames[0].callFrameId,
        expression: "JSON.parse(replacement)", returnByValue: true });
      if (captured.exceptionDetails) throw new Error(JSON.stringify(captured.exceptionDetails));
      return { replacement: captured.result.value as PendingReview[], stack: pause.callFrames.map(frame => ({ functionName: frame.functionName, url: frame.url })) };
    },
    release: () => debuggerSession.send("Debugger.resume"),
    close: async () => { await debuggerSession.send("Debugger.resume").catch(() => undefined); await debuggerSession.detach(); },
  };
}

export const readReviews = (page: Page) => page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "[]") as PendingReview[], reviewStorageKey);
export async function saveProof(testInfo: TestInfo, proof: object) {
  const serialized = JSON.stringify({ sourceRevision: (await promisify(execFile)("git", ["rev-parse", "HEAD"], { encoding: "utf8" })).stdout.trim(),
    productionChanges: false, ...proof }, null, 2);
  const directory = process.env.TEMPO_TEST_TIMING_DIR ?? "test-results/performance";
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, `issue221-${testInfo.title.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}.json`), serialized);
  await testInfo.attach("issue221-native-proof", { body: serialized, contentType: "application/json" });
}
