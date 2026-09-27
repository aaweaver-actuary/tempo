// @vitest-environment node
import { Chess } from "chess.js";
import { afterEach, expect, it, vi } from "vitest";
import { runCoalescedStudyMatch } from "../../app/lib/background-study";
import { tempoPerformanceTimings } from "../../app/lib/performance";

const fens = [new Chess().fen()];
const board = new Chess();
board.move("Nf3");
board.move("Nf6");
fens.push(board.fen());
board.move("d4");
board.move("d5");
fens.push(board.fen());

afterEach(() => vi.unstubAllGlobals());

it("rapid Builder positions coalesce queued matches to the latest FEN", async () => {
  class ControlledWorker {
    static instance: ControlledWorker;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    posted: Array<{ id: number; task: { fen: string } }> = [];
    constructor() { ControlledWorker.instance = this; }
    postMessage(message: { id: number; task: { fen: string } }) { this.posted.push(message); }
    complete(requestIndex: number) {
      const request = this.posted[requestIndex];
      this.onmessage?.({ data: { id: request.id, result: [] } } as MessageEvent);
    }
    terminate() {}
  }
  vi.stubGlobal("Worker", ControlledWorker);
  const timingCountBefore = tempoPerformanceTimings().length;
  const task = (fen: string) => ({
    kind: "findPositionMatches" as const,
    repertoireId: "white", revision: 1, fen,
  });
  const firstAbort = new AbortController();
  const first = runCoalescedStudyMatch(task(fens[0]), firstAbort.signal);
  const obsolete = runCoalescedStudyMatch(task(fens[1]));
  const latest = runCoalescedStudyMatch(task(fens[2]));
  expect(ControlledWorker.instance.posted.map(({ task }) => task.fen)).toEqual([fens[0]]);
  await expect(obsolete).rejects.toThrow("Superseded");
  firstAbort.abort();
  await expect(first).rejects.toThrow("Cancelled");
  ControlledWorker.instance.complete(0);
  await vi.waitFor(() => expect(ControlledWorker.instance.posted.map(({ task }) => task.fen))
    .toEqual([fens[0], fens[2]]));
  ControlledWorker.instance.complete(1);
  await expect(latest).resolves.toEqual([]);
  const next = runCoalescedStudyMatch(task(fens[0]));
  await vi.waitFor(() => expect(ControlledWorker.instance.posted).toHaveLength(3));
  ControlledWorker.instance.complete(2);
  await expect(next).resolves.toEqual([]);
  const waits = tempoPerformanceTimings().slice(timingCountBefore)
    .filter(({ operation }) => operation === "study-match-coalesced-wait");
  expect(waits).toHaveLength(3);
  expect(waits.every(({ duration }) => duration >= 0)).toBe(true);
});
