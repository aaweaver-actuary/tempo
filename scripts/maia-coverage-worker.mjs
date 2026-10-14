import { analyzeMaiaPosition } from "./maia-inference.mjs";
import { PendingMaiaCommandError, requestMaiaApi } from "./maia-coverage-client.mjs";
const apiUrl = process.env.TEMPO_API_URL ?? "http://api:8000";
const sleep = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));
async function request(path, options = {}) {
  return requestMaiaApi(apiUrl, path, options);
}

if (process.env.TEMPO_MAIA_SMOKE === "1") {
  const fen = process.env.TEMPO_MAIA_FIXTURE_FEN ?? "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const moves = await analyzeMaiaPosition(fen, 1500);
  if (!moves.length || Math.abs(moves.reduce((sum, move) => sum + move.probability, 0) - 1) > 0.0001)
    throw new Error("Maia probabilities are invalid");
  console.log(JSON.stringify(moves.slice(0, 5)));
  process.exit(0);
}

while (true) {
  let job;
  try {
    const admission = await request("/api/system/foreground-active");
    if (admission.study_only_mode) { await sleep(5_000); continue; }
    if (admission.active) { await sleep(1_000); continue; }
    if (!(await request("/api/repertoire-coverage/maia/available")).available) {
      await sleep(5_000); continue;
    }
    job = (await request("/api/repertoire-coverage/maia/claim", { method: "POST" })).job;
    if (!job) { await sleep(2_000); continue; }
    const heartbeat = setInterval(() => {
      void request("/api/repertoire-coverage/maia/heartbeat", {
        method: "POST", body: JSON.stringify({ node_id: job.node_id, lease_id: job.lease_id }),
      }).catch((error) => console.error("Maia lease heartbeat failed:", error));
    }, 30_000);
    try {
      const moves = await analyzeMaiaPosition(job.fen, job.elo);
      await request("/api/repertoire-coverage/maia/submit", {
        method: "POST", body: JSON.stringify({ node_id: job.node_id, lease_id: job.lease_id, moves }),
      });
      job = undefined;
    } finally { clearInterval(heartbeat); }
  } catch (error) {
    console.error("Maia coverage failed:", error);
    if (job && !(error instanceof PendingMaiaCommandError)) {
      try {
        await request("/api/repertoire-coverage/maia/failure", {
          method: "POST",
          body: JSON.stringify({ node_id: job.node_id, lease_id: job.lease_id, error: String(error).slice(0, 900) }),
        });
      } catch (reportError) { console.error("Could not report Maia failure:", reportError); }
    }
    await sleep(2_000);
  }
}
