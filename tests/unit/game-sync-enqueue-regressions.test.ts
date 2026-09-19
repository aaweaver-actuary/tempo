import { describe, expect, it } from "vitest";
import { syncResultSchema } from "../../app/domain/schemas";

const queuedSync = {
  imported: 0,
  job_id: "65b588f2-cba2-4678-bc8c-b7a10b5227f1",
  status: "queued" as const,
};

const lichessResult = {
  provider: "lichess" as const,
  username: "player",
  status: "idle" as const,
  fetched: 1,
  inserted: 1,
  updated: 0,
  duplicates: 0,
  filtered: 0,
  rejected: 0,
  failed: 0,
  error: null,
  retry_after: null,
};

describe("game sync enqueue provider contracts", () => {
  it("queued game sync accepts an empty provider map without a diagnostic", () => {
    expect(syncResultSchema.parse({ ...queuedSync, providers: {} })).toEqual({
      ...queuedSync,
      providers: {},
    });
  });

  it("single provider sync result does not require the other provider", () => {
    expect(
      syncResultSchema.parse({
        ...queuedSync,
        status: "complete",
        providers: { lichess: lichessResult },
      }).providers,
    ).toEqual({ lichess: lichessResult });
  });

  it("malformed queued provider data still fails strict validation", () => {
    expect(
      syncResultSchema.safeParse({
        ...queuedSync,
        providers: { lichess: { ...lichessResult, unexpected: true } },
      }).success,
    ).toBe(false);
    expect(
      syncResultSchema.safeParse({
        ...queuedSync,
        providers: { unknown: lichessResult },
      }).success,
    ).toBe(false);
  });
});
