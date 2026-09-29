// @vitest-environment node
import { expect, it, vi } from "vitest";
import { recoverNextEngineJob } from "../../scripts/engine-job-recovery.mjs";

it("handles a recovered game job before clearing a defensive claim journal", async () => {
  const game = { kind: "position", id: "game-job" };
  const defense = { kind: "threat", id: "defense-job" };
  const gameJournal = { recover: vi.fn().mockResolvedValueOnce({
    path: "/api/games/analysis/position/claim", result: { job: game },
  }).mockResolvedValue(null) };
  const defenseJournal = { recover: vi.fn().mockResolvedValue({
    path: "/api/defensive-threats/analysis/claim", result: { job: defense },
  }) };
  const first = await recoverNextEngineJob(gameJournal, defenseJournal);
  expect(first).toMatchObject({ job: game, jobKind: "game" });
  expect(defenseJournal.recover).not.toHaveBeenCalled();
  const second = await recoverNextEngineJob(gameJournal, defenseJournal);
  expect(second).toMatchObject({ job: defense, jobKind: "defense" });
});

it("a defensive null result cannot overwrite a recovered game job", async () => {
  const game = { kind: "position", id: "game-job" };
  const gameJournal = { recover: vi.fn().mockResolvedValue({
    path: "/api/games/analysis/position/claim", result: { job: game },
  }) };
  const defenseJournal = { recover: vi.fn().mockResolvedValue({
    path: "/api/defensive-threats/analysis/claim", result: { job: null },
  }) };
  expect(await recoverNextEngineJob(gameJournal, defenseJournal))
    .toMatchObject({ job: game, jobKind: "game" });
  expect(defenseJournal.recover).not.toHaveBeenCalled();
});
