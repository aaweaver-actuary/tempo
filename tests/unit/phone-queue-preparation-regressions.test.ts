import { afterEach, expect, it, vi } from "vitest";
import { openingEvidenceStorage } from "../fixtures/opening-evidence-storage";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("phone preparation treats a refreshing projection as pending and preserves the saved queue", async () => {
  const storage = await openingEvidenceStorage();
  const existing = { localDate: "2026-10-05", preparedAt: "2026-10-05T12:00:00Z", cards: [],
    nextTemporaryId: 1_000_000_000_000, attempts: [{ cardId: "saved-review", completedAt: "2026-10-05T12:01:00Z" }] };
  storage.stores.training.set("prepared-daily-queue", existing);
  const { savePreparedTraining } = await import("../../app/lib/offline-training");
  await expect(savePreparedTraining({ local_date: "2026-10-05", prepared_at: "2026-10-05T12:02:00Z",
    cards: [], count: 0, projection: { state: "refreshing", generation: 1, updated_at: null, refresh_pending: 1, last_error: null, blocked_count: 0 } })).rejects.toMatchObject({ name: "PhoneQueueRefreshingError" });
  expect(storage.stores.training.get("prepared-daily-queue")).toEqual(existing);
  expect(storage.commits).toEqual([]);
});

it("phone preparation coalesces repeated failures and resolves them only when ready", async () => {
  await openingEvidenceStorage();
  const { showPhoneQueuePreparationNotice } = await import("../../app/views/fetchAndInitializeQueue");
  const { notifications, publishNotification } = await import("../../app/lib/notifications");
  publishNotification({ severity: "warning", source: "training queue", message: "Phone queue could not be prepared. Old warning" });
  for (let index = 0; index < 20; index++) showPhoneQueuePreparationNotice("Phone queue could not be prepared. Worker unavailable", "warning");
  expect(notifications().filter(record => record.key === "phone-queue-preparation")).toHaveLength(1);
  showPhoneQueuePreparationNotice("Phone offline queue is refreshing. The saved queue is retained.");
  expect(notifications().find(record => record.key === "phone-queue-preparation")?.resolvedAt).toBeNull();
  showPhoneQueuePreparationNotice("Phone queue prepared for 2026-10-05.", "info", true);
  const resolvedRecords = notifications().filter(record => record.source === "training queue");
  expect(resolvedRecords.every(record => Boolean(record.resolvedAt))).toBe(true);
  showPhoneQueuePreparationNotice("Phone queue prepared for 2026-10-05.", "info", true);
  expect(notifications()).toHaveLength(resolvedRecords.length);
});

it.each([
  { state: "failed", count: 0, message: "Queue worker unavailable" },
  { state: "ready", count: 1, message: "The daily queue is not fully prepared yet" },
])("phone preparation keeps genuine $state failures actionable without replacing stored attempts", async ({ state, count, message }) => {
  const storage = await openingEvidenceStorage();
  const existing = { attempts: [{ cardId: "unacknowledged" }] };
  storage.stores.training.set("prepared-daily-queue", existing);
  const { savePreparedTraining } = await import("../../app/lib/offline-training");
  await expect(savePreparedTraining({ local_date: "2026-10-05", prepared_at: "2026-10-05T12:02:00Z",
    cards: [], count, projection: { state, generation: 1, updated_at: null, refresh_pending: 0,
      last_error: "Queue worker unavailable", blocked_count: 0 } })).rejects.toThrow(message);
  expect(storage.stores.training.get("prepared-daily-queue")).toEqual(existing);
  expect(storage.commits).toEqual([]);
});
