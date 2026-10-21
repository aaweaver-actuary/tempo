const storagePrefix = "tempo-pending-integrity-repairs-v3:";
const legacyQueueKey = "tempo-pending-integrity-repairs-v2";
const legacyCommandKey = "tempo-pending-integrity-repair-v1";

export function integrityRepairStorageKey(operationId: string): string {
  return storagePrefix + operationId;
}

export function writeIntegrityRepairRecord(repair: { operationId: string }, validatedRecord: () => object): void {
  // Evaluate validation at the original write-argument point, after resolving Storage.
  localStorage.setItem(storagePrefix + repair.operationId, JSON.stringify(validatedRecord()));
}

export function readLegacyRepairQueueBytes(): string | null {
  return localStorage.getItem(legacyQueueKey);
}

export function readLegacyRepairCommandBytes(): string | null {
  return localStorage.getItem(legacyCommandKey);
}

export function decodeLegacyRepairQueue(stored: string): unknown {
  return JSON.parse(stored);
}

export function decodeLegacyRepairCommand(stored: string): unknown {
  return JSON.parse(stored);
}

export function readIntegrityRepairBytes(operationId: string): string | null {
  return localStorage.getItem(storagePrefix + operationId);
}

export function decodeIntegrityRepairRecord(stored: string): unknown {
  return JSON.parse(stored);
}

export function clearLegacyRepairQueue(): void {
  localStorage.removeItem(legacyQueueKey);
}

export function clearLegacyRepairCommand(): void {
  localStorage.removeItem(legacyCommandKey);
}

export function integrityRepairRecordKeys(): string[] {
  return Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
    .filter((key): key is string => Boolean(key?.startsWith(storagePrefix)));
}

export function readIntegrityRepairRecordForKey(key: string): unknown {
  return JSON.parse(localStorage.getItem(key)!);
}

export function clearIntegrityRepairRecord(operationId: string): void {
  localStorage.removeItem(storagePrefix + operationId);
}
