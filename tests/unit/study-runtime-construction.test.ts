import { describe, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { openIntentStore } from "@tempo/browser-intents";
import { createStudyRuntime } from "../../app/features/study/session-runtime";
import { HeldStudyIntentError, STUDY_INTENT_KINDS, type StudyAdapterEntry } from "../../app/features/study/session-contracts";

const envelope = (kind: string, key = `key-${kind}`) => ({ key, kind, attemptId: `attempt-${kind}`, requestId: `request-${kind}`,
  endpoint: "/original", revision: 1, createdAt: "original", payload: { original: true } });
function entries(deliver = vi.fn(async () => ({ state: "committed" as const, receipt: { ok: true } }))): StudyAdapterEntry[] {
  return STUDY_INTENT_KINDS.map(kind => kind === "defense-command" || kind === "endgame-command"
    ? { kind, disposition: "held", reason: "retired producer" } : { kind, disposition: "deliver", adapter: { deliver } });
}
async function store(workspace: string) { return openIntentStore({ workspace, databaseName: workspace, indexedDB: new IDBFactory() }); }

describe("FE22 inert Study runtime construction", () => {
  it("imports without creating a session or opening storage", async () => {
    const random = vi.spyOn(globalThis.crypto, "randomUUID");
    try { vi.resetModules(); await import("../../app/features/study/session-runtime"); expect(random).not.toHaveBeenCalled(); }
    finally { random.mockRestore(); }
  });
  it("requires every kind exactly once and does not acquire a store at construction", async () => {
    const acquire = vi.fn(); const table = entries();
    expect(() => createStudyRuntime({ store: acquire, isCurrent: () => true, adapters: table.slice(1) })).toThrow("Missing Study adapter");
    expect(() => createStudyRuntime({ store: acquire, isCurrent: () => true, adapters: [...table, table[0]] })).toThrow("duplicate");
    expect(() => createStudyRuntime({ store: acquire, isCurrent: () => true, adapters: table })).not.toThrow();
    expect(acquire).not.toHaveBeenCalled();
  });
  it("holds retained unknown and retired identities without dispatch, and rejects new unknown before retention", async () => {
    const owned = await store("fe22-held"), deliver = vi.fn(async () => ({ state: "committed" as const }));
    try {
      const unknown = envelope("historic-unknown"), retired = envelope("defense-command");
      await owned.put(unknown); await owned.put(retired);
      const runtime = createStudyRuntime({ store: async () => owned, isCurrent: value => value === owned, adapters: entries(deliver) });
      const session = await runtime.session();
      await expect(session.checkSave(unknown.key)).rejects.toMatchObject({ name: "HeldStudyIntentError", key: unknown.key, kind: unknown.kind, requestId: unknown.requestId, attemptId: unknown.attemptId, endpoint: unknown.endpoint, revision: unknown.revision });
      await expect(session.checkSave(retired.key)).rejects.toBeInstanceOf(HeldStudyIntentError);
      expect(await owned.get(unknown.key)).toMatchObject({ state: "pending", ...unknown });
      expect(await owned.get(retired.key)).toMatchObject({ state: "pending", ...retired });
      await expect(session.submit(envelope("new-unknown"))).rejects.toThrow("Unknown new Study intent kind");
      expect(await owned.get("key-new-unknown")).toBeUndefined(); expect(deliver).not.toHaveBeenCalled();
      runtime.invalidate();
    } finally { owned.close(); }
  });
  it("reuses one store, invalidates old handles and rejects an old in-flight receipt after authority replacement", async () => {
    const first = await store("fe22-first"), second = await store("fe22-second");
    let current = first, started!: () => void, release!: () => void;
    const entered = new Promise<void>(resolve => { started = resolve; });
    const gate = new Promise<void>(resolve => { release = resolve; });
    const deliver = vi.fn(async () => { started(); await gate; return { state: "committed" as const, receipt: { original: true } }; });
    const runtime = createStudyRuntime({ store: async () => current, isCurrent: value => value === current, adapters: entries(deliver) });
    try {
      const a = await runtime.session(); expect(await runtime.session()).toBe(a);
      const request = envelope("review"); a.start(request.key);
      const pending = a.submit(request); await entered;
      current = second; const b = await runtime.session(); expect(b).not.toBe(a);
      expect(() => a.start(request.key)).toThrow("authority changed");
      release(); await expect(pending).rejects.toThrow("authority changed");
      expect(await first.get(request.key)).toMatchObject({ state: "pending", ...request });
      expect(await second.get(request.key)).toBeUndefined();
      runtime.invalidate(); expect(() => b.start("other")).toThrow("authority changed");
      expect(await runtime.session()).not.toBe(b);
    } finally { release(); runtime.invalidate(); first.close(); second.close(); }
  });
});

it("preserves prototype adapter receivers for delivery and claimed receipt recovery", async () => {
  class Client {
    sent = 0; checked = 0;
    async deliver() { this.sent++; return { state: "committed" as const, receipt: { source: "delivery" } }; }
    async checkReceipt() { this.checked++; return { state: "committed" as const, receipt: { source: "receipt" } }; }
  }
  const owned = await store("fe22-prototype"), client = new Client();
  const table = entries().map(entry => entry.kind === "review" ? { kind: "review" as const, disposition: "deliver" as const, adapter: client } : entry);
  const runtime = createStudyRuntime({ store: async () => owned, isCurrent: value => value === owned, adapters: table });
  try {
    const session = await runtime.session(), first = envelope("review", "first"), second = envelope("review", "second");
    session.start(first.key);
    expect(await session.submit(first)).toEqual({ state: "committed", receipt: { source: "delivery" }, data: undefined });
    await owned.put(second); expect(await owned.claim(second.key, "other", Date.now(), 60_000)).toBeTruthy();
    expect(await session.checkSave(second.key)).toEqual({ state: "committed", receipt: { source: "receipt" }, data: undefined });
    expect(client.sent).toBe(1); expect(client.checked).toBe(1);
    expect(await owned.get(first.key)).toMatchObject({ state: "committed", receipt: { source: "delivery" } });
    expect(await owned.get(second.key)).toMatchObject({ state: "committed", receipt: { source: "receipt" } });
  } finally { runtime.invalidate(); owned.close(); }
});

it.each(["committed", "discarded", "conflicted"] as const)("holds unknown and retired terminal %s records without rewriting them", async state => {
  const owned = await store(`fe22-terminal-${state}`), deliver = vi.fn(async () => ({ state: "committed" as const }));
  const unknown = envelope("historic-unknown"), retired = envelope("endgame-command");
  try {
    for (const record of [unknown, retired]) {
      await owned.put(record);
      await owned.update(record.key, latest => ({ ...latest, state, receipt: { original: state } }));
    }
    const runtime = createStudyRuntime({ store: async () => owned, isCurrent: value => value === owned, adapters: entries(deliver) });
    const session = await runtime.session();
    for (const original of [unknown, retired]) {
      const before = await owned.get(original.key);
      await expect(session.checkSave(original.key)).rejects.toMatchObject({ name: "HeldStudyIntentError", key: original.key, requestId: original.requestId, attemptId: original.attemptId });
      await expect(session.retrySave(original.key)).rejects.toMatchObject({ name: "HeldStudyIntentError", key: original.key });
      if (original.kind === "endgame-command") await expect(session.submit(original)).rejects.toMatchObject({ name: "HeldStudyIntentError", key: original.key });
      const advance = vi.fn();
      await expect(session.retryNext(original.key, advance, () => true)).rejects.toMatchObject({ name: "HeldStudyIntentError", key: original.key });
      expect(advance).not.toHaveBeenCalled();
      expect(await owned.get(original.key)).toEqual(before);
    }
    expect(deliver).not.toHaveBeenCalled(); runtime.invalidate();
  } finally { owned.close(); }
});
