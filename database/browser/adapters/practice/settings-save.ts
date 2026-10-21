/** Existing durable browser persistence; application decisions stay with callers. */
const pendingKey = "tempo-pending-settings-save-v1";
export function readPendingSettingsSave() { return localStorage.getItem(pendingKey); }
export function writePendingSettingsSave(pending: unknown) { localStorage.setItem(pendingKey, JSON.stringify(pending)); }
export function removePendingSettingsSave() { localStorage.removeItem(pendingKey); }
