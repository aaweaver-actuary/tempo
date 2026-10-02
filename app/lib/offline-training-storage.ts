/** One connection/version for prepared queues and incremental shadow journals. */
export const offlineTrainingDatabaseName = "tempo-offline-training";
export const offlineTrainingDatabaseVersion = 2;
let databasePromise: Promise<IDBDatabase> | undefined;

export function offlineTrainingDatabase(): Promise<IDBDatabase> {
  databasePromise ??= new Promise<IDBDatabase>((resolve, reject) => {
    let blocked = false;
    const request = indexedDB.open(offlineTrainingDatabaseName, offlineTrainingDatabaseVersion);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("training")) database.createObjectStore("training");
      if (!database.objectStoreNames.contains("opening_attempts")) {
        const attempts = database.createObjectStore("opening_attempts", { keyPath: "attempt_id" });
        attempts.createIndex("delivery_state", "delivery_state");
      }
      if (!database.objectStoreNames.contains("opening_events")) {
        const events = database.createObjectStore("opening_events", { keyPath: ["attempt_id", "sequence"] });
        events.createIndex("attempt_id", "attempt_id");
      }
    };
    request.onsuccess = () => {
      const database = request.result;
      if (blocked) { database.close(); return; }
      database.onversionchange = () => { database.close(); databasePromise = undefined; };
      resolve(database);
    };
    request.onerror = () => { databasePromise = undefined; reject(request.error); };
    request.onblocked = () => { blocked = true; databasePromise = undefined; reject(new Error("Close older Tempo tabs to upgrade offline evidence storage.")); };
  }).catch((error) => { databasePromise = undefined; throw error; });
  return databasePromise;
}
