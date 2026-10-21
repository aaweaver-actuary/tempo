const pendingKey = "tempo-pending-game-sync-command-v1";

export function readPendingGameSync(): string | null {
  return localStorage.getItem(pendingKey);
}

export function storePendingGameSync(pending: object): void {
  localStorage.setItem(pendingKey, JSON.stringify(pending));
}

export function clearPendingGameSync(): void {
  localStorage.removeItem(pendingKey);
}
