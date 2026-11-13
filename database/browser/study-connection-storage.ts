import type { RetainedAuthorityInspection, RetainedStudyExport, RetainedWorkFact } from "../../app/domain/study-authority";
/** Existing browser storage operations; connection/authority decisions stay with Study. */
const connectionKey = "tempo-learning-connection-v1";
const authoritiesKey = "tempo-learning-authorities-v1";

export function sessionConnectionAvailable() { return typeof sessionStorage !== "undefined"; }
export function readSessionConnection() { return sessionStorage.getItem(connectionKey); }
export function writeSessionConnection(value: string) { sessionStorage.setItem(connectionKey, value); }
export function removeSessionConnection() { sessionStorage.removeItem(connectionKey); }
export function readAuthorityRegistry() { return localStorage.getItem(authoritiesKey); }
export function writeAuthorityRegistry(value: string) { localStorage.setItem(authoritiesKey, value); }

/** Read existing stores without upgrading, rewriting or deleting retained data. */
export async function readRetainedStudyData(registeredOfflineDatabases: () => string[], validateStorage = false): Promise<RetainedStudyExport> {
  if (!globalThis.indexedDB) throw new Error("Durable Study storage is unavailable. Enable browser storage and retry.");
  const databases: Record<string, Record<string, unknown[]>> = {};
  const registered = registeredOfflineDatabases();
  const discovered = typeof indexedDB.databases === "function" ? (await indexedDB.databases()).map(database => database.name).filter((name): name is string => Boolean(name?.startsWith("tempo-offline-training:"))) : [];
  for (const name of new Set(["tempo-study-intents", "tempo-offline-training", ...registered, ...discovered])) {
    databases[name] = await new Promise((resolve, reject) => {
      const request = indexedDB.open(name);
      let absent = false;
      request.onupgradeneeded = () => { absent = true; request.transaction!.abort(); };
      request.onerror = () => absent ? resolve({}) : reject(request.error);
      request.onblocked = () => reject(new Error("Close older Tempo tabs before inspecting saved Study work."));
      request.onsuccess = async () => {
        const database = request.result;
        try {
          const names = [...database.objectStoreNames];
          if (validateStorage && name === "tempo-study-intents") {
            if (database.version !== 1 || !["intents", "teaching", "metadata"].every(store => names.includes(store)))
              throw new Error("Saved Study storage has an incompatible schema. Keep browser data.");
            const inspection = database.transaction(names, "readonly");
            for (const store of ["intents", "teaching", "metadata"]) {
              if (JSON.stringify(inspection.objectStore(store).keyPath) !== '["workspace","key"]')
                throw new Error("Saved Study storage has incompatible keys. Keep browser data.");
            }
            for (const [store, indexes] of [["intents", ["workspace", "state"]], ["teaching", ["workspace", "card"]]] as const)
              if (!indexes.every(index => inspection.objectStore(store).indexNames.contains(index)))
                throw new Error("Saved Study storage has incompatible indexes. Keep browser data.");
          }
          if (validateStorage && name.startsWith("tempo-offline-training:" ) &&
              (database.version !== 2 || !["training", "opening_attempts", "opening_events"].every(store => names.includes(store))))
            throw new Error("Saved offline Study storage has an incompatible schema. Keep browser data.");
          if (!names.length) throw new Error("Saved Study storage has an incompatible schema. Keep browser data.");
          const transaction = database.transaction(names, "readonly");
          const values = await Promise.all(names.map(store => new Promise<[string, unknown[]]>((read, fail) => {
            const result = transaction.objectStore(store).getAll();
            result.onsuccess = () => read([store, result.result]); result.onerror = () => fail(result.error);
          })));
          transaction.oncomplete = () => resolve(Object.fromEntries(values));
          transaction.onabort = () => reject(transaction.error ?? new Error("Saved Study inspection was aborted."));
        } catch (error) { reject(error); } finally { database.close(); }
      };
    });
  }
  const legacy = Object.fromEntries(Object.keys(localStorage).filter(key =>
    /^tempo-(?:pending-(?:training|teaching|endgame|burial|tactic-capture-v1$|card-delete-v1$)|bury-(?:operation|identity)|seen-moves|first-clean-passes)/.test(key))
    .map(key => [key, localStorage.getItem(key)]).filter(([,value]) => value && value !== "[]"));
  return { databases, legacy };
}

/** Translate existing formats without changing their data or terminal semantics. */
export async function inspectRetainedStudyAuthority(registeredOfflineDatabases: () => string[]): Promise<RetainedAuthorityInspection> {
  const retained = await readRetainedStudyData(registeredOfflineDatabases, true);
  const facts: RetainedWorkFact[] = [];
  const record = (value: unknown): Record<string, unknown> | undefined =>
    value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
  for (const value of retained.databases["tempo-study-intents"].intents ?? []) {
    const intent = record(value);
    facts.push({ authority: typeof intent?.workspace === "string" ? intent.workspace : null,
      disposition: (intent?.state === "committed" || intent?.state === "discarded") ? "terminal" : "unresolved" });
  }
  const intentStores = new Set(["intents", "teaching", "metadata"]);
  for (const [store, values] of Object.entries(retained.databases["tempo-study-intents"]))
    if (!intentStores.has(store) && values.length) facts.push({ authority: null, disposition: "unknown" });
  const offlineStores = new Set(["training", "opening_attempts", "opening_events"]);
  for (const [name, stores] of Object.entries(retained.databases)) {
    if (!name.startsWith("tempo-offline-training")) continue;
    let authority: string | null = null;
    if (name.startsWith("tempo-offline-training:")) {
      const encoded = name.slice("tempo-offline-training:".length);
      try {
        const decoded = decodeURIComponent(encoded);
        if (decoded && encodeURIComponent(decoded) === encoded) authority = decoded;
      } catch { /* Unrecognized authority remains unconfirmed, not empty. */ }
    }
    for (const [store, values] of Object.entries(stores))
      if (!offlineStores.has(store) && values.length) facts.push({ authority, disposition: "unknown" });
    for (const value of stores.opening_attempts ?? []) {
      const attempt = record(value);
      facts.push({ authority, disposition: (attempt?.delivery_state === "complete" || attempt?.delivery_state === "rejected")
        ? "terminal" : "unresolved" });
    }
    for (const value of stores.training ?? []) {
      const prepared = record(value);
      if (!Array.isArray(prepared?.attempts)) {
        // A malformed foreign snapshot is not evidence that retained work is empty.
        facts.push({ authority, disposition: "unknown" });
        continue;
      }
      for (const value of prepared.attempts) {
        const attempt = record(value);
        facts.push({ authority, disposition: !attempt ? "unknown" :
          attempt.serverReviewId || attempt.serverAcknowledged ? "terminal" : "unresolved" });
      }
    }
  }
  for (const _key of Object.keys(retained.legacy)) facts.push({ authority: null, disposition: "unresolved" });
  return { facts, retained };
}
