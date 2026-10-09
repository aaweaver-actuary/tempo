import { afterEach, expect, it, vi } from "vitest";
import { clearFinishedActivity } from "../../app/lib/activity-clear-command";
const first = { completion_cutoff: "2026-10-01T00:00:00+00:00", completion_snapshot: "first-signed" };
const later = { completion_cutoff: "2026-10-02T00:00:00+00:00", completion_snapshot: "later-signed" };
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("clear finished retains the original snapshot across lost delivery and replays its receipt identity", async () => {
  const request = vi.fn().mockRejectedValueOnce(new Error("Lost response"))
    .mockResolvedValueOnce(Response.json({ state: "unknown" }))
    .mockResolvedValueOnce(Response.json({ ok: true, cleared_through: first.completion_cutoff }));
  vi.stubGlobal("fetch", request);
  await expect(clearFinishedActivity(first)).rejects.toThrow("Lost response");
  await clearFinishedActivity(later);
  const original = request.mock.calls[0][1];
  expect(request.mock.calls[2][1]).toEqual(original);
  expect(JSON.parse(original.body)).toEqual(first);
  expect(localStorage.getItem("tempo-pending-activity-clear-v1")).toBeNull();
});
it("pending and failed clear receipts never report false success or hide finished work", async () => {
  const request = vi.fn().mockResolvedValueOnce(Response.json({ operation_id: "pending-clear" }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: "pending" }))
    .mockResolvedValueOnce(Response.json({ state: "failed", error: { message: "Database unavailable" } }));
  vi.stubGlobal("fetch", request);
  await expect(clearFinishedActivity(first)).rejects.toThrow("pending");
  expect(localStorage.getItem("tempo-pending-activity-clear-v1")).not.toBeNull();
  await expect(clearFinishedActivity(later)).rejects.toThrow("Database unavailable");
  expect(request).toHaveBeenCalledTimes(3);
  expect(localStorage.getItem("tempo-pending-activity-clear-v1")).toBeNull();
});
it("completed clear receipt must confirm at least the saved cutoff", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ok: true, cleared_through: first.completion_cutoff })));
  await expect(clearFinishedActivity(later)).rejects.toThrow("not confirmed");
  expect(localStorage.getItem("tempo-pending-activity-clear-v1")).not.toBeNull();
});
