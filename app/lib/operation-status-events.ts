const listeners = new Set<(operationId: string) => void>();
/** A known nonblocked receipt is a status signal, never a command retry. */
export function subscribeOperationStatusChange(listener: (operationId: string) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function notifyOperationStatusChange(operationId: string): void {
  for (const listener of listeners) {
    try { listener(operationId); } catch { /* Observers cannot change receipt semantics. */ }
  }
}
