type PrefixAction = "preview" | "save";
const storageKey = (repertoireId: string, action: PrefixAction) => `tempo-canonical-prefix-${action}-v1:${repertoireId}`;

export function readPendingCanonicalPrefixRecord(repertoireId: string, action: PrefixAction): unknown {
  const stored = localStorage.getItem(storageKey(repertoireId, action));
  return stored ? JSON.parse(stored) : undefined;
}

export function writePendingCanonicalPrefixRecord(repertoireId: string, action: PrefixAction, record: { operationId: string; body: Record<string, string | number> }): void {
  localStorage.setItem(storageKey(repertoireId, action), JSON.stringify(record));
}

export function clearPendingCanonicalPrefixRecord(repertoireId: string, action: PrefixAction): void {
  localStorage.removeItem(storageKey(repertoireId, action));
}
