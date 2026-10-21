export type PendingDiscoveryAdmission = {
  opportunityId: string;
  selectedMoveUci: string;
  evidenceFingerprint: string;
  operationId: string;
  intentId?: string;
  state: "pending" | "accepted" | "failed";
  error?: string;
  retryCount?: number;
  nextAttemptAt?: number;
  confirmationPollOrder?: number;
};
