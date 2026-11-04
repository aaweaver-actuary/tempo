/** Passive, versioned inspection. Original stores remain the durable source of truth. */
export type HeldJournalRecord = {
  database: string;
  store: string;
  primaryKey: IDBValidKey;
  value: unknown;
  disposition: "BLOCK";
  reason: "unconfirmed-authority";
};
export type HeldJournalInspection = {
  version: 1;
  database: string;
  databaseVersion: number;
  records: HeldJournalRecord[];
};

/**
 * Capture keys and structured-clone values in one read transaction. No interpretation,
 * authority binding, or server delivery is implied. The caller owns opening and
 * closing the original database and must retain it after this inspection.
 */
export function inspectHeldJournal(database: IDBDatabase, maxRecords: number): Promise<HeldJournalInspection> {
  if (!Number.isSafeInteger(maxRecords) || maxRecords < 1)
    return Promise.reject(new Error("Journal inspection requires a positive safe integer record budget."));
  const stores = Array.from(database.objectStoreNames);
  const result: HeldJournalInspection = { version: 1, database: database.name, databaseVersion: database.version, records: [] };
  if (!stores.length) return Promise.resolve(result);
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(stores, "readonly");
    let budgetExceeded = false;
    for (const name of stores) {
      const request = transaction.objectStore(name).openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) return;
        if (result.records.length >= maxRecords) { budgetExceeded = true; transaction.abort(); return; }
        result.records.push({ database: database.name, store: name, primaryKey: cursor.primaryKey,
          value: cursor.value, disposition: "BLOCK", reason: "unconfirmed-authority" });
        cursor.continue();
      };
    }
    transaction.oncomplete = () => resolve(result);
    transaction.onabort = () => reject(budgetExceeded
      ? new Error("Journal inspection incomplete: record budget exceeded; keep original browser data.")
      : transaction.error ?? new Error("Journal inspection aborted; keep original browser data."));
    transaction.onerror = () => { /* Transaction abort reports the original storage error. */ };
  });
}
