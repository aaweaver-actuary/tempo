import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildNotificationExport, clearAllNotifications, clearNotification, clearNotificationHistory,
  notificationNeedsAttention, notificationToastIds, notifications,
  notificationsAtOrAbove, publishNotification, resolveNotification, updateNotification,
} from "../../app/lib/notifications";
import { NotificationCenter, NotificationViewport } from "../../app/components/notification-center";

beforeEach(() => { clearNotificationHistory(); vi.useRealTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("notification regressions", () => {
  it("clearing one notification removes only its badge contribution and retains exported history", () => {
    const clearedId = publishNotification({ severity: "warning", source: "sync", key: "sync-conflict",
      message: "Saved conflict", details: { cardIds: ["card-1"] } });
    publishNotification({ severity: "error", source: "service", message: "Service unavailable" });
    publishNotification({ severity: "info", source: "queue", message: "Queue ready" });
    const originalHistory = notifications();
    const { container } = render(<><NotificationCenter /><NotificationViewport /></>);
    expect(container.querySelector(".notification-count")?.textContent).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss sync notification" }));
    expect(container.querySelector(".notification-count")?.textContent).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    const conflictEntry = screen.getByText("Saved conflict").closest("article")!;
    fireEvent.click(within(conflictEntry).getByRole("button", { name: "Clear" }));
    expect(container.querySelector(".notification-count")?.textContent).toBe("1");
    expect(within(conflictEntry).getByText("Cleared")).toBeTruthy();
    expect(notifications().map((record) => record.id)).toEqual(originalHistory.map((record) => record.id));
    const clearedRecord = notifications().find((record) => record.id === clearedId)!;
    expect(clearedRecord).toEqual({ ...originalHistory.find((record) => record.id === clearedId)!,
      clearedAt: expect.any(String) });
    expect(clearedRecord.clearedAt).toEqual(expect.any(String));
    expect(JSON.parse(buildNotificationExport("warning")).notifications).toContainEqual(clearedRecord);
  });

  it("clear all acknowledges every retained notification regardless of severity filter", () => {
    const activeId = publishNotification({ severity: "info", source: "review", message: "Saving result…", active: true });
    const resolvedId = publishNotification({ severity: "error", source: "old service", message: "Old failure" });
    resolveNotification(resolvedId);
    publishNotification({ severity: "error", source: "service", message: "Current failure" });
    publishNotification({ severity: "warning", source: "sync", message: "Current conflict" });
    const originalHistory = notifications();
    const { container } = render(<><NotificationCenter /><NotificationViewport /></>);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    fireEvent.click(screen.getByRole("button", { name: "error" }));
    expect(container.querySelector(".notification-count")?.textContent).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(container.querySelector(".notification-count")).toBeNull();
    expect(notifications()).toEqual(originalHistory.map((record) => ({ ...record, clearedAt: expect.any(String) })));
    expect(notificationToastIds()).toEqual([activeId]);
    expect(screen.getByRole("button", { name: "Clear all" }).hasAttribute("disabled")).toBe(true);
    const clearedHistory = notifications();
    act(() => { clearAllNotifications(); clearNotification("missing-notification"); });
    expect(notifications()).toBe(clearedHistory);
    act(() => { publishNotification({ severity: "warning", source: "new sync", message: "New conflict" }); });
    expect(container.querySelector(".notification-count")?.textContent).toBe("1");
    expect(screen.getByRole("button", { name: "Clear all" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    const newEntry = within(within(screen.getByRole("region", { name: "Notifications" }))
      .getByText("New conflict").closest("article")!);
    expect(newEntry.getByText("New", { exact: true })).toBeTruthy();
  });

  it("unchanged cleared incidents stay cleared while changed and recurring incidents count again", () => {
    const incident = { severity: "error" as const, source: "sync status", key: "sync-incident", message: "Invalid status",
      details: { debugRecordId: "debug-1", stack: "first stack", status: 503, cardIds: ["card-1"] } };
    const incidentId = publishNotification(incident);
    clearNotification(incidentId);
    const firstSeen = notifications()[0].occurredAt;
    const acknowledgedAt = notifications()[0].clearedAt;
    publishNotification({ ...incident, details: { cardIds: ["card-1"], status: 503,
      stack: "second stack", debugRecordId: "debug-2" } });
    expect(notifications()[0]).toMatchObject({ id: incidentId, clearedAt: acknowledgedAt, occurrenceCount: 2,
      occurredAt: firstSeen, resolvedAt: null, details: { debugRecordId: "debug-2" } });
    expect(notifications().filter(notificationNeedsAttention)).toHaveLength(0);
    expect(notificationToastIds()).not.toContain(incidentId);
    updateNotification(incidentId, { details: { ...incident.details, debugRecordId: "debug-3", stack: "third stack" } });
    expect(notifications()[0].clearedAt).toBe(acknowledgedAt);
    expect(notificationToastIds()).not.toContain(incidentId);
    publishNotification({ ...incident, details: { ...incident.details, cardIds: ["card-2"] } });
    expect(notifications()[0]).toMatchObject({ id: incidentId, clearedAt: null });
    expect(notifications().filter(notificationNeedsAttention)).toHaveLength(1);
    expect(notificationToastIds()).toContain(incidentId);
    clearNotification(incidentId);
    updateNotification(incidentId, { message: "Changed failure" });
    expect(notifications()[0].clearedAt).toBeNull();
    clearNotification(incidentId);
    updateNotification(incidentId, { severity: "warning" });
    expect(notifications()[0].clearedAt).toBeNull();
    clearNotification(incidentId);
    publishNotification({ ...incident, severity: "warning", message: "Changed failure", source: "other sync" });
    expect(notifications()[0].clearedAt).toBeNull();
    clearNotification(incidentId);
    resolveNotification(incidentId);
    const recurrenceId = publishNotification(incident);
    expect(recurrenceId).not.toBe(incidentId);
    expect(notifications().find((record) => record.id === recurrenceId)?.clearedAt).toBeNull();
    expect(notifications().filter(notificationNeedsAttention)).toHaveLength(1);
    expect(notifications().find((record) => record.id === incidentId)?.resolvedAt).not.toBeNull();
  });

  it("cleared notifications survive reload and remain usable when storage writes fail", async () => {
    const incident = { severity: "warning" as const, source: "sync", key: "persistent-conflict", message: "Stored conflict" };
    const clearedId = publishNotification(incident);
    clearNotification(clearedId);
    const clearedRecord = notifications()[0];
    const legacyRecord = { id: "legacy-notification", severity: "error", source: "legacy", message: "Legacy failure",
      occurredAt: "2026-09-28T12:00:00.000Z", updatedAt: "2026-09-28T12:00:00.000Z", resolvedAt: null, active: false };
    localStorage.setItem("tempo-notifications-v1", JSON.stringify([clearedRecord, legacyRecord]));
    vi.resetModules();
    const fresh = await import("../../app/lib/notifications");
    fresh.hydrateNotifications();
    expect(fresh.notifications().find((record) => record.id === clearedId)).toEqual(clearedRecord);
    expect(fresh.notifications().find((record) => record.id === legacyRecord.id)?.clearedAt).toBeNull();
    expect(fresh.publishNotification(incident)).toBe(clearedId);
    expect(fresh.notifications().filter(fresh.notificationNeedsAttention)).toHaveLength(1);
    const blockedStorage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(() => fresh.clearAllNotifications()).not.toThrow();
    expect(fresh.notifications().filter(fresh.notificationNeedsAttention)).toHaveLength(0);
    fresh.publishNotification({ severity: "error", source: "new service", message: "New failure" });
    expect(fresh.notifications().filter(fresh.notificationNeedsAttention)).toHaveLength(1);
    expect(() => fresh.clearNotification(fresh.notifications()[0].id)).not.toThrow();
    expect(fresh.notifications().filter(fresh.notificationNeedsAttention)).toHaveLength(0);
    blockedStorage.mockRestore();
  });

  it("new notifications and updates remain newest first with severity thresholds", () => {
    const first = publishNotification({ severity: "info", source: "queue", message: "First" });
    publishNotification({ severity: "error", source: "sync", message: "Second" });
    updateNotification(first, { severity: "warning", message: "First updated" });
    expect(notifications().map((record) => record.message)).toEqual(["First updated", "Second"]);
    expect(notificationsAtOrAbove("warning").map((record) => record.message)).toEqual(["First updated", "Second"]);
    expect(notificationsAtOrAbove("error").map((record) => record.message)).toEqual(["Second"]);
  });

  it("saving status resolves in place and repeated unresolved conflicts do not duplicate", () => {
    const saving = publishNotification({ severity: "info", source: "review", key: "save:1", message: "Saving result…", active: true });
    resolveNotification(saving, { severity: "success", message: "Result saved." });
    expect(notifications()).toHaveLength(1);
    expect(notifications()[0]).toMatchObject({ id: saving, active: false, severity: "success", message: "Result saved." });
    expect(notifications()[0].resolvedAt).not.toBeNull();
    const conflict = { severity: "warning" as const, source: "phone review sync", key: "phone-conflicts", message: "1 conflict", details: { cardIds: ["card-1"] } };
    const first = publishNotification(conflict);
    expect(publishNotification(conflict)).toBe(first);
    expect(notifications()).toHaveLength(2);
  });

  it("a transient popup fades while its notification remains in the tray", () => {
    vi.useFakeTimers();
    publishNotification({ severity: "warning", source: "sync", message: "Conflict saved" });
    render(<><NotificationCenter /><NotificationViewport /></>);
    expect(screen.getByText("Conflict saved")).toBeTruthy();
    act(() => { vi.advanceTimersByTime(7_300); });
    expect(notificationToastIds()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.getByText("Conflict saved")).toBeTruthy();
  });

  it("active work stays visible until resolved and history keeps the latest 500", () => {
    vi.useFakeTimers();
    const activeId = publishNotification({ severity: "info", source: "review", message: "Saving result…", active: true });
    render(<NotificationViewport />);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(notificationToastIds()).toContain(activeId);
    act(() => { resolveNotification(activeId, { severity: "success", message: "Result saved." }); });
    act(() => { vi.advanceTimersByTime(4_300); });
    expect(notificationToastIds()).not.toContain(activeId);
    for (let index = 0; index < 505; index += 1)
      publishNotification({ severity: "info", source: "test", message: `Record ${index}` });
    expect(notifications()).toHaveLength(500);
    expect(notifications()[0].message).toBe("Record 504");
    expect(notifications().some((record) => record.message === "Record 0")).toBe(false);
  });

  it("history survives module reload and still works when storage writes fail", async () => {
    publishNotification({ severity: "warning", source: "sync", message: "Stored conflict" });
    vi.resetModules();
    const fresh = await import("../../app/lib/notifications");
    fresh.hydrateNotifications();
    expect(fresh.notifications().map((record) => record.message)).toContain("Stored conflict");
    const blocked = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(() => fresh.publishNotification({ severity: "info", source: "test", message: "Still works" })).not.toThrow();
    expect(fresh.notifications()[0].message).toBe("Still works");
    blocked.mockRestore();
  });

  it("hydrates legacy secret-bearing incident keys without losing counts or identity", async () => {
    const legacySignature = "api\u0000sync\u0000/api/games/sync/status\u0000Error\u0000password=canary-legacy-42";
    localStorage.setItem("tempo-notifications-v1", JSON.stringify([{
      id: "notification-legacy", key: `debug-incident:${legacySignature}`,
      occurredAt: "2026-09-28T12:00:00.000Z", updatedAt: "2026-09-29T12:00:00.000Z",
      occurrenceCount: 7, resolvedAt: null, active: true, severity: "error",
      source: "sync", message: "password=canary-legacy-42", details: {},
    }]));
    vi.resetModules();
    const fresh = await import("../../app/lib/notifications");
    fresh.hydrateNotifications();
    expect(fresh.notifications()[0]).toMatchObject({
      id: "notification-legacy", occurrenceCount: 7,
      occurredAt: "2026-09-28T12:00:00.000Z",
    });
    expect(fresh.notifications()[0].key).toMatch(/^debug-incident:[a-f0-9]{16}$/);
    expect(`${localStorage.getItem("tempo-notifications-v1")}${fresh.buildNotificationExport("error")}`)
      .not.toContain("canary-legacy-42");
  });

  it("severity JSON export includes safe details and excludes secrets", () => {
    publishNotification({ severity: "info", source: "test", message: "ordinary" });
    publishNotification({ severity: "warning", source: "sync", message: "Conflict for player@example.com?token=secret", details: { cardIds: ["card-1"] } });
    const exported = buildNotificationExport("warning");
    const payload = JSON.parse(exported) as { schemaVersion: number; severityThreshold: string; retainedRecordCount: number; notifications: Array<{ message: string; details: { cardIds: string[] } }> };
    expect(payload).toMatchObject({ schemaVersion: 1, severityThreshold: "warning", retainedRecordCount: 2 });
    expect(payload.notifications).toHaveLength(1);
    expect(payload.notifications[0].details.cardIds).toEqual(["card-1"]);
    expect(exported).not.toContain("player@example.com");
    expect(exported).not.toContain("token=secret");
  });

  it("repeated incident observations keep first seen history and count occurrences", () => {
    const incident = { severity: "error" as const, source: "sync status",
      key: "incident:sync-schema", message: "Invalid status" };
    const firstId = publishNotification(incident);
    expect(publishNotification(incident)).toBe(firstId);
    expect(notifications()).toHaveLength(1);
    expect(notifications()[0].occurrenceCount).toBe(2);
    const firstSeen = notifications()[0].occurredAt;
    resolveNotification(firstId);
    const recurrenceId = publishNotification(incident);
    expect(recurrenceId).not.toBe(firstId);
    expect(notifications().find((item) => item.id === firstId)?.occurredAt).toBe(firstSeen);
  });
});
