import { createEngineSearch } from "./engine-search.mjs";
import { createStockfishProcess } from "./stockfish-process.mjs";
import { createDurableEngineRequest, migrateLegacyDefenseClaimJournal } from "./durable-engine-request.mjs";
import { createEngineFamilyRequest } from "./engine-job-families.mjs";
import { runEngineWorkerCycle } from "./engine-worker-cycle.mjs";

const api = process.env.TEMPO_API_URL ?? "http://api:8000";
const journalPath = process.env.TEMPO_ENGINE_OUTBOX_PATH ?? "/tmp/tempo-engine-pending-command.json";
await migrateLegacyDefenseClaimJournal(journalPath);
const durableRequest = createDurableEngineRequest(api, journalPath);
const defenseClaimRequest = createDurableEngineRequest(api, `${journalPath}.defense`);
const engine = await createStockfishProcess();
engine.uci("uci");
engine.uci("setoption name Threads value 1");
engine.uci("setoption name Hash value 32");
engine.uci("isready");

const sleep = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));

const request = createEngineFamilyRequest(durableRequest, defenseClaimRequest);

const searches = createEngineSearch(engine);
const evaluate = searches.evaluate;

if (process.env.TEMPO_ENGINE_SMOKE === "1") {
  const { report } = await evaluate({ request: {
    position_start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    position_prefix_uci: [], root_move_uci: "a2a3", multipv: 1, depth: 4,
    engine_version: "Stockfish 19 WASM", network_version: "nn-61e7af4bb97d.nnue",
  } }, "engine_defense", async () => true);
  if (report.lines[0]?.root_move_uci !== "a2a3") throw new Error("Restricted search returned the wrong root");
  console.log("Restricted Stockfish search passed");
  process.exit(0);
}

while (true) {
  await runEngineWorkerCycle({ searches, request, durableRequest, defenseClaimRequest, sleep,
    shutdown: () => process.exit(1) });
}
