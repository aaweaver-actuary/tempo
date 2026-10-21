/** Existing durable browser persistence; application decisions stay with callers. */
export function writeTacticProgress(progress: unknown) {
  localStorage.setItem("tempo-tactics-progress-v2", JSON.stringify(progress));
}
export function readTacticProgressMigration() { return localStorage.getItem("tempo-tactic-progress-pack-migration-v1"); }
export function markTacticProgressMigrated() { localStorage.setItem("tempo-tactic-progress-pack-migration-v1", "true"); }
