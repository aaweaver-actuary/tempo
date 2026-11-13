/** Prepared queue IndexedDB reads and atomic updates; application callbacks own policy. */
import { offlineTrainingDatabase as database, captureOfflineDatabaseBinding, type OfflineDatabaseBinding } from "./offline-training-storage";
import type { PreparedTraining } from "../../app/domain/offline-training";

const RECORD_KEY = "prepared-daily-queue";

export async function readPreparedTraining(binding?: OfflineDatabaseBinding): Promise<PreparedTraining | null> {
  if (typeof indexedDB === "undefined") return null;
  binding ??= captureOfflineDatabaseBinding();
  const opened = await database(binding);
  binding?.requireCurrent();
  return new Promise((resolve, reject) => {
    const request = opened.transaction("training").objectStore("training").get(RECORD_KEY);
    request.onsuccess = () => { try { binding?.requireCurrent(); resolve(request.result ?? null); } catch (error) { reject(error); } };
    request.onerror = () => reject(request.error);
  });
}

export async function updatePreparedTraining(
  update: (current: PreparedTraining | null) => PreparedTraining,
  binding?: OfflineDatabaseBinding,
): Promise<PreparedTraining> {
  binding ??= captureOfflineDatabaseBinding();
  const opened = await database(binding);
  binding?.requireCurrent();
  return new Promise((resolve, reject) => {
    const transaction = opened.transaction("training", "readwrite");
    const store = transaction.objectStore("training");
    let updated: PreparedTraining;
    const request = store.get(RECORD_KEY);
    request.onsuccess = () => {
      try {
        binding?.requireCurrent();
        updated = update(request.result ?? null);
        binding?.requireCurrent();
        store.put(updated, RECORD_KEY);
      } catch (error) {
        transaction.abort();
        reject(error);
      }
    };
    transaction.oncomplete = () => { try { binding?.requireCurrent(); resolve(updated); } catch (error) { reject(error); } };
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}
