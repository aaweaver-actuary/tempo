import { startEngineAttempt, engineWaitingStage } from "./engine-attempt-diagnostics.mjs";

export function engineSearchWasPreempted(error) {
  return error.message === "preempted" || error.diagnostics?.outcome === "preempted";
}

export function engineControlPath(job, kind) {
  return kind === "engine_defense"
    ? `/api/defensive-threats/analysis/${encodeURIComponent(job.id)}/control?lease_id=${encodeURIComponent(job.lease_id)}`
    : "/api/system/foreground-active";
}

export async function engineSearchAllowed(job, kind, request) {
  try {
    const control = await request(engineControlPath(job, kind), { signal: AbortSignal.timeout(1_500) });
    return kind === "engine_defense"
      ? control.search_allowed === true && control.foreground_active === false
      : control.active === false;
  } catch {
    // A control outage cannot authorize continuing a background search.
    return false;
  }
}

export async function admitEngineJob(job, kind, request) {
  if (kind !== "engine_defense" || await engineSearchAllowed(job, kind, request)) return true;
  await request(`/api/defensive-threats/analysis/${encodeURIComponent(job.id)}/release`, {
    method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
  });
  return false;
}

export function createEngineSearch(engine, request) {
  let fatalEngineError = false;
  function evaluate(job, kind = "engine_defense") {
    engineWaitingStage(kind, "execution");
    const finishDiagnostic = startEngineAttempt(kind);
    return new Promise((resolveReport, rejectReport) => {
      const { request: specification } = job;
      const lines = new Map();
      let finished = false;
      let preempted = false;
      let stopReason = "preempted";
      let stopWatchdog;
      const stopSearch = () => {
        if (preempted) return;
        preempted = true;
        stopWatchdog = setTimeout(() => {
          fatalEngineError = true;
          finished = true;
          clearTimeout(timeout);
          clearInterval(foregroundPoll);
          const error = new Error("Stockfish did not drain after cancellation");
          error.diagnostics = finishDiagnostic(stopReason === "preempted" ? "preempted" : "timeout");
          rejectReport(error);
        }, 5_000);
        engine.uci("stop");
      };
      const position = specification.position_prefix_uci ?? [];
      const whiteTurn = (specification.position_start_fen.split(" ")[1] === "w") === (position.length % 2 === 0);
      const whiteSign = whiteTurn ? 1 : -1;
      const timeout = setTimeout(() => {
        stopReason = "Engine timed out before requested depth";
        stopSearch();
      }, 55_000);
      let controlPollPending = false;
      const foregroundPoll = process.env.TEMPO_ENGINE_SMOKE === "1" ? undefined : setInterval(async () => {
        if (finished || preempted || controlPollPending) return;
        controlPollPending = true;
        try {
          const allowed = await engineSearchAllowed(job, kind, request);
          if (!finished && !preempted && !allowed) stopSearch();
        } finally {
          controlPollPending = false;
        }
      }, 750);
      engine.listen = (text) => {
        if (finished) return;
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
          clearTimeout(stopWatchdog);
          if (preempted) {
            const error = new Error(stopReason);
            error.diagnostics = finishDiagnostic(stopReason === "preempted" ? "preempted" : "timeout");
            return rejectReport(error);
          }
          const completeLines = [...lines.entries()]
            .sort(([left], [right]) => left - right)
            .map(([, line]) => line)
            .filter((line) => line.depth >= specification.depth);
          if (!completeLines.length) {
            const error = new Error("Engine did not reach the requested depth");
            error.diagnostics = finishDiagnostic("failure");
            return rejectReport(error);
          }
          resolveReport({ report: { request: specification, complete: true, lines: completeLines }, diagnostics: finishDiagnostic("success") });
        }
      };
      engine.onError = (message) => {
        if (!finished) {
          fatalEngineError = true;
          finished = true;
          clearTimeout(timeout);
          clearInterval(foregroundPoll);
          clearTimeout(stopWatchdog);
          const error = new Error(String(message));
          error.diagnostics = finishDiagnostic("failure");
          rejectReport(error);
        }
      };
      engine.uci(`setoption name MultiPV value ${Math.max(1, Math.min(5, specification.multipv))}`);
      engine.uci(`position fen ${specification.position_start_fen}${position.length ? ` moves ${position.join(" ")}` : ""}`);
      engine.uci(`go depth ${specification.depth}${specification.root_move_uci ? ` searchmoves ${specification.root_move_uci}` : ""}`);
    });
  }

  return { evaluate, get fatalEngineError() { return fatalEngineError; } };
}
