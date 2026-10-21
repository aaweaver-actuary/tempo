export function readPendingGuidedReviewStart(storageKey: string): string | null {
  return localStorage.getItem(storageKey);
}

export function storePendingGuidedReviewStart(storageKey: string, operationId: string): void {
  localStorage.setItem(storageKey, operationId);
}

export function clearPendingGuidedReviewStart(storageKey: string): void {
  localStorage.removeItem(storageKey);
}

export function readPendingGuidedReviewAttempt(storageKey: string): string | null {
  return localStorage.getItem(storageKey);
}

export function storePendingGuidedReviewAttempt(storageKey: string, pending: object): void {
  localStorage.setItem(storageKey, JSON.stringify(pending));
}

export function clearPendingGuidedReviewAttempt(storageKey: string): void {
  localStorage.removeItem(storageKey);
}
