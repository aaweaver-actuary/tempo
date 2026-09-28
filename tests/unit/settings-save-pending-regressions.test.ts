import { afterEach, expect, it, vi } from "vitest";
import { PendingOperationError } from "../../app/lib/operation-status";
import { saveLocalSettings } from "../../app/lib/settings-save";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("settings retries a pending Celery save with its durable operation ID", async () => {
  const submittedKeys: string[] = [];
  let statusReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/settings")) {
      const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
      submittedKeys.push(key);
      return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
    }
    if (url.includes("/api/operations/")) {
      statusReads += 1;
      return Response.json(statusReads <= 2
        ? { state: "pending" }
        : { state: "complete", response: { new_cards_per_day: 12 } });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));

  await expect(saveLocalSettings({ new_cards_per_day: 12 })).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  expect(localStorage.getItem("tempo-pending-settings-save-v1")).not.toBeNull();
  await saveLocalSettings({ new_cards_per_day: 12 });
  expect(submittedKeys).toHaveLength(2);
  expect(submittedKeys[0]).toBe(submittedKeys[1]);
  expect(localStorage.getItem("tempo-pending-settings-save-v1")).toBeNull();
});

it("settings does not overtake an earlier pending save with changed values", async () => {
  const writes: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/settings")) {
      writes.push(String(init?.body));
      return Response.json({ operation_id: "settings-first", state: "pending" }, { status: 202 });
    }
    if (url.includes("/api/operations/"))
      return Response.json({ state: "pending" });
    throw new Error(`Unexpected request: ${url}`);
  }));
  await expect(saveLocalSettings({ new_cards_per_day: 12 })).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  await expect(saveLocalSettings({ new_cards_per_day: 13 })).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  expect(writes).toEqual([JSON.stringify({ new_cards_per_day: 12 })]);
});
