/** Existing durable browser persistence; application decisions stay with callers. */
const STORAGE_KEY = "tempo-position-annotations";
const PENDING_KEY = "tempo-pending-position-annotation-v1";
export function readPendingPositionAnnotation() { return localStorage.getItem(PENDING_KEY); }
export function writePendingPositionAnnotation(pending: unknown) { localStorage.setItem(PENDING_KEY, JSON.stringify(pending)); }
export function removePendingPositionAnnotation() { localStorage.removeItem(PENDING_KEY); }
export function writePositionAnnotations(remaining: readonly unknown[]) { localStorage.setItem(STORAGE_KEY, JSON.stringify(remaining)); }
