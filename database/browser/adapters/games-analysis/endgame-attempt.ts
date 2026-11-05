const PENDING_KEY = "tempo-pending-endgame-attempt-v1";

export function readPendingEndgameAttempt(): string | null {
  return localStorage.getItem(PENDING_KEY);
}

export function storePendingEndgameAttempt(pending: object): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
}

export function clearPendingEndgameAttempt(): void {
  localStorage.removeItem(PENDING_KEY);
}
