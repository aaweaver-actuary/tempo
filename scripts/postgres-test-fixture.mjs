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

export const studyDurabilityPgn = [
  '[Event "PostgreSQL recovery durability"]', "",
  "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 d6 5. O-O Be7 6. c3 O-O 7. Re1 a6 8. Bb3 *",
  '[Event "Second deterministic line"]', "",
  "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 6. Be3 e6 7. f3 Be7 8. Qd2 *",
  '[Event "Third deterministic line"]', "",
  "1. e4 e6 2. d4 d5 3. Nc3 Nf6 4. Bg5 Be7 5. e5 Nfd7 6. Bxe7 Qxe7 7. f4 O-O 8. Nf3 *", "",
].join("\n");

export const backgroundPublicationPgn = [
  '[Event "Queued background publication"]', "",
  "1. c4 e5 2. Nc3 Nf6 3. g3 d5 4. cxd5 Nxd5 5. Bg2 *", "",
].join("\n");

export const repertoireLimitRecreationPgn = '[Event "Limit recreation"]\n\n1. e4 e5 2. Nf3 *';
