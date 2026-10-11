import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as z from "zod";
import { readWorkspaceData, invalidateWorkspaceData, resetWorkspaceCache } from "../../app/lib/workspace-data";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { clearNotificationHistory, notifications } from "../../app/lib/notifications";
import * as performance from "../../app/lib/performance";
import { dataDiagnostics, reportDataDiagnostic } from "../../app/lib/validated-data";

const assetUrl = "/data/issue36-publication.json";
const apiUrl = "http://localhost/api/repertoire/lines";
const cacheKey = (url: string) => `tempo-workspace-cache-v3:${url}`;
type DeferredRead = { resolve: (response: Response) => void; reject: (failure: Error) => void };
let reads: DeferredRead[];
let events: Array<{ state: string; url: string }>;
const captureEvent = (event: Event) => events.push((event as CustomEvent).detail);

beforeEach(() => {
  reads = []; events = [];
  clearDebugErrors(); clearNotificationHistory();
  window.addEventListener("tempo-workspace-data", captureEvent);
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve, reject) => reads.push({ resolve, reject }))));
});
afterEach(() => {
  window.removeEventListener("tempo-workspace-data", captureEvent);
  vi.unstubAllGlobals(); clearDebugErrors(); clearNotificationHistory();
});
const payload = (url: string, version: string) => url === apiUrl ? { lines: [] } : { version };
function stored(url: string) {
  const serialized = localStorage.getItem(cacheKey(url));
  return serialized ? JSON.parse(serialized).data : null;
}

it.each([assetUrl, apiUrl])("issue36_obsolete_success_after_newer_success_cannot_publish (%s)", async url => {
  const oldRead = readWorkspaceData(url);
  invalidateWorkspaceData(url);
  const currentRead = readWorkspaceData(url);
  reads[1].resolve(Response.json(payload(url, "current")));
  await currentRead;
  const currentEvents = [...events];
  reads[0].resolve(Response.json(payload(url, "obsolete")));
  await expect(oldRead).resolves.toEqual(url === apiUrl ? { lines: [] } : { version: "obsolete" });
  expect(stored(url)).toEqual(url === apiUrl ? null : { version: "current" });
  expect(events).toEqual(currentEvents);
  resetWorkspaceCache();
  const subsequentRead = readWorkspaceData(url);
  if (url === assetUrl) expect(await subsequentRead).toEqual({ version: "current" });
  let finishRefresh!: () => void;
  const refreshFinished = new Promise<void>(resolve => { finishRefresh = resolve; });
  const recordDuration = performance.recordTempoDuration;
  vi.spyOn(performance, "recordTempoDuration").mockImplementation((...args) => {
    recordDuration(...args);
    if (args[0] === "workspace-data-ready") finishRefresh();
  });
  reads[2].resolve(Response.json(payload(url, "current")));
  await subsequentRead; await refreshFinished;
});

it.each([assetUrl, apiUrl])("issue36_obsolete_success_before_newer_success_cannot_repopulate (%s)", async url => {
  const oldRead = readWorkspaceData(url);
  invalidateWorkspaceData(url);
  const currentRead = readWorkspaceData(url);
  reads[0].resolve(Response.json(payload(url, "obsolete")));
  await oldRead;
  expect(stored(url)).toBeNull();
  expect(events).toEqual([]);
  expect(readWorkspaceData(url)).toBe(currentRead);
  reads[1].resolve(Response.json(payload(url, "current")));
  await currentRead;
  expect(events).toEqual([{ state: "ready", url }]);
});

it.each([false, true])("issue36_obsolete_failure_cannot_notify_or_remove_current_request (current completed=%s)", async currentCompleted => {
  const oldRead = readWorkspaceData(apiUrl);
  const oldOutcome = oldRead.catch(error => error);
  invalidateWorkspaceData(apiUrl);
  const currentRead = readWorkspaceData(apiUrl);
  if (currentCompleted) { reads[1].resolve(Response.json({ lines: [] })); await currentRead; }
  const currentEvents = [...events];
  reads[0].reject(new Error("Obsolete request failed"));
  expect(await oldOutcome).toMatchObject({ message: "Obsolete request failed" });
  expect(events).toEqual(currentEvents);
  expect(notifications()).toEqual([]);
  expect(debugErrors().at(-1)?.message).toBe("Obsolete request failed");
  if (!currentCompleted) {
    expect(readWorkspaceData(apiUrl)).toBe(currentRead);
    reads[1].resolve(Response.json({ lines: [] })); await currentRead;
  }
  expect(events).toEqual([{ state: "ready", url: apiUrl }]);
});

it.each(["targeted", "global", "reset", "expiry"] as const)("issue36_request_replacement_revokes_all_older_publication (%s)", async replacement => {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const replace = () => {
    if (replacement === "expiry") now += 30_001;
    else if (replacement === "reset") resetWorkspaceCache();
    else invalidateWorkspaceData(replacement === "targeted" ? assetUrl : undefined);
  };
  const first = readWorkspaceData(assetUrl); replace();
  const second = readWorkspaceData(assetUrl); replace();
  const third = readWorkspaceData(assetUrl);
  reads[1].resolve(Response.json({ version: "second" })); await second;
  expect(readWorkspaceData(assetUrl)).toBe(third);
  expect(stored(assetUrl)).toBeNull(); expect(events).toEqual([]);
  reads[2].resolve(Response.json({ version: "third" })); await third;
  reads[0].resolve(Response.json({ version: "first" })); await first;
  expect(stored(assetUrl)).toEqual({ version: "third" });
  expect(events).toEqual([{ state: "ready", url: assetUrl }]);
});

it("issue36_obsolete_success_preserves_diagnostics_from_current_repertoire_data", async () => {
  const url = "http://localhost/api/repertoires";
  const oldRead = readWorkspaceData(url);
  invalidateWorkspaceData(url);
  const currentRead = readWorkspaceData(url);
  reads[1].resolve(Response.json({ repertoires: [] })); await currentRead;
  reportDataDiagnostic("repertoire", { id: "current-invalid" }, "Current diagnostic", "current-invalid");
  const currentDiagnostics = dataDiagnostics();
  reads[0].resolve(Response.json({ repertoires: [] })); await oldRead;
  expect(dataDiagnostics()).toEqual(currentDiagnostics);
});

it.each(["schema", "workspace"] as const)("issue36_obsolete_malformed_response_rejects_quietly (%s)", async validation => {
  const url = validation === "schema" ? assetUrl : apiUrl;
  const schema = validation === "schema" ? z.object({ version: z.string() }) : undefined;
  const oldRead = schema ? readWorkspaceData(url, schema) : readWorkspaceData(url);
  const oldOutcome = oldRead.catch(error => error);
  invalidateWorkspaceData(url);
  const currentRead = schema ? readWorkspaceData(url, schema) : readWorkspaceData(url);
  reads[1].resolve(Response.json(payload(url, "current"))); await currentRead;
  reads[0].resolve(Response.json({ malformed: true }));
  expect(String(await oldOutcome)).toContain("Invalid");
  expect(dataDiagnostics()).toEqual([]);
  expect(notifications()).toEqual([]);
  expect(debugErrors().length).toBeGreaterThan(0);
  expect(events).toEqual([{ state: "ready", url }]);
});

it("issue36_authoritative_requests_retain_sharing_persistence_errors_and_retry", async () => {
  const first = readWorkspaceData(assetUrl);
  expect(readWorkspaceData(assetUrl)).toBe(first);
  expect(reads).toHaveLength(1);
  reads[0].resolve(Response.json({ version: "first" })); await first;
  expect(stored(assetUrl)).toEqual({ version: "first" });
  invalidateWorkspaceData(assetUrl);
  const failure = readWorkspaceData(assetUrl).catch(error => error);
  reads[1].reject(new Error("Authoritative failure"));
  expect(await failure).toMatchObject({ message: "Authoritative failure" });
  expect(notifications().some(record => record.message === "Authoritative failure")).toBe(true);
  const retry = readWorkspaceData(assetUrl);
  reads[2].resolve(Response.json({ version: "retry" })); await retry;
  expect(stored(assetUrl)).toEqual({ version: "retry" });
  expect(events.map(event => event.state)).toEqual(["ready", "error", "ready"]);
});

it("issue36_obsolete_quarantined_records_keep_validated_results_without_shared_diagnostics", async () => {
  const oldRead = readWorkspaceData(apiUrl);
  invalidateWorkspaceData(apiUrl);
  const currentRead = readWorkspaceData(apiUrl);
  reads[1].resolve(Response.json({ lines: [] })); await currentRead;
  reads[0].resolve(Response.json({ lines: [{ id: "obsolete-invalid" }] }));
  await expect(oldRead).resolves.toEqual({ lines: [] });
  expect(dataDiagnostics()).toEqual([]);
  expect(notifications()).toEqual([]);
  expect(debugErrors().at(-1)?.kind).toBe("data-validation");
  expect(events).toEqual([{ state: "ready", url: apiUrl }]);
});

it("issue36_ready_observer_can_start_a_fresh_api_read_without_losing_its_entry", async () => {
  let nextRead: Promise<unknown> | undefined;
  const startNextRead = (event: Event) => {
    if ((event as CustomEvent).detail.url === apiUrl && !nextRead)
      nextRead = readWorkspaceData(apiUrl);
  };
  window.addEventListener("tempo-workspace-data", startNextRead);
  try {
    const first = readWorkspaceData(apiUrl);
    reads[0].resolve(Response.json({ lines: [] })); await first;
    expect(reads).toHaveLength(2);
    expect(readWorkspaceData(apiUrl)).toBe(nextRead);
    reads[1].resolve(Response.json({ lines: [] })); await nextRead;
  } finally { window.removeEventListener("tempo-workspace-data", startNextRead); }
});

it("issue36_invalidated_asset_refresh_preserves_original_fallback_without_restoring_storage", async () => {
  localStorage.setItem(cacheKey(assetUrl), JSON.stringify({ version: 3, savedAt: Date.now(), data: { version: "persisted" } }));
  expect(await readWorkspaceData(assetUrl)).toEqual({ version: "persisted" });
  invalidateWorkspaceData(assetUrl);
  const currentRead = readWorkspaceData(assetUrl);
  reads[0].resolve(Response.json({ version: "obsolete" }));
  // The in-flight refresh promise is observed by the cache even though its caller received fallback.
  let finishRefresh!: () => void;
  const refreshFinished = new Promise<void>(resolve => { finishRefresh = resolve; });
  const recordDuration = performance.recordTempoDuration;
  vi.spyOn(performance, "recordTempoDuration").mockImplementation((...args) => {
    recordDuration(...args);
    if (args[0] === "workspace-data-ready") finishRefresh();
  });
  await refreshFinished;
  expect(stored(assetUrl)).toBeNull();
  reads[1].resolve(Response.json({ version: "current" })); await currentRead;
  expect(stored(assetUrl)).toEqual({ version: "current" });
  expect(events.map(event => event.state)).toEqual(["refreshing", "ready"]);
});

it("issue36_refreshing_observer_invalidation_keeps_the_new_request_authoritative", async () => {
  localStorage.setItem(cacheKey(assetUrl), JSON.stringify({ version: 3, savedAt: Date.now(), data: { version: "persisted" } }));
  let currentRead: Promise<unknown> | undefined;
  const replaceOnRefresh = () => {
    invalidateWorkspaceData(assetUrl);
    currentRead = readWorkspaceData(assetUrl);
  };
  window.addEventListener("tempo-workspace-data", replaceOnRefresh, { once: true });
  try {
    expect(await readWorkspaceData(assetUrl)).toEqual({ version: "persisted" });
    expect(readWorkspaceData(assetUrl)).toBe(currentRead);
    let finishRefresh!: () => void;
    const refreshFinished = new Promise<void>(resolve => { finishRefresh = resolve; });
    const recordDuration = performance.recordTempoDuration;
    vi.spyOn(performance, "recordTempoDuration").mockImplementation((...args) => {
      recordDuration(...args);
      if (args[0] === "workspace-data-ready") finishRefresh();
    });
    reads[0].resolve(Response.json({ version: "obsolete" })); await refreshFinished;
    expect(stored(assetUrl)).toBeNull();
    reads[1].resolve(Response.json({ version: "current" })); await currentRead;
    expect(stored(assetUrl)).toEqual({ version: "current" });
    expect(events.map(event => event.state)).toEqual(["refreshing", "ready"]);
  } finally { window.removeEventListener("tempo-workspace-data", replaceOnRefresh); }
});

it.each(["success", "failure"])("issue36_delayed_workspace_worker_diagnostics_cannot_publish_after_invalidation (%s)", async outcome => {
  vi.resetModules();
  const workspace = await import("../../app/lib/workspace-data");
  const validation = await import("../../app/lib/validated-data");
  const workerNotifications = await import("../../app/lib/notifications");
  workerNotifications.clearNotificationHistory();
  const messages: Array<{ id: number }> = [];
  const submitted = [0, 1].map(() => {
    let resolve!: () => void;
    const promise = new Promise<void>(finish => { resolve = finish; });
    return { promise, resolve };
  });
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror = null;
    onmessageerror = null;
    constructor() { workers.push(this); }
    postMessage(message: { id: number }) { messages.push(message); submitted[messages.length - 1].resolve(); }
    terminate() {}
  }
  vi.stubGlobal("Worker", ControlledWorker);
  const first = workspace.readWorkspaceData(apiUrl);
  const firstOutcome = first.catch(error => error);
  reads[0].resolve(Response.json({ lines: [] }));
  await submitted[0].promise;
  workspace.invalidateWorkspaceData(apiUrl);
  const second = workspace.readWorkspaceData(apiUrl);
  reads[1].resolve(Response.json({ lines: [] }));
  await submitted[1].promise;
  const currentDiagnostic = { source: "repertoire line", raw: { id: "current" }, recordId: "current",
    message: "Current invalid record", recordedAt: new Date().toISOString() };
  workers[0].onmessage!({ data: { id: messages[1].id, result: { lines: [] }, diagnostics: [currentDiagnostic] } } as MessageEvent);
  await second;
  expect(validation.dataDiagnostics()).toHaveLength(1);
  const currentDiagnostics = validation.dataDiagnostics();
  workers[0].onmessage!({ data: { id: messages[0].id,
    ...(outcome === "success" ? { result: { lines: [] } } : { error: "Obsolete worker validation failed" }), diagnostics: [
    { source: "repertoire line", raw: { id: "obsolete" }, recordId: "obsolete", message: "Obsolete invalid record", recordedAt: new Date().toISOString() },
  ] } } as MessageEvent);
  if (outcome === "success") await expect(firstOutcome).resolves.toEqual({ lines: [] });
  else expect(await firstOutcome).toMatchObject({ message: "Obsolete worker validation failed" });
  expect(workerNotifications.notifications()).toEqual([]);
  expect(validation.dataDiagnostics()).toEqual(currentDiagnostics);
  expect(events).toEqual([{ state: "ready", url: apiUrl }]);
});

it.each([assetUrl, apiUrl])("issue36_quiet_obsolete_validation_cannot_suppress_current_identical_alert (%s)", async url => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-11T12:00:00Z"));
  const schema = z.strictObject({ version: z.string() });
  const obsoleteRead = readWorkspaceData(url, schema);
  const obsoleteOutcome = obsoleteRead.catch(error => error);
  invalidateWorkspaceData(url);
  const currentRead = readWorkspaceData(url, schema);
  const currentOutcome = currentRead.catch(error => error);
  reads[0].resolve(Response.json({ malformed: true }));
  const obsoleteError = await obsoleteOutcome;
  expect(obsoleteError).toBeInstanceOf(Error);
  expect(obsoleteError.message).toContain("Invalid");
  expect(dataDiagnostics()).toEqual([]);
  expect(notifications()).toEqual([]);
  expect(events).toEqual([]);
  expect(readWorkspaceData(url, schema)).toBe(currentRead);
  const quietDebugRecord = debugErrors().at(-1)!;
  reads[1].resolve(Response.json({ malformed: true }));
  const currentError = await currentOutcome;
  expect(currentError).toBeInstanceOf(Error);
  expect(currentError.message).toBe(obsoleteError.message);
  expect(dataDiagnostics()).toHaveLength(1);
  expect(events).toEqual([{ state: "error", url }]);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0]).toMatchObject({ message: currentError.message,
    details: { debugRecordId: quietDebugRecord.id, kind: "data-validation" } });
});

it("issue36_quiet_obsolete_quarantine_cannot_suppress_current_identical_alert", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-11T12:00:00Z"));
  const obsoleteRead = readWorkspaceData(apiUrl);
  invalidateWorkspaceData(apiUrl);
  const currentRead = readWorkspaceData(apiUrl);
  const invalidRecord = { lines: [{ id: "invalid-record" }] };
  reads[0].resolve(Response.json(invalidRecord));
  await expect(obsoleteRead).resolves.toEqual({ lines: [] });
  expect(dataDiagnostics()).toEqual([]);
  expect(notifications()).toEqual([]);
  expect(events).toEqual([]);
  expect(readWorkspaceData(apiUrl)).toBe(currentRead);
  reads[1].resolve(Response.json(invalidRecord));
  await expect(currentRead).resolves.toEqual({ lines: [] });
  expect(dataDiagnostics()).toHaveLength(1);
  expect(events).toEqual([{ state: "ready", url: apiUrl }]);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0]).toMatchObject({ details: { kind: "data-validation" } });
});
