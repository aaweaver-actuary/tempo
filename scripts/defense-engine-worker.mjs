import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import StockfishFactory from "../public/engines/sf_19_smallnet.js";

const api = process.env.TEMPO_API_URL ?? "http://api:8000";
const assetDirectory = resolve(import.meta.dirname, "../public/engines");
const engine = await StockfishFactory({
  locateFile: (name) => resolve(assetDirectory, name),
  listen: () => {},
});
let fatalEngineError = false;
engine.setNnueBuffer(new Uint8Array(await readFile(resolve(assetDirectory, "nn-61e7af4bb97d.nnue"))));
engine.uci("uci");
engine.uci("setoption name Threads value 1");
engine.uci("setoption name Hash value 32");
engine.uci("isready");

const sleep = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, {
    ...options,
    headers: { "X-Tempo-Work-Class": "background", "X-Tempo-Engine-Worker": "docker", "Content-Type": "application/json" },
  });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
  return response.json();
}

function evaluate(job) {
  return new Promise((resolveReport, rejectReport) => {
    const { request: specification } = job;
    const lines = new Map();
    let finished = false;
    let preempted = false;
    const position = specification.position_prefix_uci ?? [];
    const whiteTurn = (specification.position_start_fen.split(" ")[1] === "w") === (position.length % 2 === 0);
    const whiteSign = whiteTurn ? 1 : -1;
    const timeout = setTimeout(() => {
      preempted = true;
      engine.uci("stop");
    }, 55_000);
    const foregroundPoll = setInterval(async () => {
      if (finished || preempted) return;
      try {
        const { active } = await request("/api/system/foreground-active");
        if (active) {
          preempted = true;
          engine.uci("stop");
        }
      } catch {
        preempted = true;
        engine.uci("stop");
      }
    }, 750);
    engine.listen = (text) => {
      if (text.startsWith("info ") && text.includes(" pv ") && !preempted) {
        const rank = Number(text.match(/ multipv (\d+)/)?.[1] ?? 1);
        const root = text.match(/ pv ([a-h][1-8][a-h][1-8][qrbn]?)/)?.[1];
        const depth = Number(text.match(/ depth (\d+)/)?.[1] ?? 0);
        const centipawns = text.match(/ score cp (-?\d+)/)?.[1];
        const mate = text.match(/ score mate (-?\d+)/)?.[1];
        if (root && (centipawns !== undefined || mate !== undefined)) {
          lines.set(rank, {
            root_move_uci: root,
            pv_uci: text.split(" pv ")[1].trim().split(/\s+/),
            score: centipawns === undefined
              ? { cp: null, mate: Number(mate) * whiteSign }
              : { cp: Number(centipawns) * whiteSign, mate: null },
            depth,
          });
        }
      }
      if (text.startsWith("bestmove ")) {
        finished = true;
        clearTimeout(timeout);
        clearInterval(foregroundPoll);
        if (preempted) return rejectReport(new Error("preempted"));
        const completeLines = [...lines.entries()]
          .sort(([left], [right]) => left - right)
          .map(([, line]) => line)
          .filter((line) => line.depth >= specification.depth);
        if (!completeLines.length) return rejectReport(new Error("Engine did not reach the requested depth"));
        resolveReport({ request: specification, complete: true, lines: completeLines });
      }
    };
    engine.onError = (message) => {
      if (!finished) {
        fatalEngineError = true;
        finished = true;
        clearTimeout(timeout);
        clearInterval(foregroundPoll);
        rejectReport(new Error(String(message)));
      }
    };
    engine.uci(`setoption name MultiPV value ${Math.max(1, Math.min(5, specification.multipv))}`);
    engine.uci(`position fen ${specification.position_start_fen}${position.length ? ` moves ${position.join(" ")}` : ""}`);
    engine.uci(`go depth ${specification.depth}${specification.root_move_uci ? ` searchmoves ${specification.root_move_uci}` : ""}`);
  });
}

if (process.env.TEMPO_ENGINE_SMOKE === "1") {
  const report = await evaluate({ request: {
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
  try {
    const available = await request("/api/system/foreground-active");
    if (available.active) { await sleep(2_000); continue; }
    job = (await request("/api/defensive-threats/analysis/claim", { method: "POST" })).job;
    if (!job) { await sleep(2_000); continue; }
    const report = await evaluate(job);
    await request(`/api/defensive-threats/analysis/${job.id}/report`, {
      method: "POST", body: JSON.stringify({ lease_id: job.lease_id, report }),
    });
  } catch (error) {
    if (job) {
      const preempted = error.message === "preempted";
      try {
        await request(`/api/defensive-threats/analysis/${job.id}/${preempted ? "release" : "failure"}`, {
          method: "POST", body: JSON.stringify(preempted
            ? { lease_id: job.lease_id }
            : { lease_id: job.lease_id, error: error.message.slice(0, 1000) }),
        });
      } catch (reportingError) { console.error("Could not update engine request:", reportingError); }
    } else console.error("Could not claim engine request:", error);
    if (fatalEngineError) process.exit(1);
    await sleep(2_000);
  }
}
