import type { Page } from "@playwright/test";

/** Assert the new owner's durable settlement; retained legacy sources are not a pending-work projection. */
export function connectedReviewState(page: Page, attemptId: string): Promise<string> {
  return page.evaluate(async identity => new Promise<string>((resolve, reject) => {
    const opening = indexedDB.open("tempo-study-intents", 1);
    opening.onerror = () => reject(opening.error);
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
