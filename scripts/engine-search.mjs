import { startEngineAttempt, engineWaitingStage } from "./engine-attempt-diagnostics.mjs";

export function engineSearchWasPreempted(error) {
  return error.message === "preempted" || error.diagnostics?.outcome === "preempted";
}

export function createEngineSearch(engine) {
  let fatalEngineError = false;
  function evaluate(job, kind, shouldContinue) {
    if (fatalEngineError) throw new Error("Stockfish is unavailable after a fatal engine error");
    engineWaitingStage(kind, "execution");
    const finishDiagnostic = startEngineAttempt(kind);
    return new Promise((resolveReport, rejectReport) => {
      const { request: specification } = job;
      const lines = new Map();
      let finished = false;
      let preempted = false;
      let stopReason = "preempted";
      let stopWatchdog;
      const stopSearch = (cause) => {
        if (finished || preempted) return;
        stopReason = cause;
        preempted = true;
        clearTimeout(timeout);
        stopWatchdog = setTimeout(() => {
          fatalEngineError = true;
          engine.terminate?.();
          finished = true;
          clearTimeout(timeout);
          clearInterval(foregroundPoll);
          const error = new Error("Stockfish did not drain after cancellation");
          error.diagnostics = finishDiagnostic(stopReason === "preempted" ? "preempted" : "timeout");
          rejectReport(error);
        }, 75);
        engine.uci("stop");
      };
      const position = specification.position_prefix_uci ?? [];
      const whiteTurn = (specification.position_start_fen.split(" ")[1] === "w") === (position.length % 2 === 0);
      const whiteSign = whiteTurn ? 1 : -1;
      const timeout = setTimeout(() => {
        if (!finished && !preempted) stopSearch("Engine timed out before requested depth");
      }, 55_000);
      let controlPollPending = false;
      const foregroundPoll = process.env.TEMPO_ENGINE_SMOKE === "1" ? undefined : setInterval(async () => {
        if (finished || preempted || controlPollPending) return;
        controlPollPending = true;
        try {
          let allowed = false;
          try { allowed = await shouldContinue(); } catch { /* A control outage stops work. */ }
          if (!finished && !preempted && !allowed) stopSearch("preempted");
        } finally {
          controlPollPending = false;
        }
      }, 75);
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
          engine.terminate?.();
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
