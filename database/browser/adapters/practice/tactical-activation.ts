/** Existing durable browser persistence; application decisions stay with callers. */
export function readPendingPackActivation(key: string) { return localStorage.getItem(key); }
export function writePendingPackActivation(key: string, operationId: string) { localStorage.setItem(key, operationId); }
export function removePendingPackActivation(key: string) { localStorage.removeItem(key); }
