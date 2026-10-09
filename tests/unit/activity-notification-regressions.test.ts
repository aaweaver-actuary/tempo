import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { startActivityNotificationPolling } from "../../app/lib/activity-notifications";
import { clearNotificationHistory, importActivityIncident, notifications, notificationToastIds, hideNotificationToast } from "../../app/lib/notifications";
import { activityNotificationPageSchema } from "../../app/domain/schemas/activity-notifications";

const workspace = "5c4fa2da-df89-4463-828d-522f3b7981a8";
const incident = { sequence: 1, event: "opened" as const, occurred_at: "2026-10-09T00:00:00Z",
  id: "550f862c-a8ee-4070-ab3b-240e09623a76", kind: "daily_queue" as const, source: "durable" as const,
  work_id: "queue-task", generation_key: "1", reason: "repeated_timeout" as const,
  opened_at: "2026-10-09T00:00:00Z", resolved_at: null, last_progress_at: null };
const page = { workspace_id: workspace, available: true, monitoring_available: false, monitoring_as_of: null,
  items: [incident], next_cursor: 1, has_more: false };
let stop: (() => void) | undefined;
beforeEach(() => { localStorage.clear(); clearNotificationHistory(); vi.useFakeTimers(); });
afterEach(() => { stop?.(); stop = undefined; vi.unstubAllGlobals(); vi.useRealTimers(); });

it("durable activity incidents toast once across polling and resolve only on the server's recovery event", () => {
  importActivityIncident(workspace, incident);
  const id = notificationToastIds()[0];
  hideNotificationToast(id);
  importActivityIncident(workspace, incident);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0].occurrenceCount).toBe(1);
  expect(notificationToastIds()).toEqual([]);
  importActivityIncident(workspace, { ...incident, sequence: 2, event: "resolved", resolved_at: "2026-10-09T00:01:00Z", last_progress_at: "2026-10-09T00:01:00Z" });
  expect(notifications()[0]).toMatchObject({ severity: "success", active: false, resolvedAt: "2026-10-09T00:01:00Z" });
  expect(notificationToastIds()).toHaveLength(1);
  importActivityIncident(workspace, { ...incident, sequence: 2, event: "resolved", resolved_at: "2026-10-09T00:01:00Z" });
  expect(notificationToastIds()).toHaveLength(1);
});
it("historical resolved incidents on a new device remain inspectable without warning or recovery toasts", () => {
  importActivityIncident(workspace, { ...incident, resolved_at: "2026-10-09T00:01:00Z" });
  expect(notifications()[0].severity).toBe("success");
  expect(notificationToastIds()).toEqual([]);
});
it("notification polling preserves the cursor and unresolved incident on unavailable or invalid evidence", async () => {
  const read = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(page)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...page, items: [{ ...incident, resolved_at: "invalid" }] })))
    .mockResolvedValueOnce(new Response("unavailable", { status: 503 }));
  vi.stubGlobal("fetch", read);
  const availability = vi.fn(); stop = startActivityNotificationPolling(availability);
  await vi.advanceTimersByTimeAsync(0);
  expect(read.mock.calls[0][0]).toContain("/api/system/activity/notifications?after=0");
  expect(new Headers(read.mock.calls[0][1].headers).get("X-Tempo-Work-Class")).toBe("background");
  await vi.advanceTimersByTimeAsync(60_000);
  expect(availability).toHaveBeenLastCalledWith(false);
  expect(JSON.parse(localStorage.getItem("tempo-activity-notification-cursor-v1")!).sequence).toBe(1);
  expect(notifications()[0].resolvedAt).toBeNull();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(notifications()[0].resolvedAt).toBeNull();
  expect(read.mock.calls[2][0]).toContain("after=1");
});
it("notification polling resets a restored workspace cursor before importing bounded ordered pages", async () => {
  localStorage.setItem("tempo-activity-notification-cursor-v1", JSON.stringify({ workspaceId: "old-workspace", sequence: 99 }));
  const read = vi.fn().mockImplementation(async () => new Response(JSON.stringify(page)));
  vi.stubGlobal("fetch", read); stop = startActivityNotificationPolling(vi.fn());
  await vi.advanceTimersByTimeAsync(1);
  expect(read.mock.calls[0][0]).toContain("after=99");
  expect(read.mock.calls[1][0]).toContain("after=0");
  expect(notifications()).toHaveLength(1);
});
it("notification polling has one flight and cancellation retains the previous cursor", async () => {
  const read = vi.fn().mockImplementation(() => new Promise(() => {}));
  vi.stubGlobal("fetch", read); stop = startActivityNotificationPolling(vi.fn());
  window.dispatchEvent(new Event("online")); document.dispatchEvent(new Event("visibilitychange"));
  expect(read).toHaveBeenCalledTimes(1);
  stop(); expect(read.mock.calls[0][1].signal.aborted).toBe(true);
  expect(localStorage.getItem("tempo-activity-notification-cursor-v1")).toBeNull();
});
it("durable notification consumer rejects duplicate sequences and non-advancing page cutoffs", () => {
  expect(activityNotificationPageSchema.safeParse({ ...page, items: [incident, incident] }).success).toBe(false);
  expect(activityNotificationPageSchema.safeParse({ ...page, next_cursor: 2 }).success).toBe(false);
  expect(activityNotificationPageSchema.safeParse({ ...page, items: [], has_more: true }).success).toBe(false);
});

it("durable notification consumer rejects recovery without accepted progress or a valid episode order", () => {
  const recovery = { ...incident, event: "resolved", resolved_at: "2026-10-09T00:01:00Z", last_progress_at: "2026-10-09T00:01:00Z" };
  expect(activityNotificationPageSchema.safeParse({ ...page, items: [recovery] }).success).toBe(true);
  for (const inconsistent of [{ ...recovery, resolved_at: null }, { ...recovery, last_progress_at: null },
    { ...recovery, resolved_at: "2026-10-08T23:59:00Z" }])
    expect(activityNotificationPageSchema.safeParse({ ...page, items: [inconsistent] }).success).toBe(false);
});
