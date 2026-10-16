import type { Page } from "@playwright/test";

/** Assert the new owner's durable settlement; retained legacy sources are not a pending-work projection. */
export function connectedReviewState(page: Page, attemptId: string): Promise<string> {
  return page.evaluate(async identity => new Promise<string>((resolve, reject) => {
    const opening = indexedDB.open("tempo-study-intents", 1);
    let absent = false;
    opening.onupgradeneeded = () => { absent = true; opening.transaction!.abort(); };
    opening.onerror = () => absent ? resolve("missing") : reject(opening.error);
    opening.onsuccess = () => {
      const database = opening.result;
      if (!database.objectStoreNames.contains("intents")) { database.close(); resolve("missing"); return; }
      const transaction = database.transaction("intents");
      const request = transaction.objectStore("intents").getAll();
      request.onsuccess = () => resolve((request.result as { key: string; state: string }[]).find(record => record.key === `review:${identity}`)?.state ?? "missing");
      request.onerror = () => reject(request.error);
      transaction.oncomplete = transaction.onabort = () => database.close();
    };
  }), attemptId);
}

export function nativeStudyRecords(page: Page): Promise<Array<{ key: string; kind: string; state: string; requestId: string; payload: unknown; receipt?: unknown }>> {
  return page.evaluate(() => new Promise((resolve, reject) => {
    const opening = indexedDB.open("tempo-study-intents", 1);
    let absent = false;
    opening.onupgradeneeded = () => { absent = true; opening.transaction!.abort(); };
    opening.onerror = () => absent ? resolve([]) : reject(opening.error);
    opening.onsuccess = () => {
      const database = opening.result;
      if (!database.objectStoreNames.contains("intents")) { database.close(); reject(new Error("Incompatible Study intent storage")); return; }
      const transaction = database.transaction("intents");
      const request = transaction.objectStore("intents").getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      transaction.oncomplete = transaction.onabort = () => database.close();
    };
  }));
}
