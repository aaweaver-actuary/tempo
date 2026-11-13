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
