import { createHash } from "node:crypto";

// One bounded event per delivery. Never log journal options or HTTP error text.
export function startPositionClaimDelivery(path, operationId) {
  if (process.env.TEMPO_POSITION_CLAIM_DIAGNOSTICS !== "1" ||
      path !== "/api/games/analysis/position/claim") return () => {};
  let started, operationDigest;
  try {
    started = performance.now();
    operationDigest = createHash("sha256").update(operationId).digest("hex").slice(0, 24);
  } catch { return () => {}; }
  return (outcome) => {
    try {
      console.info(JSON.stringify({ event: "position_claim", role: "engine", operation_digest: operationDigest,
        stage: "journal_delivery", outcome, duration_ms: Math.max(0, performance.now() - started) }));
    } catch { /* Diagnostics cannot change delivery or journal recovery. */ }
  };
}
