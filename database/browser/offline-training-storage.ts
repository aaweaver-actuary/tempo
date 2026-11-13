/** Connections are keyed by the exact authority-derived locator. */
import { defaultStudyClient, studyOfflineDatabaseName } from "../../app/lib/study-connection";
export const offlineTrainingDatabaseName = "tempo-offline-training";
export const offlineTrainingDatabaseVersion = 2;
export type OfflineDatabaseBinding = { locator: string; requireCurrent: () => void; sameAuthority?: () => boolean };
const databases = new Map<string, Promise<IDBDatabase>>();

export function captureOfflineDatabaseBinding(): OfflineDatabaseBinding {
  const locator = studyOfflineDatabaseName(), authority = defaultStudyClient.studyWorkspace(),
    origin = defaultStudyClient.origin, generation = defaultStudyClient.generation;
  const sameAuthority = () => {
    try { return defaultStudyClient.origin === origin && defaultStudyClient.studyWorkspace() === authority && studyOfflineDatabaseName() === locator; }
    catch { return false; }
  };
  return { locator, sameAuthority, requireCurrent: () => {
    if (defaultStudyClient.origin !== origin || defaultStudyClient.generation !== generation ||
        defaultStudyClient.studyWorkspace() !== authority || studyOfflineDatabaseName() !== locator)
      throw new Error("Study authority changed. Recover the original offline data with its original service.");
  } };
}

export async function offlineTrainingDatabase(binding?: OfflineDatabaseBinding): Promise<IDBDatabase> {
  const explicitBinding = binding !== undefined;
  binding ??= captureOfflineDatabaseBinding();
  binding.requireCurrent();
  const locator = binding.locator;
  let promise = databases.get(locator);
  if (!promise) {
    let opened!: Promise<IDBDatabase>;
    const evict = () => { if (databases.get(locator) === opened) databases.delete(locator); };
    opened = new Promise<IDBDatabase>((resolve, reject) => {
      let blocked = false;
      const request = indexedDB.open(locator, offlineTrainingDatabaseVersion);
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
        database.onversionchange = () => { database.close(); evict(); };
        resolve(database);
      };
      request.onerror = () => { evict(); reject(request.error); };
      request.onblocked = () => { blocked = true; evict(); reject(new Error("Close older Tempo tabs to upgrade offline evidence storage.")); };
    }).catch(error => { evict(); throw error; });
    databases.set(locator, opened); promise = opened;
  }
  const database = await promise;
  binding?.requireCurrent();
  if ((explicitBinding || database.name !== undefined) && database.name !== locator) throw new Error("The original offline storage locator changed.");
  return database;
}
