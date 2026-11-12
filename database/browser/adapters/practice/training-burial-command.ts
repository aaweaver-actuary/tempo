/** Existing durable browser persistence; application decisions stay with callers. */
const operationKey = (id: number) => `tempo-bury-operation-${id}`;
const identityKey = (id: number) => `tempo-bury-identity-${id}`;
export function readBurialOperation(queueEntryId: number) { return localStorage.getItem(operationKey(queueEntryId)); }
export function writeBurialOperation(queueEntryId: number, operationId: string) { localStorage.setItem(operationKey(queueEntryId), operationId); }
export function removeBurialOperation(queueEntryId: number) { localStorage.removeItem(operationKey(queueEntryId)); }
export function readBurialIdentity(queueEntryId: number) { return localStorage.getItem(identityKey(queueEntryId)); }
export function writeBurialIdentity(queueEntryId: number, requestBody: string) { localStorage.setItem(identityKey(queueEntryId), requestBody); }
export function removeBurialIdentity(queueEntryId: number) { localStorage.removeItem(identityKey(queueEntryId)); }
export function readPendingBurialEntry() { return localStorage.getItem("tempo-pending-burial-entry"); }
export function writePendingBurialEntry(queueEntryId: number) { localStorage.setItem("tempo-pending-burial-entry", String(queueEntryId)); }
export function removePendingBurialEntry() { localStorage.removeItem("tempo-pending-burial-entry"); }
export function readKnownBurialOperation(queueEntryId: number) { return localStorage.getItem(operationKey(queueEntryId)); }
