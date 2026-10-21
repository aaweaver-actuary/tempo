export function readPendingGameExclusion(storageKey: string): string | null {
  return localStorage.getItem(storageKey);
}

export function storePendingGameExclusion(storageKey: string, pending: object): void {
  localStorage.setItem(storageKey, JSON.stringify(pending));
}

export function clearPendingGameExclusion(storageKey: string): void {
  localStorage.removeItem(storageKey);
}
