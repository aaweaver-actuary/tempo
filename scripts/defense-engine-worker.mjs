import { createEngineSearch, admitEngineJob } from "./engine-search.mjs";
import { engineWaitingStage } from "./engine-attempt-diagnostics.mjs";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import StockfishFactory from "../public/engines/sf_19_smallnet.js";
import { createDurableEngineRequest, migrateLegacyDefenseClaimJournal } from "./durable-engine-request.mjs";
import { recoverNextEngineJob } from "./engine-job-recovery.mjs";

const api = process.env.TEMPO_API_URL ?? "http://api:8000";
const journalPath = process.env.TEMPO_ENGINE_OUTBOX_PATH ?? "/tmp/tempo-engine-pending-command.json";
await migrateLegacyDefenseClaimJournal(journalPath);
const durableRequest = createDurableEngineRequest(api, journalPath);
const defenseClaimRequest = createDurableEngineRequest(api, `${journalPath}.defense`);
const assetDirectory = resolve(import.meta.dirname, "../public/engines");
const engine = await StockfishFactory({
  locateFile: (name) => resolve(assetDirectory, name),
  listen: () => {},
});
engine.setNnueBuffer(new Uint8Array(await readFile(resolve(assetDirectory, "nn-61e7af4bb97d.nnue"))));
engine.uci("uci");
engine.uci("setoption name Threads value 1");
engine.uci("setoption name Hash value 32");
engine.uci("isready");

const sleep = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));

async function request(path, options = {}) {
  const journal = path === "/api/defensive-threats/analysis/claim"
    ? defenseClaimRequest : durableRequest;
  return journal.send(path, {
    ...options,
    ...(path === "/api/defensive-threats/analysis/claim" ? { pollAttempts: 20 } : {}),
    headers: { ...options.headers, "X-Tempo-Engine-Worker": "docker" },
  });
}

const searches = createEngineSearch(engine, request);
const evaluate = searches.evaluate;

if (process.env.TEMPO_ENGINE_SMOKE === "1") {
  const { report } = await evaluate({ request: {
    position_start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    position_prefix_uci: [], root_move_uci: "a2a3", multipv: 1, depth: 4,
    engine_version: "Stockfish 19 WASM", network_version: "nn-61e7af4bb97d.nnue",
  } });
  if (report.lines[0]?.root_move_uci !== "a2a3") throw new Error("Restricted search returned the wrong root");
  console.log("Restricted Stockfish search passed");
  process.exit(0);
}

while (true) {
  let job;
  let jobKind;
  try {
    const recovered = await recoverNextEngineJob(durableRequest, defenseClaimRequest);
    ({ job, jobKind } = recovered);
    let { defenseClaimUnresolved } = recovered;
    if (!job) {
      const available = await request("/api/system/foreground-active");
      if (available.active) { engineWaitingStage("engine", "foreground_admission"); await sleep(2_000); continue; }
      if (!defenseClaimUnresolved) {
        try {
          job = (await request("/api/defensive-threats/analysis/claim", { method: "POST" })).job;
        } catch (error) {
          if (!error.operationId) throw error;
          defenseClaimUnresolved = true;
          console.error("Defensive claim remains unresolved:", error.operationId, error.message);
        }
      }
      if (job) jobKind = "defense";
      else {
        await request("/api/games/analysis/repair-timeout", { method: "POST" });
        await request("/api/games/analysis/repair-provenance", { method: "POST" });
        job = (await request("/api/games/analysis/position/claim", { method: "POST" })).job;
        jobKind = "game";
      }
    }
    if (!job) { engineWaitingStage("engine", "idle"); await sleep(2_000); continue; }
    if (job.kind === "finalize") {
      await request("/api/games/analysis/position/finalize", {
        method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
      });
      job = undefined;
      continue;
    }
    if (!await admitEngineJob(job, jobKind === "defense" ? "engine_defense" : "engine_game", request)) {
      job = undefined;
      continue;
    }
    const { report, diagnostics } = await evaluate(job, jobKind === "defense" ? "engine_defense" : "engine_game");
    await request(jobKind === "defense"
      ? `/api/defensive-threats/analysis/${job.id}/report`
      : `/api/games/analysis/position/${job.id}/report`, {
      method: "POST", body: JSON.stringify({ lease_id: job.lease_id, report, diagnostics }),
    });
  } catch (error) {
    if (error.operationId) {
      console.error("Engine database command is still pending:", error.operationId);
      await sleep(2_000);
      continue;
    }
    if (job) {
      const preempted = error.message === "preempted";
      try {
        if (job.kind === "finalize") {
          await request(`/api/games/analysis/${encodeURIComponent(job.game_id)}/failure`, {
            method: "POST", body: JSON.stringify({ lease_id: job.lease_id,
              error: `Game finalization failed: ${error.message.slice(0, 900)}` }),
          });
          job = undefined;
          continue;
        }
        const prefix = jobKind === "defense" ? "/api/defensive-threats/analysis" : "/api/games/analysis/position";
        await request(`${prefix}/${job.id}/${preempted ? "release" : "failure"}`, {
          method: "POST", body: JSON.stringify(preempted
            ? { lease_id: job.lease_id, diagnostics: error.diagnostics }
            : { lease_id: job.lease_id, error: error.message.slice(0, 1000), diagnostics: error.diagnostics }),
        });
      } catch (reportingError) { console.error("Could not update engine request:", reportingError); }
    } else console.error("Could not claim engine request:", error);
    if (searches.fatalEngineError) process.exit(1);
    await sleep(2_000);
  }
}
