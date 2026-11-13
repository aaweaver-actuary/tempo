import { afterEach, expect, it, vi } from "vitest";
import { PendingOperationError } from "../../app/lib/operation-status";
import { saveLocalSettings } from "../../app/lib/settings-save";

const settingsResult = { initial_depth: 5, timezone: "UTC", new_cards_per_day: 12,
  study_new_per_day: 2, tactics_new_per_day: 5, study_only_mode: false, light_first_interval_days: 1 };

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
        : { state: "complete", response: settingsResult });
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

it.each(["foreign", "missing"])("%s initial acknowledgment retains settings key and exact body", async kind => {
  let savedDuringSend: string | null = null;
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).toBe("PUT");
    savedDuringSend = localStorage.getItem("tempo-pending-settings-save-v1");
    expect(init?.body).toBe(JSON.stringify({ new_cards_per_day: 12 }));
    return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(saveLocalSettings({ new_cards_per_day: 12 })).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-settings-save-v1")).toBe(savedDuringSend);
  expect(fetcher).toHaveBeenCalledOnce();
});

it.each(["direct", "receipt"])("malformed %s settings result retains the original key and bytes", async kind => {
  const original = JSON.stringify({ operationId: "original", body: JSON.stringify({ new_cards_per_day: 12 }) });
  if (kind === "receipt") localStorage.setItem("tempo-pending-settings-save-v1", original);
  let sent: string | null = null;
  vi.stubGlobal("fetch", vi.fn(async () => {
    sent = localStorage.getItem("tempo-pending-settings-save-v1");
    return Response.json(kind === "receipt" ? { state: "complete", response: { new_cards_per_day: 12 } } : { new_cards_per_day: 12 });
  }));
  await expect(saveLocalSettings({ new_cards_per_day: 12 })).rejects.toBeInstanceOf(PendingOperationError);
  expect(localStorage.getItem("tempo-pending-settings-save-v1")).toBe(kind === "receipt" ? original : sent);
});

it.each(["direct", "receipt"])("valid %s settings result clears only after validation", async kind => {
  const original = JSON.stringify({ operationId: "original", body: JSON.stringify({ new_cards_per_day: 12 }) });
  if (kind === "receipt") localStorage.setItem("tempo-pending-settings-save-v1", original);
  const fetcher = vi.fn(async () => Response.json(kind === "receipt"
    ? { operation_id: "original", state: "complete", response: settingsResult } : settingsResult));
  vi.stubGlobal("fetch", fetcher);
  await saveLocalSettings({ new_cards_per_day: 12 });
  expect(fetcher).toHaveBeenCalledOnce();
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

it.each([
  ["malformed", { state: "complete" }],
  ["foreign", { state: "complete", operation_id: "other", response: { new_cards_per_day: 12 } }],
])("%s receipt preserves the saved operation", async (_label, receipt) => {
  const original = JSON.stringify({ operationId: "original", body: JSON.stringify({ new_cards_per_day: 12 }) });
  localStorage.setItem("tempo-pending-settings-save-v1", original);
  const fetcher = vi.fn(async () => Response.json(receipt));
  vi.stubGlobal("fetch", fetcher);
  await expect(saveLocalSettings({ new_cards_per_day: 13 })).rejects.toThrow();
  expect(localStorage.getItem("tempo-pending-settings-save-v1")).toBe(original);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
