// Only reject a completed result. A queued/running scan can still contain the
// previous generation's needs_repair status and must be allowed to finish.
export function assertNoCompletedFixtureConflict(integrity, repertoireId) {
  if (integrity.scan_status === "idle" && integrity.status === "needs_repair") {
    const issues = Array.isArray(integrity.issues) ? integrity.issues.slice(0, 5) : [];
    throw new Error(`PostgreSQL fixture ${repertoireId} completed integrity validation with needs_repair; `
      + `fix the PGN fixture or its supported admission path instead of waiting for queue admission. `
      + `issues=${JSON.stringify(issues)}`);
  }
}
