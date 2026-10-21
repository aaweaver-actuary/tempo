const PENDING_PREFIX = "tempo-pending-opportunity-refresh-v1:";

export function readPendingOpportunityRefreshIdentity(repertoireId: string): string | null {
  return localStorage.getItem(`${PENDING_PREFIX}${repertoireId}`);
}

export function writePendingOpportunityRefreshIdentity(repertoireId: string, operationId: string): void {
  localStorage.setItem(`${PENDING_PREFIX}${repertoireId}`, operationId);
}

export function clearPendingOpportunityRefreshIdentity(repertoireId: string): void {
  localStorage.removeItem(`${PENDING_PREFIX}${repertoireId}`);
}
