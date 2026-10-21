const PENDING_KEY = "tempo-pending-tactic-capture-v1";

export function readPendingTacticCapture(): string | null {
  return localStorage.getItem(PENDING_KEY);
}

export function storePendingTacticCapture(requestBody: string): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify({ body: requestBody }));
}

export function clearPendingTacticCapture(): void {
  localStorage.removeItem(PENDING_KEY);
}
