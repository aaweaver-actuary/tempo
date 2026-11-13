const PENDING_KEY = "tempo-pending-prefix-split-v1";

export function readPendingPrefixSplitRecord(): unknown {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingPrefixSplitRecord(record: { operationId: string; decision: "accept" | "reject"; cardId: string; revision: number; queueEntryId?: number; intentId?: string }): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(record));
}

export function clearPendingPrefixSplitRecord(): void {
  localStorage.removeItem(PENDING_KEY);
}

export function clearPendingPrefixSplitIfMatches(operationId: string, intentId?: string): boolean {
  const raw = localStorage.getItem(PENDING_KEY);
  if (!raw) return false;
  const current: unknown = JSON.parse(raw);
  if (!current || typeof current !== "object" || !("operationId" in current) ||
      current.operationId !== operationId || ("intentId" in current ? current.intentId : undefined) !== intentId) return false;
  localStorage.removeItem(PENDING_KEY);
  return true;
}

export function attachPrefixSplitIntentIfMatches(operationId: string, intentId: string): boolean {
  const raw = localStorage.getItem(PENDING_KEY);
  if (!raw) return false;
  const current: unknown = JSON.parse(raw);
  if (!current || typeof current !== "object" || !("operationId" in current) || current.operationId !== operationId ||
      ("intentId" in current && current.intentId !== undefined && current.intentId !== intentId)) return false;
  localStorage.setItem(PENDING_KEY, JSON.stringify({ ...current, intentId }));
  return true;
}
