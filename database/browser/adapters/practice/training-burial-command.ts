/** Existing durable browser persistence; application decisions stay with callers. */
export function readBurialOperation(key: string) { return localStorage.getItem(key); }
export function writeBurialOperation(key: string, operationId: string) { localStorage.setItem(key, operationId); }
export function removeBurialOperation(queueEntryId: number) { localStorage.removeItem(`tempo-bury-operation-${queueEntryId}`); }
export function readBurialIdentity(key: string) { return localStorage.getItem(key); }
export function writeBurialIdentity(key: string, requestBody: string) { localStorage.setItem(key, requestBody); }
export function removeBurialIdentity(queueEntryId: number) { localStorage.removeItem(`tempo-bury-identity-${queueEntryId}`); }
export function readPendingBurialEntry() { return localStorage.getItem("tempo-pending-burial-entry"); }
export function writePendingBurialEntry(queueEntryId: number) { localStorage.setItem("tempo-pending-burial-entry", String(queueEntryId)); }
export function removePendingBurialEntry() { localStorage.removeItem("tempo-pending-burial-entry"); }
export function readKnownBurialOperation(queueEntryId: number) { return localStorage.getItem(`tempo-bury-operation-${queueEntryId}`); }
