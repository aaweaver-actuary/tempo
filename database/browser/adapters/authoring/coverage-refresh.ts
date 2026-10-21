const PENDING_PREFIX = "tempo-pending-coverage-refresh-v1:";

export function readPendingCoverageRefreshIdentity(repertoireId: string): string | null {
  return localStorage.getItem(`${PENDING_PREFIX}${repertoireId}`);
}

export function writePendingCoverageRefreshIdentity(repertoireId: string, operationId: string): void {
  localStorage.setItem(`${PENDING_PREFIX}${repertoireId}`, operationId);
}

export function clearPendingCoverageRefreshIdentity(repertoireId: string): void {
  localStorage.removeItem(`${PENDING_PREFIX}${repertoireId}`);
}
