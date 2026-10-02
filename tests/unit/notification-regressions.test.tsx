import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildNotificationExport, clearAllNotifications, clearNotification, clearNotificationHistory,
  groupNotifications, notificationNeedsAttention, notificationToastIds, notifications,
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
    expect(screen.queryByText("Saved conflict")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(within(screen.getByText("Saved conflict").closest("article")!).getByText("Cleared")).toBeTruthy();
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
    expect(notificationToastIds()).toEqual([]);
    expect(notifications().find((record) => record.id === activeId)?.active).toBe(true);
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

  it("routine review saves never show popups", () => {
    const saving = publishNotification({ severity: "info", source: "review", key: "save:quiet", message: "Saving result…", active: true });
    const view = render(<><NotificationCenter /><NotificationViewport /></>);
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    act(() => resolveNotification(saving, { severity: "success", message: "Result saved." }));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByText("Result saved.")).toBeTruthy();
  });

  it("clearing grouped notifications preserves independent operations and new arrivals", () => {
    const notice = { severity: "warning" as const, source: "discovery save", message: "Save pending" };
    const first = publishNotification({ ...notice, key: "discovery:first" });
    const second = publishNotification({ ...notice, key: "discovery:second" });
    const view = render(<><NotificationCenter /><NotificationViewport /></>);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(0);
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    expect(notifications()).toHaveLength(2);
    expect(notifications().every((record) => record.clearedAt && !record.resolvedAt)).toBe(true);
    act(() => publishNotification({ ...notice, key: "discovery:first" }));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    act(() => publishNotification({ ...notice, key: "discovery:third" }));
    expect(view.container.querySelector(".notification-count")?.textContent).toBe("1");
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(1);
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(2);
    const exportedRecords = JSON.parse(buildNotificationExport("warning")).notifications;
    expect(exportedRecords).toHaveLength(3);
    expect(exportedRecords.map((record: { key: string }) => record.key).sort())
      .toEqual(["discovery:first", "discovery:second", "discovery:third"]);
    expect(exportedRecords.filter((record: { clearedAt: string | null }) => record.clearedAt)).toHaveLength(2);
    act(() => resolveNotification(first, { severity: "success", message: "Save confirmed" }));
    expect(notifications().find((record) => record.id === second)?.resolvedAt).toBeNull();
    expect(view.container.querySelector(".notification-count")?.textContent).toBe("1");
  });

  it("identical retries share one entry without reopening or extending the popup", () => {
    vi.useFakeTimers();
    const notice = { severity: "warning" as const, source: "training queue", message: "Guided attempt save pending. Tempo will retry." };
    publishNotification(notice);
    const view = render(<><NotificationCenter /><NotificationViewport /></>);
    act(() => vi.advanceTimersByTime(2_000));
    act(() => { publishNotification(notice); publishNotification({ ...notice, key: "guided:other" }); });
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(1);
    expect(view.container.querySelector(".notification-item")?.textContent).toContain("Repeated 3 times");
    expect(view.container.querySelector(".notification-count")?.textContent).toBe("1");
    act(() => vi.advanceTimersByTime(5_300));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    act(() => publishNotification(notice));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    act(() => publishNotification({ ...notice, message: "Reconnect to retry." }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss training queue notification" }));
    act(() => publishNotification({ ...notice, message: "Reconnect to retry." }));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
  });

  it("grouped discovery warnings resolve independently", () => {
    const notice = { severity: "warning" as const, source: "discovery save", message: "Discovery save unconfirmed; Tempo will retry." };
    const first = publishNotification({ ...notice, key: "discovery-save:first" });
    const second = publishNotification({ ...notice, key: "discovery-save:second" });
    const view = render(<NotificationCenter />);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(1);
    act(() => resolveNotification(first, { severity: "success", message: "Discovery save confirmed." }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(1);
    expect(screen.queryByText("Discovery save confirmed.")).toBeNull();
    expect(notifications().find((record) => record.id === second)?.resolvedAt).toBeNull();
    expect(JSON.parse(buildNotificationExport("info")).notifications).toHaveLength(2);
    act(() => resolveNotification(second, { severity: "success", message: "Discovery save confirmed." }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(0);
    expect(view.container.querySelector(".notification-count")).toBeNull();
  });

  it("notification history opens to unresolved warnings and errors", () => {
    publishNotification({ severity: "info", source: "review", message: "Saving quietly" });
    publishNotification({ severity: "success", source: "settings", message: "Settings saved" });
    const resolved = publishNotification({ severity: "error", source: "service", message: "Old failure" });
    resolveNotification(resolved);
    publishNotification({ severity: "warning", source: "sync", message: "Reconnect" });
    publishNotification({ severity: "error", source: "review", message: "Retry save" });
    const view = render(<NotificationCenter />);
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.getByRole("button", { name: "Needs attention" }).getAttribute("aria-pressed")).toBe("true");
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(2);
    expect(screen.queryByText("Old failure")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.getByRole("button", { name: "Needs attention" }).getAttribute("aria-pressed")).toBe("true");
    expect(view.container.querySelectorAll(".notification-item")).toHaveLength(2);
  });

  it("keyed diagnostic repeats keep their popup deadline when details change", () => {
    vi.useFakeTimers();
    const incident = { severity: "error" as const, source: "sync", key: "incident:sync", message: "Invalid status" };
    publishNotification({ ...incident, details: { debugRecordId: "first" } });
    const view = render(<NotificationViewport />);
    act(() => vi.advanceTimersByTime(2_000));
    act(() => publishNotification({ ...incident, details: { debugRecordId: "second" } }));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(1);
    act(() => vi.advanceTimersByTime(5_300));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    act(() => publishNotification({ ...incident, details: { debugRecordId: "third" } }));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
  });

  it("confirming one grouped save does not restart the remaining warning popup", () => {
    vi.useFakeTimers();
    const notice = { severity: "warning" as const, source: "discovery save", message: "Still pending" };
    const first = publishNotification({ ...notice, key: "discovery:first" });
    publishNotification({ ...notice, key: "discovery:second" });
    const view = render(<NotificationViewport />);
    act(() => vi.advanceTimersByTime(2_000));
    act(() => resolveNotification(first, { severity: "success", message: "Confirmed" }));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(1);
    act(() => vi.advanceTimersByTime(5_300));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
  });

  it("grouping keeps different sources severities details and resolution states separate", () => {
    const notice = { severity: "warning" as const, source: "sync", message: "Retry", details: { cardId: "one", endpointPath: "/one" } };
    publishNotification(notice);
    publishNotification({ ...notice, details: { endpointPath: "/one", cardId: "one" } });
    publishNotification({ ...notice, source: "review" });
    publishNotification({ ...notice, severity: "error" });
    publishNotification({ ...notice, details: { ...notice.details, endpointPath: "/two" } });
    const resolved = publishNotification(notice);
    resolveNotification(resolved);
    expect(groupNotifications(notifications())).toHaveLength(5);
    expect(groupNotifications(notifications()).find((group) => group.occurrenceCount === 2)).toBeTruthy();
    expect(JSON.parse(buildNotificationExport("warning")).notifications).toHaveLength(6);
  });

  it("stored duplicate warnings group after reload without losing operation identities", async () => {
    const notice = { severity: "warning" as const, source: "discovery save", message: "Still pending", details: { selectedMove: "e2e4" } };
    publishNotification({ ...notice, key: "discovery:first" });
    publishNotification({ ...notice, key: "discovery:first" });
    publishNotification({ ...notice, key: "discovery:second" });
    vi.resetModules();
    const fresh = await import("../../app/lib/notifications");
    fresh.hydrateNotifications();
    expect(fresh.groupNotifications(fresh.notifications())).toMatchObject([{ occurrenceCount: 3 }]);
    expect(fresh.notificationToastIds()).toHaveLength(0);
    const exported = JSON.parse(fresh.buildNotificationExport("warning"));
    expect(exported.notifications.map((record: { key: string }) => record.key).sort()).toEqual(["discovery:first", "discovery:second"]);
    expect(exported.notifications.every((record: { details: unknown }) => JSON.stringify(record.details) === JSON.stringify(notice.details))).toBe(true);
  });

  it("warning popups stay bounded and expire even while work remains active", () => {
    vi.useFakeTimers();
    for (let index = 0; index < 4; index += 1)
      publishNotification({ severity: "warning", source: "review", message: `Pending ${index}`, active: true });
    const view = render(<NotificationViewport />);
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(3);
    act(() => vi.advanceTimersByTime(7_300));
    expect(view.container.querySelectorAll(".notification-toast")).toHaveLength(0);
    expect(notifications()).toHaveLength(4);
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

  it("active work stays in history without a popup and history keeps the latest 500", () => {
    vi.useFakeTimers();
    const activeId = publishNotification({ severity: "info", source: "review", message: "Saving result…", active: true });
    render(<NotificationViewport />);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(notificationToastIds()).not.toContain(activeId);
    expect(notifications().find((record) => record.id === activeId)?.active).toBe(true);
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
