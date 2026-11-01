// @vitest-environment node
import { expect, it, vi } from "vitest";
import { createEngineJobFamilies } from "../../scripts/engine-job-families.mjs";
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
  const { game: gameFamily, defense: defenseFamily } = createEngineJobFamilies({ request: vi.fn(), durableRequest: gameJournal, defenseClaimRequest: defenseJournal });
  const first = await recoverNextEngineJob(gameFamily, defenseFamily);
  expect(first).toMatchObject({ job: game, family: gameFamily });
  expect(defenseJournal.recover).not.toHaveBeenCalled();
  const second = await recoverNextEngineJob(gameFamily, defenseFamily);
  expect(second).toMatchObject({ job: defense, family: defenseFamily });
});

it("a defensive null result cannot overwrite a recovered game job", async () => {
  const game = { kind: "position", id: "game-job" };
  const gameJournal = { recover: vi.fn().mockResolvedValue({
    path: "/api/games/analysis/position/claim", result: { job: game },
  }) };
  const defenseJournal = { recover: vi.fn().mockResolvedValue({
    path: "/api/defensive-threats/analysis/claim", result: { job: null },
  }) };
  const { game: gameFamily, defense: defenseFamily } = createEngineJobFamilies({ request: vi.fn(), durableRequest: gameJournal, defenseClaimRequest: defenseJournal });
  expect(await recoverNextEngineJob(gameFamily, defenseFamily))
    .toMatchObject({ job: game, family: gameFamily });
  expect(defenseJournal.recover).not.toHaveBeenCalled();
});
