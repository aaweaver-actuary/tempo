import { createEngineSearch } from "./engine-search.mjs";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import StockfishFactory from "../public/engines/sf_19_smallnet.js";
import { createDurableEngineRequest, migrateLegacyDefenseClaimJournal } from "./durable-engine-request.mjs";
import { runEngineWorkerCycle } from "./engine-worker-cycle.mjs";

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

async function request(path, options = {}, deliveryOptions = {}) {
  const journal = path === "/api/defensive-threats/analysis/claim"
    ? defenseClaimRequest : durableRequest;
  return journal.send(path, {
    ...options,
    ...(path === "/api/defensive-threats/analysis/claim" ? { pollAttempts: 20 } : {}),
    headers: { ...options.headers, "X-Tempo-Engine-Worker": "docker" },
  }, deliveryOptions);
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
  await runEngineWorkerCycle({ searches, request, durableRequest, defenseClaimRequest, sleep,
    shutdown: () => process.exit(1) });
}
