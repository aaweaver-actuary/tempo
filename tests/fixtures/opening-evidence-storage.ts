import { webcrypto } from "node:crypto";
import { vi } from "vitest";
import fixture from "./opening-evidence-manifest.json";
import { openingDecisionManifestSchema, type OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";

export function evidenceCompletion(attemptId = "offline-opening-attempt"): OpeningEvidenceCheckpoint {
  const manifest = openingDecisionManifestSchema.parse(fixture);
  return { attempt_id: attemptId, manifest, origin_queue_entry_id: 101, queue_entry_id: 101,
    parent_attempt_id: null, source: "offline", study_timezone: "UTC", started_at: "2026-10-03T12:00:00Z",
    terminal: { state: "complete", final_sequence: 3, ended_at: "2026-10-03T12:01:00Z" },
    events: manifest.decisions.map((decision, index) => ({ sequence: index + 1, decision_index: index,
      decision_id: decision.decision_id, expected_uci: decision.expected_uci, response_uci: decision.expected_uci,
      kind: "first_response", disposition: "expected", assistance: null, observed_at: "2026-10-03T12:00:30Z" })) };
}

/** Async requests with atomic commit/abort; real browser transaction coverage remains required. */
export async function openingEvidenceStorage() {
  vi.resetModules();
  vi.stubGlobal("indexedDB", {});
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("navigator", { onLine: true, locks: { request: vi.fn(async (_name, _options, callback) => callback({})) } });
  vi.stubGlobal("localStorage", { getItem: () => null });
  const stores: Record<string, Map<unknown, unknown>> = { training: new Map(), opening_attempts: new Map(), opening_events: new Map() };
  const writes: { store: string; value: unknown }[] = [];
  const commits: string[][] = [];
  const controls = { beforePut: undefined as ((store: string, value: unknown) => Error | undefined) | undefined,
    beforeCommit: undefined as (() => Error | undefined) | undefined };
  const database = {
    transaction: (names: string | string[], mode?: string) => {
      const selected = Array.isArray(names) ? names : [names];
      const rows = Object.fromEntries(selected.map(name => [name, new Map(structuredClone([...stores[name]]))]));
      let pendingRequests = 0;
      let aborted = false;
      const transaction = { error: null as Error | null, oncomplete: null as (() => void) | null,
        onerror: null as (() => void) | null, onabort: null as (() => void) | null,
        abort: () => { aborted = true; queueMicrotask(() => transaction.onabort?.()); },
        objectStore: (name: string) => {
          const request = (action: () => unknown, cloneResult = true) => {
            pendingRequests++;
            const result = { result: undefined as unknown, error: null as Error | null, onsuccess: null as (() => void) | null, onerror: null as (() => void) | null };
            queueMicrotask(() => {
              if (aborted) return;
              try { const value = action(); result.result = cloneResult ? structuredClone(value) : value; }
              catch (error) { result.error = transaction.error = error as Error; aborted = true; result.onerror?.(); transaction.onerror?.(); transaction.onabort?.(); return; }
              result.onsuccess?.();
              if (--pendingRequests === 0) queueMicrotask(() => {
                if (pendingRequests || aborted) return;
                if (mode === "readwrite") {
                  const error = controls.beforeCommit?.();
                  if (error) { transaction.error = error; aborted = true; transaction.onabort?.(); return; }
                  for (const store of selected) stores[store] = rows[store]; commits.push(selected); }
                transaction.oncomplete?.();
              });
            });
            return result;
          };
          const ordered = () => [...rows[name].entries()].sort(([left], [right]) => String(left).localeCompare(String(right)));
          const matching = (field: string, key: unknown) => ordered().map(([, value]) => value).filter(value => (value as Record<string, unknown>)[field] === key);
          return {
            get: (key: unknown) => request(() => rows[name].get(key)),
            getAll: () => request(() => ordered().map(([, value]) => value)),
            put: (value: Record<string, unknown>, key?: unknown) => request(() => {
              writes.push({ store: name, value: structuredClone(value) });
              const error = controls.beforePut?.(name, value); if (error) throw error;
              const primaryKey = key ?? (name === "opening_events" ? JSON.stringify([value.attempt_id, value.sequence]) : value.attempt_id);
              rows[name].set(primaryKey, structuredClone(value)); return primaryKey;
            }),
            delete: (key: unknown) => request(() => rows[name].delete(Array.isArray(key) ? JSON.stringify(key) : key)),
            index: (field: string) => ({ get: (key: unknown) => request(() => matching(field, key)[0]),
              getAll: (key: unknown) => request(() => matching(field, key)), count: (key: unknown) => request(() => matching(field, key).length),
              openCursor: (key: unknown) => {
                const entries = ordered().filter(([, value]) => (value as Record<string, unknown>)[field] === key);
                let position = 0;
                const cursor = request(() => entries.length ? next() : null, false);
                const next = (): unknown => {
                  if (position >= entries.length) return null;
                  const primaryKey = entries[position][0];
                  return { delete: () => request(() => rows[name].delete(primaryKey)),
                    continue: () => { position++; request(() => { cursor.result = next(); cursor.onsuccess?.(); }); } };
                };
                return cursor;
              } }),
            openCursor: () => {
              const entries = ordered(); let position = 0;
              const result = { result: null as unknown, onsuccess: null as (() => void) | null, onerror: null as (() => void) | null };
              const advance = () => { pendingRequests++; queueMicrotask(() => {
                result.result = entries[position] ? { value: structuredClone(entries[position++][1]), continue: advance } : null;
                result.onsuccess?.();
                if (--pendingRequests === 0) queueMicrotask(() => transaction.oncomplete?.());
              }); };
              advance(); return result;
            },
          };
        } };
      return transaction;
    },
  } as unknown as IDBDatabase;
  const install = async () => {
    const storage = await import("../../app/lib/offline-training-storage");
    vi.spyOn(storage, "offlineTrainingDatabase").mockResolvedValue(database);
  };
  await install();
  const seed = (completion: OpeningEvidenceCheckpoint) => {
    const { events, ...header } = structuredClone(completion);
    stores.opening_attempts.set(completion.attempt_id, { ...header, owner_session_id: "orphan-browser", final_sequence: events.length, delivery_state: "idle" });
    for (const event of events) stores.opening_events.set(JSON.stringify([completion.attempt_id, event.sequence]), { ...event, attempt_id: completion.attempt_id });
  };
  return { stores, controls, commits, writes, install, seed };
}
