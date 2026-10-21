/** Existing durable browser persistence; application decisions stay with callers. */
const STORAGE_KEY = "tempo-training-burials";
export function readTrainingBurials() { return localStorage.getItem(STORAGE_KEY); }
export function writeTrainingBurials(day: string, cardIds: Set<string>) { localStorage.setItem(STORAGE_KEY, JSON.stringify({ day, cardIds: [...cardIds] })); }
export function removeTrainingBurials() { localStorage.removeItem(STORAGE_KEY); }
