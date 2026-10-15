// @vitest-environment node
import { it, expect, afterEach } from "vitest";
import { mkdtemp, mkdir, writeFile, readFile, rm, access } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { BrowserContext, Route } from "@playwright/test";
import { ControlledApi } from "../strict-api";
import { parseSelection, verifyControlledResults } from "../../../scripts/controlled-browser-inventory.mjs";
import { runControlledBrowser, collectSelection } from "../../../scripts/test-controlled-browser.mjs";
import { prepareControlledAssets, controlledEnvironment } from "../../../scripts/controlled-browser-host.mjs";
const directories: string[] = [];
afterEach(async () => { for (const directory of directories.splice(0)) await rm(directory, { recursive: true, force: true }); });
async function temporary() { const directory = await mkdtemp(join(tmpdir(), "tempo-controlled-regression-")); directories.push(directory); return directory; }
const selection = () => parseSelection(["interaction.spec.ts", "--project=chromium", "--grep=tablet menu"]);
function report(identities: string[]) {
  return { errors: [], stats: { expected: identities.length, unexpected: 0, flaky: 0, skipped: 0 }, suites: [{ specs: identities.map(identity => {
    const [projectName, file, title] = identity.split("|");
    return { file, title, tests: [{ projectName, expectedStatus: "passed", results: [{ status: "passed", retry: 0 }] }] };
  }) }] };
}
it.each([[], ["--project=chromium"], ["phone.spec.ts"]].map(args => ({ args })))("controlled_browser_collection_matches_nonzero_exact_request $args", ({ args }) => {
  const requested = parseSelection(["--list", ...args]);
  expect(collectSelection(requested)).toEqual(requested.expected);
});
it.each([["missing.spec.ts"], ["phone.spec.ts", "phone.spec.ts"], ["--grep=absent-case"], ["--project=firefox", "phone.spec.ts"], ["--update-snapshots"], ["--grep"], ["--project=chromium", "--project=chromium"]].map(args => ({ args })))("controlled_browser_invalid_or_empty_selection_fails $args", ({ args }) => {
  expect(() => parseSelection(args)).toThrow();
});
it("controlled_browser_result_accounting_rejects_invalid_missing_duplicate_and_retry_reports", () => {
  const expected = selection().expected;
  expect(verifyControlledResults(report(expected), expected)).toEqual(expected);
  for (const invalid of [null, {}, { suites: [], errors: [] }, { ...report(expected), errors: ["caught failure"] }, report([...expected, ...expected]), report([])])
    expect(() => verifyControlledResults(invalid, expected)).toThrow();
  for (const status of ["failed", "skipped", "timedOut", "interrupted"]) {
    const result = report(expected); result.suites[0].specs[0].tests[0].results[0].status = status;
    expect(() => verifyControlledResults(result, expected)).toThrow();
  }
  const retried = report(expected); retried.suites[0].specs[0].tests[0].results[0].retry = 1;
  expect(() => verifyControlledResults(retried, expected)).toThrow();
  const badCounts = report(expected); badCounts.stats.skipped = 1;
  expect(() => verifyControlledResults(badCounts, expected)).toThrow();
});
it.each(["package_build", "production_build", "host_startup", "browser_execution", "invalid_report", "api_escape", "cancelled", "success"])("controlled_browser_runner_cleans_owned_resources_after_%s", async failure => {
  const outputRoot = await temporary();
  const requested = selection();
  const stages: string[] = [];
  const evidence = await runControlledBrowser(requested, {
    outputRoot,
    ...(failure === "host_startup" ? { startHost: async () => { throw new Error("Injected host startup failure"); } } : {}),
    runNode: async (stage: string, _args: string[], environment: Record<string, string>, paths: {buildDirectory: string; reportPath: string}) => {
      stages.push(stage);
      if (stage === failure) throw new Error("Injected child failure " + stage);
      if (stage === "production_build") { await mkdir(paths.buildDirectory); await writeFile(join(paths.buildDirectory, "index.html"), "unit host"); }
      if (stage === "browser_execution") {
        if (failure === "api_escape") await fetch(environment.TEMPO_CONTROLLED_ORIGIN + "/api/escaped");
        if (failure === "cancelled") process.emit("SIGTERM");
        await writeFile(paths.reportPath, failure === "invalid_report" ? "invalid" : JSON.stringify(report(requested.expected)));
      }
    },
  });
  expect(evidence.success).toBe(failure === "success");
  expect(evidence.cleanup).toBe(true);
  await expect(access(join(evidence.runDirectory, "build"))).rejects.toThrow();
  await expect(access(join(evidence.runDirectory, "public"))).rejects.toThrow();
  if (evidence.origin) await expect(fetch(evidence.origin)).rejects.toThrow();
  expect(JSON.parse(await readFile(join(evidence.runDirectory, "evidence.json"), "utf8")).success).toBe(evidence.success);
  expect(stages.length).toBeGreaterThan(0);
  if (failure === "api_escape") expect(evidence.api_escapes).toHaveLength(1);
});
it("controlled_browser_missing_or_changed_real_asset_fails_instead_of_substituting", async () => {
  const root = await temporary();
  await mkdir(join(root, "tests/fixtures/controlled-browser"), { recursive: true });
  await writeFile(join(root, "tests/fixtures/controlled-browser/inputs.json"), JSON.stringify({ publicFiles: [], wasm: {"tempo_core_bg.wasm": "expected"} }));
  await expect(prepareControlledAssets(root, join(root, "output"))).rejects.toThrow("Missing real");
  await mkdir(join(root, "public/tempo-core"), { recursive: true });
  await writeFile(join(root, "public/tempo-core/tempo_core_bg.wasm"), "not a real matching artifact");
  await expect(prepareControlledAssets(root, join(root, "output"))).rejects.toThrow("digest mismatch");
});
it("controlled_browser_child_environment_excludes_proxy_service_and_node_injection", () => {
  const unsafe = Object.fromEntries(["DATABASE_URL", "PGHOST", "REDIS_URL", "TEMPO_PROXY_API", "TEMPO_DOCKER_URL", "TEMPO_BROWSER_API_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NODE_OPTIONS", "PYTHONPATH", "NEXT_PUBLIC_API_URL", "TEMPO_TARGET"].map(name => [name, "forbidden-value"]));
  expect(controlledEnvironment({ ...unsafe, PATH: "/usr/bin" })).toEqual({ PATH: "/usr/bin", TZ: "America/New_York" });
});
it.each(["method", "path", "query", "duplicate-query", "body", "origin", "asset-method", "asset-path", "malformed-body"])("controlled_browser_rejected_traffic_is_fatal_even_if_application_catches_%s", async mismatch => {
  const origin = "http://127.0.0.1:4321";
  const api = new ControlledApi(origin, new Set(["/", "/assets/main.js"]));
  let intercept!: (route: Route) => Promise<void>;
  await api.install({ routeWebSocket: async () => {}, route: async (_: string, handler: typeof intercept) => { intercept = handler; } } as unknown as BrowserContext);
  api.register({ method: "GET", path: "/api/queue/window", query: { limit: "20" } }, () => {});
  let url = origin + "/api/queue/window?limit=20", method = "GET", body: string | null = null;
  if (mismatch === "method") method = "PUT";
  if (mismatch === "path") url = origin + "/api/unexpected?limit=20";
  if (mismatch === "query") url += "&extra=1";
  if (mismatch === "duplicate-query") url += "&limit=20";
  if (mismatch === "body") body = "{}";
  if (mismatch === "malformed-body") body = "invalid json";
  if (mismatch === "origin") url = "https://external.invalid/api/queue/window?limit=20";
  if (mismatch === "asset-method") { url = origin + "/assets/main.js"; method = "POST"; }
  if (mismatch === "asset-path") url = origin + "/unlisted.js";
  let aborted = false;
  await intercept({ request: () => ({ url: () => url, method: () => method, headers: () => ({}), postData: () => body, postDataJSON: () => JSON.parse(body!) }), abort: async () => { aborted = true; } } as unknown as Route);
  expect(aborted).toBe(true);
  expect(() => api.assertClean()).toThrow("Unexpected controlled traffic");
});
it("controlled_browser_websocket_attempt_is_closed_and_fails_traffic_assertion", async () => {
  const api = new ControlledApi("http://127.0.0.1:4321");
  let websocket!: (socket: {url(): string; close(): void}) => void;
  await api.install({ route: async () => {}, routeWebSocket: async (_: string, handler: typeof websocket) => { websocket = handler; } } as unknown as BrowserContext);
  let closed = false;
  websocket({ url: () => "wss://external.invalid/live", close: () => { closed = true; } });
  expect(closed).toBe(true); expect(() => api.assertClean()).toThrow("WebSocket");
});
