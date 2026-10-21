/** Prepared queue IndexedDB reads and atomic updates; application callbacks own policy. */
import { offlineTrainingDatabase as database } from "./offline-training-storage";
import type { PreparedTraining } from "../../app/domain/offline-training";

const RECORD_KEY = "prepared-daily-queue";

export async function readPreparedTraining(): Promise<PreparedTraining | null> {
  if (typeof indexedDB === "undefined") return null;
  const opened = await database();
  return new Promise((resolve, reject) => {
    const request = opened.transaction("training").objectStore("training").get(RECORD_KEY);
    request.onsuccess = () => resolve(request.result ?? null);
    request.onerror = () => reject(request.error);
  });
}

export async function updatePreparedTraining(
  update: (current: PreparedTraining | null) => PreparedTraining,
): Promise<PreparedTraining> {
  const opened = await database();
  return new Promise((resolve, reject) => {
    const transaction = opened.transaction("training", "readwrite");
    const store = transaction.objectStore("training");
    let updated: PreparedTraining;
    const request = store.get(RECORD_KEY);
    request.onsuccess = () => {
      try {
        updated = update(request.result ?? null);
        store.put(updated, RECORD_KEY);
      } catch (error) {
        transaction.abort();
        reject(error);
      }
    };
    transaction.oncomplete = () => resolve(updated);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}
