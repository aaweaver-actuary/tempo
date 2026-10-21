/** Authored Studies' existing session-lifetime command retry-key storage. */
export function readPendingStudyCommandKey(commandStorageKey: string): string | null {
  return sessionStorage.getItem(commandStorageKey);
}

export function writePendingStudyCommandKey(commandStorageKey: string, idempotencyKey: string): void {
  sessionStorage.setItem(commandStorageKey, idempotencyKey);
}

export function removePendingStudyCommandKey(commandStorageKey: string): void {
  sessionStorage.removeItem(commandStorageKey);
}
