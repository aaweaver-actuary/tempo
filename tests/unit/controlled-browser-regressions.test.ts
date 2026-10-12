// @vitest-environment node
import { afterEach, expect, it } from "vitest";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { startControlledHost, controlledEnvironment } from "../../scripts/controlled-browser-host.mjs";
import { verifyPilotSource, verifyPilotResults, pilotLedger } from "../../scripts/controlled-browser-inventory.mjs";
import type { BrowserContext, Route } from "@playwright/test";
import { ControlledApi } from "../controlled-browser/strict-api";
import { matchesContract, validateReview, receiptResponse } from "../controlled-browser/contracts";
import { backgroundDiagnosticsSchema } from "../../app/domain/schemas/background-diagnostics";
import { resolvePython } from "../../scripts/resolve-python.mjs";
import { queueEnvelopeSchema, queueCardSchema, settingsResponseSchema, repertoiresResponseSchema,
  repertoireLinesResponseSchema, motifRecommendationsSchema, tacticProgressSchema, syncStatusSchema } from "../../app/domain/schemas";

const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0).reverse()) await cleanup(); });
async function ownedHost() {
  const directory = await mkdtemp(join(tmpdir(), "tempo-b01-host-"));
  cleanups.push(() => rm(directory, { recursive: true, force: true }));
  await writeFile(join(directory, "index.html"), "local product shell");
  const host = await startControlledHost(directory, "unit-owned-host");
  cleanups.push(() => host.close());
  return host;
}
it("controlled_browser_unexpected_api_request_fails_without_live_fallback", async () => {
  const host = await ownedHost();
  for (const path of ["/api", "/api/unknown?extra=1", "/api%2Funknown"]) {
    const response = await fetch(`${host.origin}${path}`);
    expect(response.status).toBe(501);
    expect(await response.text()).not.toContain("local product shell");
  }
  expect(host.apiEscapes).toHaveLength(3);
  const api = new ControlledApi(host.origin);
  let intercept!: (route: Route) => Promise<void>;
  await api.install({ route: async (_pattern: string, handler: typeof intercept) => { intercept = handler; } } as unknown as BrowserContext);
  let aborted = false;
  await intercept({ request: () => ({ url: () => `${host.origin}/api/missing`, method: () => "GET", headers: () => ({}), postData: () => null }), abort: async () => { aborted = true; } } as unknown as Route);
  expect(aborted).toBe(true);
  expect(() => api.assertClean()).toThrow("GET");
  const contract = { method: "GET", path: "/api/queue/window", query: { limit: "20" } };
  const input = { url: `${host.origin}/api/queue/window?limit=20`, method: "GET", headers: {}, body: null };
  expect(matchesContract(contract, input, host.origin)).toBe(true);
  for (const change of [{ method: "POST" }, { url: `${input.url}&unknown=1` }, { url: `${input.url}&limit=20` },
    { url: "http://127.0.0.1:8000/api/queue/window?limit=20" }, { url: `${host.origin}/api/queue/window?limit=10` }])
    expect(matchesContract(contract, { ...input, ...change }, host.origin)).toBe(false);
});
it("controlled_browser_local_host_preserves_product_mode_and_contract_errors", async () => {
  const host = await ownedHost();
  expect(await (await fetch(`${host.origin}/__controlled_host/ready`)).json()).toEqual({ host: "controlled-frontend-only", run_id: "unit-owned-host", backend_services: [] });
  expect(await (await fetch(host.origin)).text()).toBe("local product shell");
  expect((await fetch(`${host.origin}/missing.js`)).status).toBe(404);
  expect((await fetch(host.origin, { method: "POST" })).status).toBe(405);
  const environment = controlledEnvironment({ PATH: "/bin", HOME: "/tmp/user", DATABASE_URL: "secret", TEMPO_DOCKER_URL: "live", TEMPO_PROXY_API: "live", NEXT_PUBLIC_API_URL: "live", TEMPO_REDIS_URL: "secret", PGHOST: "live" });
  expect(environment).toEqual({ PATH: "/bin", HOME: "/tmp/user", TZ: "America/New_York" });
  const config = readFileSync("vite.controlled.config.ts", "utf8");
  expect(config).toContain('__TEMPO_DEMO__: "false"');
  expect(config).toContain('__TEMPO_API_URL__: JSON.stringify("")');
  expect(config).not.toMatch(/proxy|8000|vite\.static/);
});
it("controlled_browser_review_receipt_uses_original_intent_and_authorized_continuation", async () => {
  const request = { method: "POST", url: "http://127.0.0.1:1234/api/cards/card-a/review", headers: { "idempotency-key": "review-attempt:original" },
    body: { outcome: "correct", guided: false, queue_entry_id: 41, attempt_id: "original", recorded_at: "2026-09-18T16:00:00Z" } };
  expect(() => validateReview(request, "card-a", 41)).not.toThrow();
  for (const bodyChange of [{ attempt_id: "replaced" }, { queue_entry_id: 42 }, { unexpected: true }])
    expect(() => validateReview({ ...request, body: { ...request.body, ...bodyChange } }, "card-a", 41)).toThrow("Original review intent");
  const { readOperationResponse, PendingOperationError, FailedOperationError } = await import("../../app/lib/operation-status");
  for (const state of ["queued", "pending", "executing", "retrying", "blocked"])
    await expect(readOperationResponse("review-attempt:original", { fetch: async () => Response.json({ state }) })).rejects.toBeInstanceOf(PendingOperationError);
  await expect(readOperationResponse("review-attempt:original", { fetch: async () => Response.json({ state: "failed", error: { status_code: 409, code: "queue_attempt_retired", retryable: false } }) })).rejects.toBeInstanceOf(FailedOperationError);
  const continuation = { queue_entry_id: 42, card_id: "card-b", revision: 1, cycle: 0, local_date: "2026-09-18" };
  const response = await readOperationResponse("review-attempt:original", { fetch: async () => Response.json({ state: "complete", response: { persisted: true, prepared_continuation: continuation } }) });
  expect(await response.json()).toEqual({ persisted: true, prepared_continuation: continuation });
});
it("controlled_browser_fixture_provider_consumer_shape_parity", () => {
  const producer = JSON.parse(execFileSync(resolvePython(), ["tests/fixtures/controlled-browser/generate.py"], {
    env: { ...controlledEnvironment(process.env), NODE_ENV: "test", PYTHONPATH: "backend" }, encoding: "utf8", timeout: 30_000,
  }));
  const receipts = JSON.parse(execFileSync(resolvePython(), ["tests/fixtures/controlled-browser/generate.py", "--receipts"], {
    env: { ...controlledEnvironment(process.env), NODE_ENV: "test", PYTHONPATH: "backend" }, encoding: "utf8", timeout: 30_000,
  }));
  expect(receipts).toEqual(JSON.parse(readFileSync("tests/fixtures/controlled-browser/receipts.json", "utf8")));
  for (const state of ["queued", "pending", "executing", "retrying", "blocked", "failed", "complete", "unknown"] as const)
    expect(receiptResponse(state, "review-attempt:new" )).toEqual({ ...receipts[state], operation_id: "review-attempt:new" });
  const fixture = JSON.parse(readFileSync("tests/fixtures/controlled-browser/startup.json", "utf8"));
  expect(Object.keys(producer).filter(path => path in fixture).sort()).toEqual(Object.keys(fixture).sort());
  for (const path of Object.keys(fixture)) expect(fixture[path], path).toEqual(producer[path]);
  backgroundDiagnosticsSchema.parse(producer["/api/system/background-diagnostics"]);
  settingsResponseSchema.parse(producer["/api/settings"]);
  expect(settingsResponseSchema.safeParse({ ...producer["/api/settings"], new_cards_per_day: "10" }).success).toBe(false);
  expect(queueEnvelopeSchema.safeParse({ cards: [], count: -1 }).success).toBe(false);
  repertoiresResponseSchema.parse(producer["/api/repertoires"]);
  repertoireLinesResponseSchema.parse(producer["/api/repertoire/lines"]);
  queueEnvelopeSchema.parse(producer["/api/queue/window"]);
  for (const card of producer["/api/queue/window"].cards) queueCardSchema.parse(card);
  syncStatusSchema.parse(producer["/api/games/sync/status"]);
  motifRecommendationsSchema.parse(producer["/api/game-insights/motifs"]);
  tacticProgressSchema.parse(producer["/api/tactics/progress"]);
});
it("controlled_browser_movement_preserves_exact_case_inventory", () => {
  expect(verifyPilotSource()).toHaveLength(9);
  const report = { suites: [{ specs: pilotLedger.cases.map((entry: { newFile: string; newTitle: string; projects: string[] }) => ({ file: entry.newFile, title: entry.newTitle,
    tests: entry.projects.map(projectName => ({ projectName, expectedStatus: "passed", results: [{ status: "passed" }] })) })) }], errors: [] };
  expect(verifyPilotResults(report)).toHaveLength(9);
  for (const mutate of [(r: typeof report) => r.suites[0].specs.pop(), (r: typeof report) => r.suites[0].specs.push(r.suites[0].specs[0]),
    (r: typeof report) => { r.suites[0].specs[0].tests[0].results[0].status = "skipped"; }, (r: typeof report) => { r.suites = []; }]) {
    const badReport = structuredClone(report); mutate(badReport); expect(() => verifyPilotResults(badReport)).toThrow();
  }
});
it("controlled_browser_owned_port_closes_and_cannot_be_reused_as_a_live_server", async () => {
  const host = await ownedHost();
  await cleanups.pop()!();
  await expect(fetch(host.origin)).rejects.toThrow();
});

it("controlled_browser_delayed_response_waits_for_explicit_release", async () => {
  const api = new ControlledApi("http://127.0.0.1:1234");
  let intercept!: (route: Route) => Promise<void>;
  await api.install({ route: async (_pattern: string, handler: typeof intercept) => { intercept = handler; } } as unknown as BrowserContext);
  let release!: () => void;
  const deferred = new Promise<void>(resolve => { release = resolve; });
  let fulfilled = false;
  api.register({ method: "GET", path: "/api/settings", query: {} }, async route => { await deferred; await route.fulfill({ status: 503, json: { detail: "Controlled unavailable service" } }); });
  const handling = intercept({ request: () => ({ url: () => "http://127.0.0.1:1234/api/settings", method: () => "GET", headers: () => ({}), postData: () => null }), fulfill: async () => { fulfilled = true; } } as unknown as Route);
  await Promise.resolve();
  expect(fulfilled).toBe(false);
  release(); await handling;
  expect(fulfilled).toBe(true); api.assertClean();
});
