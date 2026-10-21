const PENDING_PREFIX = "tempo-pending-opportunity-v1:";

export function readPendingOpportunityRecord(opportunityId: string): unknown {
  const stored = localStorage.getItem(`${PENDING_PREFIX}${opportunityId}`);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingOpportunityRecord(opportunityId: string, record: { operationId: string; repertoireId: string; action: "dismiss" | "acknowledge" | "snooze" | "train"; evidenceFingerprint?: string }): void {
  localStorage.setItem(`${PENDING_PREFIX}${opportunityId}`, JSON.stringify(record));
}

export function clearPendingOpportunityRecord(opportunityId: string): void {
  localStorage.removeItem(`${PENDING_PREFIX}${opportunityId}`);
}
