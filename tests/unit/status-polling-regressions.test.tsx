import { act, fireEvent, render, screen, cleanup } from "@testing-library/react";
import { Profiler } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ServiceStatusPanel } from "../../app/components/service-status-panel";
import { useGameSync, type GameSyncState } from "../../app/hooks/use-game-sync";
import { notifications, clearNotificationHistory, publishNotification } from "../../app/lib/notifications";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { boardDiagnostics } from "../../app/lib/board-diagnostics";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { STANDARD_FEN } from "../../app/const";
import { serviceStatusSnapshot } from "../../app/lib/service-status";

const activeActivity = {
  items: [{ source: "durable", id: "poll-task", title: "Polling task", state: "running", phase: "Working",
    completed: 1, total: 10, updated_at: "2026-09-22T00:00:00Z", error: null, paused: false, promoted: false }],
  counts: { running: 1, queued: 0, paused: 0, failed: 0 }, total: 1, next_offset: null as number | null,
  writer: { healthy: true, foreground: 0, background: 0 },
};
const activeJob = { id: "65b588f2-cba2-4678-bc8c-b7a10b5227f1", status: "running", created_at: "2026-09-22T00:00:00Z",
  started_at: null, completed_at: null, updated_at: "2026-09-22T00:00:00Z", error: null, result: null };
let visibility: DocumentVisibilityState;
let online: boolean;
beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible"; online = true;
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  vi.spyOn(navigator, "onLine", "get").mockImplementation(() => online);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); clearNotificationHistory(); clearDebugErrors(); });
async function settle() { await act(async () => undefined); }
async function advance(milliseconds: number) { await act(async () => vi.advanceTimersByTimeAsync(milliseconds)); }
async function wake() {
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("online"));
  });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}
function mockSyncFetch(status: () => Response | Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith("/sync/status")
    ? Promise.resolve(status()) : new Promise<Response>(() => undefined));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

it("activity_polling_matches_open_visible_online_policy", async () => {
  const fetchMock = vi.fn(async () => Response.json(activeActivity));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  await advance(29_999); expect(fetchMock).toHaveBeenCalledTimes(1);
  await advance(1); expect(fetchMock).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  expect(fetchMock).toHaveBeenCalledTimes(3);
  await advance(2_000); expect(fetchMock).toHaveBeenCalledTimes(4);
  visibility = "hidden"; await wake(); await advance(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(4);
  visibility = "visible"; online = false; await wake(); await advance(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(4);
  online = true; await wake(); expect(fetchMock).toHaveBeenCalledTimes(5);
});

it("sync_status_equivalent_responses_preserve_consumer_identity", async () => {
  mockSyncFetch(() => Response.json({ providers: [], active_job: activeJob }));
  const renders: GameSyncState[] = [];
  function Probe() { const { state } = useGameSync(); renders.push(state); return null; }
  render(<Probe />); await settle();
  const baseline = renders.length;
  await advance(58_000);
  expect(renders.length).toBe(baseline);
});

it("sync_status_hidden_offline_and_failure_backoff_match_policy", async () => {
  const statusMock = vi.fn(() => Response.json({ providers: [], active_job: activeJob }));
  mockSyncFetch(statusMock);
  function Probe() { useGameSync(); return null; }
  visibility = "hidden";
  render(<Probe />); await settle(); await advance(60_000);
  expect(statusMock).not.toHaveBeenCalled();
  visibility = "visible"; await wake(); expect(statusMock).toHaveBeenCalledTimes(1);
  await advance(2_000); expect(statusMock).toHaveBeenCalledTimes(2);
  online = false; await act(async () => window.dispatchEvent(new Event("offline")));
  await advance(60_000); expect(statusMock).toHaveBeenCalledTimes(2);
  online = true; await wake(); expect(statusMock).toHaveBeenCalledTimes(3);
});

it("sync_status_recovery_bursts_are_single_flight", async () => {
  const first = deferred<Response>();
  const second = deferred<Response>();
  const statusMock = vi.fn().mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise)
    .mockImplementation(() => Response.json({ providers: [] }));
  mockSyncFetch(statusMock);
  function Probe() { useGameSync(); return null; }
  const view = render(<Probe />); await settle();
  await wake(); expect(statusMock).toHaveBeenCalledTimes(1);
  await act(async () => first.resolve(Response.json({ providers: [] })));
  expect(statusMock).toHaveBeenCalledTimes(2);
  await act(async () => second.resolve(Response.json({ providers: [] })));
  expect(statusMock).toHaveBeenCalledTimes(2);
  view.unmount(); await advance(60_000); expect(statusMock).toHaveBeenCalledTimes(2);
});

it("activity_refresh_events_coalesce_without_parallel_requests", async () => {
  const first = deferred<Response>(); const second = deferred<Response>();
  const fetchMock = vi.fn().mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise)
    .mockImplementation(async () => Response.json(activeActivity));
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" }));
  await wake(); expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => first.resolve(Response.json(activeActivity)));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await act(async () => second.resolve(Response.json(activeActivity)));
  expect(fetchMock).toHaveBeenCalledTimes(2);
  view.unmount(); await wake(); await advance(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("activity_coalesced_success_clears_failure_before_react_commits", async () => {
  const failedRequest = deferred<Response>();
  const followupParsed = deferred<void>();
  const immediateSuccess = Response.json(activeActivity);
  vi.spyOn(immediateSuccess, "json").mockImplementation(async () => {
    followupParsed.resolve();
    return activeActivity;
  });
  const fetchMock = vi.fn().mockImplementationOnce(() => failedRequest.promise)
    .mockImplementation(async () => immediateSuccess);
  vi.stubGlobal("fetch", fetchMock);
  // Retained incidents must recover even when this session's transient failure is batched away.
  const incidentId = publishNotification({ key: "analysis-activity-error", source: "analysis activity",
    severity: "error", message: "Activity was unavailable in the previous session." });
  render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" }));
  await wake();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  // No React commit or artificial wait separates the failure and immediate follow-up success.
  await act(async () => {
    failedRequest.resolve(new Response("unavailable", { status: 503 }));
    await followupParsed.promise;
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByLabelText("needs attention")).toBeNull();
  expect(notifications().filter(record => record.key === "analysis-activity-error")).toHaveLength(1);
  expect(notifications().find(record => record.id === incidentId)?.resolvedAt).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" })); await settle();
  await advance(29_999); expect(fetchMock).toHaveBeenCalledTimes(2);
  await advance(1); expect(fetchMock).toHaveBeenCalledTimes(3);
});

it("activity_obsolete_offsets_and_unmounted_sessions_cannot_publish", async () => {
  const oldPage = deferred<Response>();
  const fetchMock = vi.fn().mockImplementationOnce(async () => Response.json({ ...activeActivity, next_offset: 50 }))
    .mockImplementationOnce(() => oldPage.promise)
    .mockImplementation(async () => Response.json({ ...activeActivity, next_offset: null,
      items: [{ ...activeActivity.items[0], id: "page-50", title: "Current page" }] }));
  vi.stubGlobal("fetch", fetchMock);
  const view = render(<ServiceStatusPanel />); await settle();
  // Opening starts a page-zero refresh; pagination supersedes that in-flight result.
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  // Initial closed data has not populated the detail pagination yet.
  await act(async () => oldPage.resolve(Response.json({ ...activeActivity, next_offset: 50 })));
  fireEvent.click(screen.getByRole("button", { name: "Show more" })); await settle();
  expect(screen.getByText("Current page")).toBeTruthy();
  expect(fetchMock.mock.calls.at(-1)?.[0]).toContain("offset=50");
  view.unmount();
  const snapshot = serviceStatusSnapshot();
  const late = deferred<Response>();
  vi.stubGlobal("fetch", vi.fn(() => late.promise));
  const remounted = render(<ServiceStatusPanel />); await settle(); remounted.unmount();
  await act(async () => late.resolve(Response.json({ ...activeActivity, counts: { ...activeActivity.counts, running: 99 } })));
  expect(serviceStatusSnapshot()).toBe(snapshot);
});

it.each(["success", "error"])("activity_offset_changes_ignore_late_%s_and_preserve_one_flight", async outcome => {
  const late = deferred<Response>();
  const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => String(input).includes("offset=50")
    ? Response.json({ ...activeActivity, next_offset: null, items: [{ ...activeActivity.items[0], title: "Current page" }] })
    : Response.json({ ...activeActivity, next_offset: 50 }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  fetchMock.mockImplementationOnce(() => late.promise);
  await act(async () => window.dispatchEvent(new Event("focus")));
  fireEvent.click(screen.getByRole("button", { name: "Show more" })); await settle();
  expect(fetchMock).toHaveBeenCalledTimes(3);
  await act(async () => late.resolve(outcome === "error" ? new Response("obsolete", { status: 503 }) : Response.json({
    ...activeActivity, counts: { ...activeActivity.counts, running: 99 }, items: [{ ...activeActivity.items[0], title: "Obsolete page" }],
  })));
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(screen.getByText("Current page")).toBeTruthy();
  expect(screen.queryByText("Obsolete page")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(notifications().some(record => record.key === "analysis-activity-error")).toBe(false);
});

it("activity_equivalent_responses_preserve_render_identity", async () => {
  let queueDepth = 0;
  const fetchMock = vi.fn(async () => Response.json({ ...activeActivity, writer: { ...activeActivity.writer, background: ++queueDepth } }));
  vi.stubGlobal("fetch", fetchMock);
  let commits = 0;
  render(<Profiler id="activity" onRender={() => commits++}><ServiceStatusPanel /></Profiler>); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  const baseline = commits;
  for (let poll = 0; poll < 29; poll++) await advance(2_000);
  expect(commits).toBe(baseline);
  expect(serviceStatusSnapshot()?.writer?.background).toBe(queueDepth);
});

it("activity_writer_failure_and_recovery_remain_actionable", async () => {
  let healthy = false;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...activeActivity, writer: { ...activeActivity.writer, healthy } })));
  render(<ServiceStatusPanel />); await settle();
  expect(screen.getByLabelText("needs attention")).toBeTruthy();
  await advance(30_000);
  expect(notifications().filter(record => record.key === "database-writer-health")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  expect(screen.getByRole("alert").textContent).toContain("Restart Tempo");
  healthy = true; await advance(2_000);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(notifications().find(record => record.key === "database-writer-health")?.resolvedAt).toBeTruthy();
});

it.each([true, false])("activity_failure_backoff_and_recovery_match_policy_open_%s", async open => {
  let failing = true;
  const fetchMock = vi.fn(async () => failing ? new Response("unavailable", { status: 503 }) : Response.json(activeActivity));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  if (open) { fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle(); }
  const baseline = fetchMock.mock.calls.length;
  for (const [index, delay] of (open ? [5_000, 10_000, 20_000, 60_000] : [30_000, 30_000, 30_000, 60_000]).entries()) {
    await advance(delay - 1); expect(fetchMock).toHaveBeenCalledTimes(baseline + index);
    await advance(1); expect(fetchMock).toHaveBeenCalledTimes(baseline + index + 1);
  }
  expect(notifications().filter(record => record.key === "analysis-activity-error")).toHaveLength(1);
  failing = false; await wake();
  expect(notifications().find(record => record.key === "analysis-activity-error")?.resolvedAt).toBeTruthy();
});

it("activity_idle_polling_and_real_progress_match_policy", async () => {
  let completed = 1; let running = 0;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...activeActivity,
    counts: { ...activeActivity.counts, running }, items: [{ ...activeActivity.items[0], completed }] })));
  render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  const progress = () => screen.getByRole("progressbar", { name: "Polling task progress" }) as HTMLProgressElement;
  expect(progress().value).toBe(1);
  completed = 2; running = 1; await advance(14_999); expect(progress().value).toBe(1);
  await advance(1); expect(progress().value).toBe(2);
  completed = 3; await advance(2_000); expect(progress().value).toBe(3);
});

it("sync_status_progress_errors_and_recovery_publish_changes", async () => {
  let job = { ...activeJob, error: null as string | null, result: null as null | { imported: number; synced_at: string; providers: object } };
  let lastState!: GameSyncState; let commits = 0;
  mockSyncFetch(() => Response.json({ providers: [], active_job: job, active_filters: { days: 90, rated_only: true, speeds: ["blitz"] } }));
  function Probe() { lastState = useGameSync().state; return null; }
  render(<Profiler id="sync" onRender={() => commits++}><Probe /></Profiler>); await settle();
  const first = lastState; const baseline = commits;
  job = { ...job, updated_at: "2026-09-22T00:01:00Z" }; await advance(2_000);
  expect(lastState).toBe(first); expect(commits).toBe(baseline);
  job = { ...job, status: "failed", error: "Provider unavailable" }; await advance(2_000);
  expect(lastState.error).toBe("Provider unavailable"); expect(lastState.syncing).toBe(false);
  job = { ...job, status: "complete", error: null, result: { imported: 3, synced_at: "2026-09-22T00:02:00Z", providers: {} } };
  await advance(15_000);
  expect(lastState.error).toBe(""); expect(lastState.imported).toBe(3); expect(lastState.lastSuccess).toBe(job.result?.synced_at);
});

it("sync_status_failure_backoff_recovers_without_duplicate_incidents", async () => {
  let failing = true;
  const statusMock = vi.fn(() => failing ? Response.json({ detail: "unavailable" }, { status: 503 }) : Response.json({ providers: [] }));
  mockSyncFetch(statusMock);
  function Probe() { useGameSync(); return null; }
  render(<Probe />); await settle();
  for (const [index, delay] of [5_000, 10_000, 20_000, 60_000].entries()) {
    await advance(delay - 1); expect(statusMock).toHaveBeenCalledTimes(index + 1);
    await advance(1); expect(statusMock).toHaveBeenCalledTimes(index + 2);
  }
  expect(debugErrors().filter(record => record.context.source === "game-sync-status")).toHaveLength(1);
  failing = false; await wake(); const baseline = statusMock.mock.calls.length;
  await advance(14_999); expect(statusMock).toHaveBeenCalledTimes(baseline);
  await advance(1); expect(statusMock).toHaveBeenCalledTimes(baseline + 1);
});

it("sync_status_unmounted_responses_cannot_resolve_new_session_incidents", async () => {
  const late = deferred<Response>();
  const statusMock = vi.fn().mockImplementationOnce(() => late.promise).mockImplementation(() => Response.json({ detail: "new session failed" }, { status: 503 }));
  mockSyncFetch(statusMock);
  function Probe() { useGameSync(); return null; }
  const old = render(<Probe />); await settle(); old.unmount();
  render(<Probe />); await settle();
  const before = debugErrors().filter(record => record.context.source === "game-sync-status");
  expect(before).toHaveLength(1);
  await act(async () => late.resolve(Response.json({ providers: [] })));
  expect(debugErrors().filter(record => record.context.source === "game-sync-status")).toHaveLength(1);
  const incident = notifications().find(record => record.source === "game-sync-status");
  expect(incident).toBeDefined();
  expect(incident?.resolvedAt).toBeNull();
});

const syncSettings = { initial_depth: 8, timezone: "America/New_York", new_cards_per_day: 5,
  lichess_username: "player", chesscom_username: "", auto_sync_minutes: 2, engine_line_window_cp: 50,
  major_mistake_cp: 100, light_first_interval_days: 3, draw_hold_user_moves: 20 };
it("passive_status_throttling_preserves_game_acquisition_and_pending_commands", async () => {
  visibility = "hidden";
  const posts: Array<{ body: string; key: string }> = [];
  let receiptComplete = false;
  const syncResult = { imported: 0, job_id: activeJob.id, status: "queued", providers: {} };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/operations/")) return Response.json(receiptComplete ? { state: "complete", response: syncResult } : { state: "pending" });
    if (path.endsWith("/api/games/sync")) {
      posts.push({ body: String(init?.body), key: (init?.headers as Record<string, string>)["Idempotency-Key"] });
      return Response.json({ operation_id: posts.at(-1)?.key }, { status: 202 });
    }
    if (path.endsWith("/api/settings")) return Response.json(syncSettings);
    return Response.json({ providers: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  let sync!: ReturnType<typeof useGameSync>["sync"];
  function Probe() { sync = useGameSync().sync; return null; }
  render(<Probe />); await settle();
  expect(fetchMock).not.toHaveBeenCalled();
  await act(async () => sync(true));
  expect(posts).toHaveLength(1);
  expect(localStorage.getItem("tempo-pending-game-sync-command-v1")).not.toBeNull();
  expect(fetchMock.mock.calls.filter(call => String(call[0]).endsWith("/sync/status"))).toHaveLength(0);
  receiptComplete = true;
  await act(async () => sync(true));
  expect(posts).toHaveLength(1); // The saved receipt confirms the original command without another POST.
  expect(localStorage.getItem("tempo-pending-game-sync-command-v1")).toBeNull();
  visibility = "visible"; await wake(); await advance(119_999);
  expect(posts).toHaveLength(1);
  await advance(60_001); // Initial automatic timer remains 180 seconds; settings apply to its next schedule.
  expect(posts).toHaveLength(2);
  await advance(120_000); expect(posts).toHaveLength(3);
});

it("activity_controls_remain_prompt_while_passive_reads_are_suspended", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ ok: true }) : Response.json(activeActivity));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  visibility = "hidden"; await wake();
  fireEvent.click(screen.getByRole("button", { name: "Pause" })); await settle();
  expect(fetchMock.mock.calls.filter(call => call[1]?.method === "POST")).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(call => call[1]?.method !== "POST")).toHaveLength(2);
});

it.each(["queued", "running", "paused", "retrying", "complete", "failed"])('sync_status_job_%s_retains_its_existing_interval', async status => {
  const statusMock = vi.fn(() => Response.json({ providers: [], active_job: { ...activeJob, status } }));
  mockSyncFetch(statusMock);
  function Probe() { useGameSync(); return null; }
  render(<Probe />); await settle();
  const interval = ["complete", "failed"].includes(status) ? 15_000 : 2_000;
  await advance(interval - 1); expect(statusMock).toHaveBeenCalledTimes(1);
  await advance(1); expect(statusMock).toHaveBeenCalledTimes(2);
});

it("sync_status_equal_provider_counts_retain_identity_and_changed_counts_publish", async () => {
  let inserted = 1; let username = "player";
  const result = () => ({ imported: 1, synced_at: "2026-09-22T00:00:00Z", providers: {
    lichess: { provider: "lichess", username, status: "idle", fetched: 1, inserted, updated: 0,
      duplicates: 0, filtered: 0, rejected: 0, failed: 0, error: null, retry_after: null },
  } });
  mockSyncFetch(() => Response.json({ providers: [], active_job: { ...activeJob, status: "complete", result: result() } }));
  let state!: GameSyncState;
  function Probe() { state = useGameSync().state; return null; }
  render(<Probe />); await settle(); const initial = state;
  username = "changed metadata"; await advance(15_000); expect(state).toBe(initial);
  inserted = 2; await advance(15_000); expect(state).not.toBe(initial);
  expect(state.providers?.[0].inserted).toBe(2);
});

it("sync_status_superseded_by_a_manual_command_cannot_publish_an_old_completion", async () => {
  const lateStatus = deferred<Response>(); const nextStatus = deferred<Response>();
  let statusCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/sync/status")) return ++statusCalls === 1 ? lateStatus.promise : nextStatus.promise;
    if (String(input).endsWith("/api/settings")) return Response.json({ ...syncSettings, lichess_username: statusCalls > 1 ? "player" : "" });
    return Response.json({ imported: 0, job_id: activeJob.id, status: "queued", providers: {} });
  }));
  let value!: ReturnType<typeof useGameSync>;
  function Probe() { value = useGameSync(); return null; }
  render(<Probe />); await settle();
  // Use configured settings for the explicit command; startup acquisition has already returned.
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/sync/status")) { statusCalls++; return nextStatus.promise; }
    if (String(input).endsWith("/api/settings")) return Response.json(syncSettings);
    return Response.json({ imported: 0, job_id: activeJob.id, status: "queued", providers: {} });
  }));
  await act(async () => value.sync(true));
  expect(value.state.jobStatus).toBe("queued");
  await act(async () => lateStatus.resolve(Response.json({ providers: [], active_job: { ...activeJob, status: "complete" } })));
  expect(value.state.jobStatus).toBe("queued");
  await act(async () => nextStatus.resolve(Response.json({ providers: [], active_job: { ...activeJob, status: "queued" } })));
});

it.each([
  { failure: "no_username", expectedError: "Add a Lichess or Chess.com username in Settings.", expectedPosts: 0 },
  { failure: "settings_http", expectedError: "Settings unavailable", expectedPosts: 0 },
  { failure: "settings_validation", expectedError: "Invalid game sync settings data", expectedPosts: 0 },
  { failure: "enqueue_http", expectedError: "Sync enqueue unavailable", expectedPosts: 1 },
  { failure: "enqueue_validation", expectedError: "Invalid game sync data", expectedPosts: 1 },
  { failure: "blocked_receipt", expectedError: "is blocked: Writer unavailable", expectedPosts: 1 },
])("manual_sync_precommand_error_is_not_erased_by_historical_completed_status_$failure", async ({ failure, expectedError, expectedPosts }) => {
  const historicalStatus = { providers: [], active_job: { ...activeJob, status: "complete",
    result: { imported: 3, synced_at: "2026-09-22T00:00:00Z", providers: {} } } };
  let manualStarted = false; let statusCalls = 0; let syncPosts = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/sync/status")) { statusCalls++; return Response.json(historicalStatus); }
    if (path.endsWith("/api/settings")) {
      if (manualStarted && failure === "settings_http") return Response.json({ detail: "Settings unavailable" }, { status: 503 });
      if (manualStarted && failure === "settings_validation") return Response.json({ initial_depth: "invalid" });
      return Response.json({ ...syncSettings, lichess_username: manualStarted && failure !== "no_username" ? "player" : "" });
    }
    if (path.includes("/operations/")) return Response.json({ state: "blocked", last_error: { message: "Writer unavailable" } });
    expect(path).toMatch(/\/api\/games\/sync$/);
    expect(init?.method).toBe("POST");
    syncPosts++;
    if (failure === "enqueue_http") return Response.json({ detail: "Sync enqueue unavailable" }, { status: 503 });
    if (failure === "enqueue_validation") return Response.json({ imported: "invalid" });
    return Response.json({ operation_id: "failed-manual-receipt" }, { status: 202 });
  }));
  let value!: ReturnType<typeof useGameSync>;
  function Probe() { value = useGameSync(); return null; }
  const view = render(<Probe />); await settle();
  expect(value.state.jobStatus).toBe("complete");
  expect(value.state.imported).toBe(3);
  manualStarted = true;
  await act(async () => value.sync(true)); await settle();
  expect(value.state.error).toContain(expectedError);
  expect(syncPosts).toBe(expectedPosts);
  expect(statusCalls).toBe(1); // No new job was established to reconcile against the historical result.
  await advance(14_999); expect(statusCalls).toBe(1);
  expect(value.state.error).toContain(expectedError);
  await advance(1); expect(statusCalls).toBe(2); // Ordinary idle polling resumes with one completion-relative timer.
  expect(value.state.error).toContain(expectedError);
  await advance(15_000); expect(statusCalls).toBe(3);
  expect(value.state.error).toContain(expectedError);
  view.unmount(); await advance(30_000); expect(statusCalls).toBe(3);
});

it("successful_manual_sync_supersedes_prior_manual_error", async () => {
  const commandResponse = deferred<Response>();
  let username = ""; let commandEstablished = false; let statusCalls = 0; let syncPosts = 0;
  let passiveJob = { ...activeJob, status: "complete", error: null as string | null,
    result: { imported: 3, synced_at: "2026-09-22T00:00:00Z", providers: {} } as object | null };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/sync/status")) {
      statusCalls++;
      return Response.json({ providers: [], active_job: commandEstablished ? activeJob : passiveJob });
    }
    if (path.endsWith("/api/settings")) return Response.json({ ...syncSettings, lichess_username: username });
    syncPosts++;
    return commandResponse.promise;
  }));
  let value!: ReturnType<typeof useGameSync>;
  function Probe() { value = useGameSync(); return null; }
  render(<Probe />); await settle();
  await act(async () => value.sync(true));
  const manualError = "Add a Lichess or Chess.com username in Settings.";
  expect(value.state.error).toBe(manualError);
  await advance(15_000); expect(statusCalls).toBe(2);
  expect(value.state.error).toBe(manualError);
  // Even an unrelated active job's server error cannot replace the actionable local failure.
  passiveJob = { ...passiveJob, status: "running", error: "Earlier provider outage", result: null };
  await advance(15_000); expect(statusCalls).toBe(3);
  expect(value.state.error).toBe(manualError);
  username = "player";
  let command!: Promise<void>;
  await act(async () => { command = value.sync(true); });
  expect(syncPosts).toBe(1);
  expect(value.state.error).toBe(manualError); // Merely starting another attempt is not success.
  commandEstablished = true;
  await act(async () => {
    commandResponse.resolve(Response.json({ imported: 0, job_id: activeJob.id, status: "queued", providers: {} }));
    await command;
  });
  expect(statusCalls).toBe(4);
  expect(value.state.error).toBe("");
  expect(value.state.jobStatus).toBe("running");
  await advance(2_000); expect(statusCalls).toBe(5);
  expect(value.state.error).toBe("");
});

it.each(["pending", "complete"])("manual_sync_%s_receipt_keeps_immediate_status_reconciliation", async receiptState => {
  let manualStarted = false; let statusCalls = 0; let syncPosts = 0; let receiptCalls = 0;
  const pendingKey = "tempo-pending-game-sync-command-v1";
  const savedCommand = { operationId: "existing-sync-receipt", body: JSON.stringify({ lichess_username: "player", chesscom_username: "",
    days: 90, speeds: ["blitz", "rapid", "classical"], rated_only: true, repair: false }) };
  const syncResult = { imported: 0, job_id: activeJob.id, status: "queued", providers: {} };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/sync/status")) {
      statusCalls++;
      return Response.json({ providers: [], active_job: manualStarted ? activeJob : { ...activeJob, status: "complete" } });
    }
    if (path.endsWith("/api/settings")) return Response.json({ ...syncSettings, lichess_username: manualStarted ? "player" : "" });
    if (path.includes("/operations/")) {
      receiptCalls++;
      return Response.json(receiptState === "complete" ? { state: "complete", response: syncResult } : { state: "pending" });
    }
    syncPosts++;
    return Response.json({ operation_id: savedCommand.operationId }, { status: 202 });
  }));
  let value!: ReturnType<typeof useGameSync>;
  function Probe() { value = useGameSync(); return null; }
  render(<Probe />); await settle();
  localStorage.setItem(pendingKey, JSON.stringify(savedCommand));
  manualStarted = true;
  await act(async () => value.sync(true)); await settle();
  expect(value.state.jobStatus).toBe("running");
  expect(value.state.error).toBe("");
  expect(statusCalls).toBe(2);
  expect(syncPosts).toBe(receiptState === "complete" ? 0 : 1);
  expect(receiptCalls).toBe(receiptState === "complete" ? 1 : 2);
  expect(localStorage.getItem(pendingKey)).toBe(receiptState === "complete" ? null : JSON.stringify(savedCommand));
  await advance(1_999); expect(statusCalls).toBe(2);
  await advance(1); expect(statusCalls).toBe(3);
});

it("manual_sync_startup_suspends_passive_reads_until_command_state_and_then_reconciles", async () => {
  const commandSettings = deferred<Response>();
  const preCommandStatus = deferred<Response>();
  const postCommandStatus = deferred<Response>();
  let manualStarted = false; let commandEstablished = false; let statusCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/sync/status")) {
      statusCalls++;
      if (!manualStarted) return Response.json({ providers: [], active_job: { ...activeJob, status: "complete" } });
      return commandEstablished ? postCommandStatus.promise : preCommandStatus.promise;
    }
    if (path.endsWith("/api/settings")) return manualStarted ? commandSettings.promise
      : Response.json({ ...syncSettings, lichess_username: "" });
    commandEstablished = true;
    return Response.json({ imported: 0, job_id: activeJob.id, status: "queued", providers: {} });
  }));
  let value!: ReturnType<typeof useGameSync>;
  function Probe() { value = useGameSync(); return null; }
  render(<Probe />); await settle();
  expect(value.state.jobStatus).toBe("complete"); // The bootstrap read has finished; no request is in flight.
  manualStarted = true;
  let command!: Promise<void>;
  await act(async () => { command = value.sync(true); });
  await wake(); await advance(15_000);
  await act(async () => { commandSettings.resolve(Response.json(syncSettings)); await command; });
  expect(value.state.jobStatus).toBe("queued");
  // The old implementation launches this read during settings loading, then accepts it after the POST.
  await act(async () => preCommandStatus.resolve(Response.json({ providers: [], active_job: { ...activeJob, status: "complete" } })));
  expect(value.state.jobStatus).toBe("queued");
  expect(value.state.syncing).toBe(true);
  expect(statusCalls).toBe(2); // Bootstrap plus one legitimate post-command refresh.
  await act(async () => postCommandStatus.resolve(Response.json({ providers: [], active_job: activeJob })));
  expect(value.state.jobStatus).toBe("running");
  await advance(1_999); expect(statusCalls).toBe(2);
  await advance(1); expect(statusCalls).toBe(3);
});

it("manual_sync_post_command_status_reconciles_before_react_commits", async () => {
  const postCommandParsed = deferred<void>();
  let manualStarted = false;
  const immediateResponse = (body: unknown, parsed?: () => void) => {
    const response = Response.json(body);
    vi.spyOn(response, "json").mockImplementation(async () => { parsed?.(); return body; });
    return response;
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/sync/status")) return immediateResponse({ providers: [], active_job: { ...activeJob, status: "complete" } },
      manualStarted ? () => postCommandParsed.resolve() : undefined);
    if (path.endsWith("/api/settings")) return immediateResponse({ ...syncSettings, lichess_username: manualStarted ? "player" : "" });
    return immediateResponse({ imported: 0, job_id: activeJob.id, status: "queued", providers: {} });
  }));
  let value!: ReturnType<typeof useGameSync>;
  function Probe() { value = useGameSync(); return null; }
  render(<Probe />); await settle();
  expect(value.state.jobStatus).toBe("complete");
  manualStarted = true;
  await act(async () => {
    await value.sync(true);
    await postCommandParsed.promise;
  });
  // The authoritative read equals the bootstrap snapshot, but supersedes the queued command update.
  expect(value.state.jobStatus).toBe("complete");
  expect(value.state.syncing).toBe(false);
});

const trainingCard = { id: asCardId("polling-training"), kind: "opening" as const, title: "Polling training", subtitle: "",
  startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const };
const trainingProps = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1,
  card: trainingCard, boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
  handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), onMove: vi.fn(), useSharedBoard: true };

it("equivalent_status_during_actual_training_avoids_parent_commits_and_board_publications", async () => {
  useTrainingStore.setState({ currentFenString: asFenString(STANDARD_FEN), step: 0, feedback: "ready", queueNotice: "",
    attempt: { entryKey: "polling-training", generation: 1, phase: "playerTurn" } });
  const statusMock = vi.fn(() => Response.json({ providers: [], active_job: activeJob }));
  const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith("/sync/status")
    ? Promise.resolve(statusMock()) : String(input).includes("/system/activity")
      ? Promise.resolve(Response.json(activeActivity)) : new Promise<Response>(() => undefined));
  vi.stubGlobal("fetch", fetchMock);
  let trainingCommits = 0;
  function TrainingConsumer() {
    useGameSync();
    return <><Profiler id="training" onRender={() => trainingCommits++}><TrainingView {...trainingProps} /></Profiler><ServiceStatusPanel /></>;
  }
  render(<TrainingConsumer />); await settle();
  expect(screen.getByText("Polling training")).toBeTruthy();
  const initialCommits = trainingCommits; const initialCounters = boardDiagnostics();
  for (let tick = 0; tick < 29; tick++) await advance(2_000);
  expect(statusMock).toHaveBeenCalledTimes(30);
  expect(fetchMock.mock.calls.filter(call => String(call[0]).includes("/system/activity"))).toHaveLength(2);
  expect(trainingCommits).toBe(initialCommits);
  const finalCounters = boardDiagnostics();
  for (const counter of ["acquisitions", "releases", "publications", "positionResets", "inputCancellations"] as const)
    expect(finalCounters[counter] - initialCounters[counter], counter).toBe(0);
});

it.each([
  { name: "closed active", open: false, hidden: false, offline: false, active: true, activity: 2, sync: 30 },
  { name: "closed idle", open: false, hidden: false, offline: false, active: false, activity: 2, sync: 4 },
  { name: "open active", open: true, hidden: false, offline: false, active: true, activity: 30, sync: 30 },
  { name: "open idle", open: true, hidden: false, offline: false, active: false, activity: 4, sync: 4 },
  { name: "hidden active", open: false, hidden: true, offline: false, active: true, activity: 0, sync: 0 },
  { name: "offline active", open: false, hidden: false, offline: true, active: true, activity: 0, sync: 0 },
])("status_request_counts_match_the_sixty_second_window_$name", async scenario => {
  visibility = scenario.hidden ? "hidden" : "visible"; online = !scenario.offline;
  let activityRequests = 0; let syncRequests = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    if (String(input).includes("/system/activity")) {
      activityRequests++;
      return Promise.resolve(Response.json({ ...activeActivity, counts: { ...activeActivity.counts, running: scenario.active ? 1 : 0 } }));
    }
    if (String(input).endsWith("/sync/status")) {
      syncRequests++;
      return Promise.resolve(Response.json({ providers: [], active_job: scenario.active ? activeJob : null }));
    }
    return new Promise<Response>(() => undefined);
  }));
  function Consumer() { useGameSync(); return <ServiceStatusPanel />; }
  render(<Consumer />); await settle();
  if (scenario.open) {
    fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
    // Count the explicit open refresh as the window's initial sample, excluding the earlier closed bootstrap.
    activityRequests--;
  }
  for (let tick = 0; tick < 59; tick++) await advance(1_000);
  expect(activityRequests).toBe(scenario.activity); expect(syncRequests).toBe(scenario.sync);
});

it("activity_initial_offline_mount_reports_unavailable_not_empty", async () => {
  online = false;
  const fetchMock = vi.fn(async () => Response.json({ ...activeActivity, items: [], total: 0,
    counts: { running: 0, queued: 0, paused: 0, failed: 0 } }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  expect(fetchMock).not.toHaveBeenCalled();
  expect(screen.queryByText("No background activity yet.")).toBeNull();
  expect(screen.getByText("Activity status has not been loaded yet.")).toBeTruthy();
  expect(notifications().some(record => record.key === "analysis-activity-error")).toBe(false);
  await advance(60_000); expect(fetchMock).not.toHaveBeenCalled();
  online = true; await wake();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("Activity status has not been loaded yet.")).toBeNull();
  expect(screen.getByText("No background activity yet.")).toBeTruthy();
  await advance(14_999); expect(fetchMock).toHaveBeenCalledTimes(1);
  await advance(1); expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("activity_offline_after_success_preserves_last_known_empty_status", async () => {
  const fetchMock = vi.fn(async () => Response.json({ ...activeActivity, items: [], total: 0,
    counts: { running: 0, queued: 0, paused: 0, failed: 0 } }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  online = false; await act(async () => window.dispatchEvent(new Event("offline")));
  await advance(60_000); expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByText("No background activity yet.")).toBeTruthy();
  expect(screen.queryByText("Activity status has not been loaded yet.")).toBeNull();
  expect(screen.getByText("0 running · 0 queued · 0 paused · 0 failed")).toBeTruthy();
});

it("activity_offline_after_success_preserves_last_known_items_and_writer_health", async () => {
  const fetchMock = vi.fn(async () => Response.json({ ...activeActivity,
    writer: { ...activeActivity.writer, healthy: false } }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  online = false; await act(async () => window.dispatchEvent(new Event("offline")));
  await advance(60_000); expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Polling task")).toBeTruthy();
  expect(screen.getByText("1 running · 0 queued · 0 paused · 0 failed")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("Restart Tempo");
  expect(screen.queryByText("No background activity yet.")).toBeNull();
  expect(notifications().find(record => record.key === "database-writer-health")?.resolvedAt).toBeNull();
});

it("activity_remount_offline_does_not_claim_service_recovery", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("unavailable", { status: 503 })));
  const first = render(<ServiceStatusPanel />); await settle(); first.unmount();
  online = false;
  render(<ServiceStatusPanel />); await settle();
  expect(notifications().find(record => record.key === "analysis-activity-error")?.resolvedAt).toBeNull();
  online = true;
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(activeActivity)));
  await wake();
  expect(notifications().find(record => record.key === "analysis-activity-error")?.resolvedAt).toBeTruthy();
});

it("activity_invalid_payload_and_explicit_retry_preserve_actionable_errors", async () => {
  let valid = false;
  const fetchMock = vi.fn(async () => Response.json(valid ? activeActivity : { items: [] }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
  expect(screen.getByRole("alert").textContent).toContain("unexpected format");
  valid = true; fireEvent.click(screen.getByRole("button", { name: "Retry status" })); await settle();
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText("Polling task")).toBeTruthy();
});

it("sync_status_invalid_payload_does_not_publish_false_success_and_recovers", async () => {
  let valid = false; let state!: GameSyncState;
  mockSyncFetch(() => Response.json(valid ? { providers: [], active_job: activeJob } : { providers: [], active_job: { ...activeJob, status: "imaginary" } }));
  function Probe() { state = useGameSync().state; return null; }
  render(<Probe />); await settle();
  expect(state.jobStatus).toBeUndefined();
  expect(debugErrors().some(record => record.context.source === "validated-data")).toBe(true);
  valid = true; await wake(); expect(state.jobStatus).toBe("running");
  const validationIncident = notifications().find(record => record.source === "validated-data");
  expect(validationIncident).toBeDefined(); expect(validationIncident?.resolvedAt).toBeTruthy();
});

it("activity_rapid_open_close_preserves_a_single_closed_timer", async () => {
  const fetchMock = vi.fn(async () => Response.json(activeActivity));
  vi.stubGlobal("fetch", fetchMock);
  render(<ServiceStatusPanel />); await settle();
  for (let toggle = 0; toggle < 3; toggle++) {
    fireEvent.click(screen.getByRole("button", { name: "Analysis activity" })); await settle();
    fireEvent.click(screen.getByRole("button", { name: "Close" })); await settle();
  }
  const baseline = fetchMock.mock.calls.length;
  await advance(29_999); expect(fetchMock).toHaveBeenCalledTimes(baseline);
  await advance(1); expect(fetchMock).toHaveBeenCalledTimes(baseline + 1);
});
