/** Durable commands are immutable. Claims coordinate delivery; receipts establish settlement. */
export type IntentEnvelope = {
  key: string; kind: string; attemptId: string; requestId: string;
  endpoint: string; revision?: number; createdAt: string; payload: unknown;
};
export type IntentState = "pending" | "conflicted" | "committed" | "discarded";
export type IntentRecord = IntentEnvelope & {
  workspace: string; sequence: number; state: IntentState; version: number;
  data?: unknown; receipt?: unknown; claim?: { owner: string; token: string; expiresAt: number };
};
export type TeachingFact = { key: string; cardId: string; revision: number; ply?: number; kind: "seen" | "first-clean" };
export type IntentPutOptions = { initialState?: "pending" | "conflicted"; boundary?: { key: string; value: number } };
export type IntentChange = { intents?: boolean; metadata?: boolean; teachingKeys?: string[]; invalidated?: boolean };
export type IntentStore = {
  put(envelope: IntentEnvelope, data?: unknown, options?: IntentPutOptions): Promise<IntentRecord>;
  get(key: string): Promise<IntentRecord | undefined>;
  list(kind?: string, activeOnly?: boolean): Promise<IntentRecord[]>;
  update(key: string, change: (record: IntentRecord) => Pick<IntentRecord, "state" | "data" | "receipt">): Promise<IntentRecord>;
  claim(key: string, owner: string, now: number, durationMs: number): Promise<string | undefined>;
  release(key: string, token: string): Promise<void>;
  addTeachingFact(fact: TeachingFact, intent?: IntentEnvelope): Promise<void>;
  teachingFact(key: string): Promise<TeachingFact | undefined>;
  teachingFacts(cardId?: string): Promise<TeachingFact[]>;
  checkpoint(key: string, value?: unknown): Promise<unknown>;
  compareCheckpoint(key: string, expected: unknown, value: unknown): Promise<boolean>;
  boundary(key: string, select: (record: IntentRecord) => boolean, advanceFrom?: number, settled?: (records: IntentRecord[]) => boolean): Promise<{ value: number; records: IntentRecord[] }>;
  subscribe(listener: (change: IntentChange) => void): () => void;
  close(): void;
};

export class IntentStorageError extends Error {
  constructor(message: string, options?: ErrorOptions) { super(message + (options?.cause && typeof options.cause === "object" && "message" in options.cause ? ` (${String("name" in options.cause ? options.cause.name : "StorageError")}: ${String(options.cause.message)})` : ""), options); this.name = "IntentStorageError"; }
}
export class IntentIdentityConflict extends Error {
  constructor(readonly key: string) { super("This study request already has a different immutable payload."); this.name = "IntentIdentityConflict"; }
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  return "{" + Object.keys(value).sort().filter(key => Reflect.get(value, key) !== undefined)
    .map(key => JSON.stringify(key) + ":" + canonical(Reflect.get(value, key))).join(",") + "}";
}
function identity(record: IntentEnvelope): string {
  return canonical({ key: record.key, kind: record.kind, attemptId: record.attemptId, requestId: record.requestId,
    endpoint: record.endpoint, revision: record.revision, createdAt: record.createdAt, payload: record.payload });
}
function request<T>(operation: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => { operation.onsuccess = () => resolve(operation.result); operation.onerror = () => reject(operation.error); });
}

export async function openIntentStore(options: { workspace: string; databaseName?: string; indexedDB?: IDBFactory }): Promise<IntentStore> {
  if (!options.workspace) throw new IntentStorageError("Study storage needs a workspace identity.");
  const factory = options.indexedDB ?? globalThis.indexedDB;
  if (!factory) throw new IntentStorageError("Durable study storage is unavailable. Keep this page open and restore browser storage.");
  const database = await new Promise<IDBDatabase>((resolve, reject) => {
    let rejected = false;
    let opening: IDBOpenDBRequest;
    try { opening = factory.open(options.databaseName ?? "tempo-study-intents", 1); }
    catch (cause) { reject(new IntentStorageError("Could not open durable study storage.", { cause })); return; }
    opening.onupgradeneeded = () => {
      const records = opening.result.createObjectStore("intents", { keyPath: ["workspace", "key"] });
      records.createIndex("workspace", "workspace");
      records.createIndex("state", ["workspace", "state"]);
      const facts = opening.result.createObjectStore("teaching", { keyPath: ["workspace", "key"] });
      facts.createIndex("workspace", "workspace");
      facts.createIndex("card", ["workspace", "cardId"]);
      opening.result.createObjectStore("metadata", { keyPath: ["workspace", "key"] });
    };
    opening.onsuccess = () => { if (rejected) opening.result.close(); else resolve(opening.result); };
    opening.onerror = () => reject(new IntentStorageError("Could not open durable study storage.", { cause: opening.error }));
    opening.onblocked = () => { rejected = true; reject(new IntentStorageError("Close older Tempo tabs and reopen to update study storage.")); };
  });
  const listeners = new Set<(change: IntentChange) => void>();
  const notify = (change: IntentChange) => { for (const listener of listeners) {
    try { listener(change); } catch { /* Subscriber failures cannot undo a committed transaction. */ }
  } };
  let closed = false;
  database.onversionchange = () => { closed = true; database.close(); notify({ invalidated: true }); };
  const channel = typeof BroadcastChannel === "function" ? new BroadcastChannel(`tempo-study-intents:${options.workspace}`) : undefined;
  channel?.addEventListener("message", event => {
    const change = event.data as IntentChange;
    if (change && typeof change === "object" && (!change.teachingKeys || (Array.isArray(change.teachingKeys) && change.teachingKeys.every(key => typeof key === "string")))) notify(change);
  });
  const changed = (change: IntentChange) => {
    if (closed) return;
    notify(change);
    try { channel?.postMessage(change); } catch { /* Notifications are advisory; storage/receipts retain authority. */ }
  };
  async function transaction<T>(names: string[], mode: IDBTransactionMode, action: (transaction: IDBTransaction) => Promise<T>, change: IntentChange = { intents: names.includes("intents"), metadata: names.includes("metadata") }): Promise<T> {
    if (closed) throw new IntentStorageError("Study storage changed in another tab. Reopen Tempo before continuing.");
    return new Promise<T>((resolve, reject) => {
      let operation: IDBTransaction;
      try { operation = database.transaction(names, mode); }
      catch (cause) { reject(new IntentStorageError("Could not access durable study storage.", { cause })); return; }
      let result: T;
      let failure: unknown;
      operation.oncomplete = () => { if (mode === "readwrite") changed(change); resolve(result); };
      // A request error bubbles before the transaction has its final abort/cause.
      operation.onerror = () => { /* Wait for abort; request errors are not a commit result. */ };
      operation.onabort = () => reject(failure instanceof IntentIdentityConflict ? failure :
        new IntentStorageError("The browser could not commit study data. Keep this page open and restore storage.", { cause: failure ?? operation.error }));
      void action(operation).then(value => { result = value; }, cause => {
        failure = cause;
        try { operation.abort(); } catch { reject(cause); }
      });
    });
  }
  const key = (recordKey: string): IDBValidKey => [options.workspace, recordKey];
  const get = (recordKey: string) => transaction(["intents"], "readonly", tx => request<IntentRecord | undefined>(tx.objectStore("intents").get(key(recordKey))));
  return {
    async put(envelope, data, insertion = {}) {
      // Snapshot before the first asynchronous turn; callers cannot mutate an in-flight insertion.
      envelope = structuredClone(envelope); data = structuredClone(data); insertion = structuredClone(insertion);
      if (![envelope.key, envelope.kind, envelope.attemptId, envelope.requestId, envelope.endpoint, envelope.createdAt].every(value => typeof value === "string" && value.length))
        throw new IntentStorageError("The study request has an incomplete identity.");
      if (insertion.initialState !== undefined && !["pending", "conflicted"].includes(insertion.initialState)) throw new IntentStorageError("Invalid initial study state.");
      return transaction(["intents", "metadata"], "readwrite", async tx => {
        const records = tx.objectStore("intents");
        const previous = await request<IntentRecord | undefined>(records.get(key(envelope.key)));
        if (previous) { if (identity(previous) !== identity(envelope)) throw new IntentIdentityConflict(envelope.key); return previous; }
        const metadata = tx.objectStore("metadata");
        if (insertion.boundary) {
          const boundary = await request<{ value: number } | undefined>(metadata.get(key(insertion.boundary.key)));
          if ((boundary?.value ?? 0) !== insertion.boundary.value) throw new IntentStorageError("Practice changed in another tab. Reload this exercise before answering.");
        }
        const counter = await request<{ value: number } | undefined>(metadata.get(key("sequence")));
        const sequence = (counter?.value ?? 0) + 1;
        await request(metadata.put({ workspace: options.workspace, key: "sequence", value: sequence }));
        const record: IntentRecord = { ...envelope, workspace: options.workspace, sequence, state: insertion.initialState ?? "pending", version: 1, data };
        await request(records.add(record));
        return record;
      });
    },
    get,
    list: (kind, activeOnly) => transaction(["intents"], "readonly", async tx => {
      const source = tx.objectStore("intents");
      const records = activeOnly ? (await Promise.all(["pending", "conflicted"].map(state => request<IntentRecord[]>(source.index("state").getAll([options.workspace, state]))))).flat()
        : await request<IntentRecord[]>(source.index("workspace").getAll(options.workspace));
      return records.filter(record => !kind || record.kind === kind).sort((left, right) => left.sequence - right.sequence);
    }),
    update: (recordKey, change) => transaction(["intents"], "readwrite", async tx => {
      const records = tx.objectStore("intents");
      const current = await request<IntentRecord | undefined>(records.get(key(recordKey)));
      if (!current) throw new IntentStorageError("The original study request is unavailable. Preserve this browser's data.");
      const update = change(structuredClone(current));
      if ((current.state === "committed" || current.state === "discarded") && update.state !== current.state)
        throw new IntentStorageError("A settled study request cannot be made pending again.");
      const next = { ...current, state: update.state, data: update.data, receipt: update.receipt, version: current.version + 1 };
      await request(records.put(next)); return next;
    }),
    claim: (recordKey, owner, now, durationMs) => transaction(["intents"], "readwrite", async tx => {
      if (!owner || !Number.isFinite(now) || !Number.isFinite(durationMs) || durationMs <= 0) throw new IntentStorageError("Invalid study delivery claim.");
      const records = tx.objectStore("intents");
      const current = await request<IntentRecord | undefined>(records.get(key(recordKey)));
      if (!current || current.state !== "pending" || (current.claim && current.claim.expiresAt > now)) return undefined;
      const token = crypto.randomUUID();
      await request(records.put({ ...current, claim: { owner, token, expiresAt: now + durationMs }, version: current.version + 1 }));
      return token;
    }),
    release: (recordKey, token) => transaction(["intents"], "readwrite", async tx => {
      const records = tx.objectStore("intents");
      const current = await request<IntentRecord | undefined>(records.get(key(recordKey)));
      if (current?.claim?.token === token) { delete current.claim; await request(records.put({ ...current, version: current.version + 1 })); }
    }),
    addTeachingFact: (fact, intent) => {
      fact = structuredClone(fact); intent = structuredClone(intent);
      return transaction(intent ? ["teaching", "intents", "metadata"] : ["teaching"], "readwrite", async tx => {
      if (!fact.cardId || !fact.key || !Number.isSafeInteger(fact.revision) || fact.revision < 1 ||
          (fact.ply !== undefined && (!Number.isSafeInteger(fact.ply) || fact.ply < 0))) throw new IntentStorageError("Invalid teaching fact.");
      const facts = tx.objectStore("teaching");
      if (!await request(facts.get(key(fact.key)))) await request(facts.add({ ...fact, workspace: options.workspace }));
      if (intent) {
        const records = tx.objectStore("intents");
        const previous = await request<IntentRecord | undefined>(records.get(key(intent.key)));
        if (previous && identity(previous) !== identity(intent)) throw new IntentIdentityConflict(intent.key);
        if (!previous) {
          const metadata = tx.objectStore("metadata");
          const counter = await request<{ value: number } | undefined>(metadata.get(key("sequence")));
          const sequence = (counter?.value ?? 0) + 1;
          await request(metadata.put({ workspace: options.workspace, key: "sequence", value: sequence }));
          await request(records.add({ ...intent, workspace: options.workspace, sequence, version: 1, state: "pending" }));
        }
      }
      }, { teachingKeys: [fact.key], intents: Boolean(intent) });
    },
    teachingFact: factKey => transaction(["teaching"], "readonly", tx => request<TeachingFact | undefined>(tx.objectStore("teaching").get(key(factKey)))),
    teachingFacts: cardId => transaction(["teaching"], "readonly", async tx => {
      const source = tx.objectStore("teaching");
      const facts = await request<TeachingFact[]>(cardId ? source.index("card").getAll([options.workspace, cardId]) : source.index("workspace").getAll(options.workspace));
      return facts.filter(fact => !cardId || fact.cardId === cardId);
    }),
    checkpoint: (recordKey, value) => transaction(["metadata"], value === undefined ? "readonly" : "readwrite", async tx => {
      const metadata = tx.objectStore("metadata");
      if (value !== undefined) await request(metadata.put({ workspace: options.workspace, key: recordKey, value }));
      return (await request<{ value: unknown } | undefined>(metadata.get(key(recordKey))))?.value;
    }),
    compareCheckpoint: (recordKey, expected, value) => transaction(["metadata"], "readwrite", async tx => {
      const metadata = tx.objectStore("metadata");
      const previous = (await request<{ value: unknown } | undefined>(metadata.get(key(recordKey))))?.value;
      if (canonical(previous) !== canonical(expected)) return false;
      await request(metadata.put({ workspace: options.workspace, key: recordKey, value })); return true;
    }),
    boundary: (recordKey, select, advanceFrom, settled) => transaction(["intents", "metadata"], advanceFrom === undefined ? "readonly" : "readwrite", async tx => {
      const metadata = tx.objectStore("metadata");
      const value = Number((await request<{ value: number } | undefined>(metadata.get(key(recordKey))))?.value ?? 0);
      const records = (await request<IntentRecord[]>(tx.objectStore("intents").index("workspace").getAll(options.workspace)))
        .filter(select).sort((left, right) => left.sequence - right.sequence);
      if (advanceFrom === undefined) return { value, records };
      if (value !== advanceFrom) throw new IntentStorageError("Practice changed in another tab. Reload this exercise before answering.");
      if (records.some(record => record.state === "pending" || record.state === "conflicted") || (settled && !settled(records))) throw new IntentStorageError("Confirm the saved answer before starting another practice attempt.");
      const next = Math.max(value, ...records.map(record => record.sequence));
      await request(metadata.put({ workspace: options.workspace, key: recordKey, value: next }));
      return { value: next, records };
    }),
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    close() { closed = true; database.close(); channel?.close(); listeners.clear(); },
  };
}
