const PENDING_KEY = "tempo-pending-endgame-template-v1";

export function readPendingEndgameTemplate(): string | null {
  return localStorage.getItem(PENDING_KEY);
}

export function storePendingEndgameTemplate(pending: object): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
}

export function clearPendingEndgameTemplate(): void {
  localStorage.removeItem(PENDING_KEY);
}
