import { expect, it, vi } from "vitest";
import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import type { StudyClient } from "../../app/lib/study-connection";
import { createStudyCommandDelivery } from "../../app/lib/study-command-client";

function fixture(started = false) {
  const record = { key: "scope:original", kind: "study-command", requestId: "original", attemptId: "attempt",
    endpoint: "/api/study/attempt", data: { started }, payload: { scope: "scope", body: { attempt_id: "attempt" },
      headers: { "Idempotency-Key": "original", "Content-Type": "application/json" } } } as IntentRecord;
  const store = { update: vi.fn(async (_key, change) => change(record)), list: vi.fn(async () => []) } as unknown as IntentStore;
  return { record, store };
}
function client(transport: typeof fetch) {
  return { origin: "https://a.example", generation: 1, independent: true, learningFetch: transport } as unknown as StudyClient;
}

it("uses the supplied authority and store for POST and original receipt", async () => {
  const { record, store } = fixture();
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      expect(input).toBe(record.endpoint);
      expect(init.headers).toEqual({ "Idempotency-Key": "original", "Content-Type": "application/json" });
      expect(JSON.parse(String(init.body))).toEqual({ attempt_id: "attempt" });
      return Response.json({ operation_id: "original" }, { status: 202 });
    }
    expect(String(input)).toBe("https://a.example/api/operations/original");
    return Response.json({ operation_id: "original", state: "complete", response: { attempt_id: "attempt" } });
  });
  const delivery = createStudyCommandDelivery(client(fetch), async () => store);
  expect((await delivery.deliver(record, new AbortController().signal)).state).toBe("committed");
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(store.update).toHaveBeenCalledOnce();
});

it("refuses receipt polling after the supplied authority changes during a 202 body read", async () => {
  const { record, store } = fixture();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let reading!: () => void;
  const entered = new Promise<void>(resolve => { reading = resolve; });
  const fetch = vi.fn(async () => {
    const response = new Response(null, { status: 202 });
    response.clone = () => ({ json: async () => { reading(); await gate; return { operation_id: "original" }; } }) as Response;
    return response;
  });
  const selected = client(fetch);
  const delivery = createStudyCommandDelivery(selected, async () => store);
  const pending = delivery.deliver(record, new AbortController().signal);
  await entered;
  Object.defineProperty(selected, "generation", { value: 2 });
  release();
  await expect(pending).rejects.toThrow("Study authority changed");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("checks each original receipt through its own supplied client", async () => {
  const { record, store } = fixture(true);
  const otherStore = fixture(true).store;
  const firstFetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ operation_id: "original", state: "complete", response: { attempt_id: "attempt" } }));
  const secondFetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ operation_id: "original", state: "complete", response: { attempt_id: "attempt" } }));
  const first = client(firstFetch), second = client(secondFetch);
  Object.defineProperty(second, "origin", { value: "https://b.example" });
  const a = createStudyCommandDelivery(first, async () => store);
  const b = createStudyCommandDelivery(second, async () => otherStore);
  expect((await a.checkReceipt(record, new AbortController().signal))?.state).toBe("committed");
  expect((await b.checkReceipt(record, new AbortController().signal))?.state).toBe("committed");
  expect(String(firstFetch.mock.calls[0][0])).toBe("https://a.example/api/operations/original");
  expect(String(secondFetch.mock.calls[0][0])).toBe("https://b.example/api/operations/original");
});
