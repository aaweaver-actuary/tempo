const pendingKey = (repertoireId: string) => `tempo-pending-repertoire-settings-v1:${repertoireId}`;

export function readPendingRepertoireSettingsBytes(repertoireId: string): string | null {
  return localStorage.getItem(pendingKey(repertoireId));
}

export function decodePendingRepertoireSettingsRecord(stored: string): unknown {
  return JSON.parse(stored);
}

export function writePendingRepertoireSettingsRecord(repertoireId: string, record: { operationId: string; body: string }): void {
  localStorage.setItem(pendingKey(repertoireId), JSON.stringify(record));
}

export function clearPendingRepertoireSettingsRecord(repertoireId: string): void {
  localStorage.removeItem(pendingKey(repertoireId));
}
