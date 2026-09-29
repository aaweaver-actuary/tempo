import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildNotificationExport, clearNotificationHistory, notificationToastIds, notifications,
  notificationsAtOrAbove, publishNotification, resolveNotification, updateNotification,
} from "../../app/lib/notifications";
import { NotificationCenter, NotificationViewport } from "../../app/components/notification-center";

beforeEach(() => { clearNotificationHistory(); vi.useRealTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("notification regressions", () => {
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
