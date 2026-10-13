// @vitest-environment node
import { expect, it } from "vitest";
import { RedisAdmissionCleanup } from "../browser/support/redis-admission-cleanup";

function harness(failures: Record<string, unknown> = {}) {
  const calls: string[] = [];
  const operation = (resource: string) => async () => {
    calls.push(resource);
    if (resource in failures) throw failures[resource];
  };
  const cleanup = new RedisAdmissionCleanup({
    stopRedis: operation("stop"), pauseRedis: operation("pause"),
    startRedis: operation("start"), unpauseRedis: operation("unpause"),
    closePage: operation("page"), cleanupFixture: operation("fixture"),
  });
  return { cleanup, calls, operation };
}

it.each(["start", "unpause"])("Redis admission %s failure still closes page and cleans fixture while retaining assertion", async (restoration) => {
  const assertion = new Error("original assertion");
  const restorationFailure = new Error(`${restoration} denied`);
  const { cleanup, calls, operation } = harness({ [restoration]: restorationFailure });
  let observed: unknown;
  try {
    await cleanup.run(async () => {
      await cleanup.withResponseListener(operation("listener"), async () => {
        if (restoration === "start") await cleanup.stopRedis();
        else await cleanup.pauseRedis();
        throw assertion;
      });
    });
  } catch (failure) { observed = failure; }
  expect(calls).toEqual([restoration === "start" ? "stop" : "pause", "listener", restoration, "page", "fixture"]);
  expect(observed).toBeInstanceOf(AggregateError);
  const aggregate = observed as AggregateError;
  expect(aggregate.cause).toBe(assertion);
  expect(aggregate.errors[0]).toBe(assertion);
  expect(aggregate.errors[1].cause).toBe(restorationFailure);
});

it.each(["refusal", "nonreply"])("Redis admission assertion during %s removes listener and attempts every owned resource", async (fault) => {
  const assertion = new Error("scenario assertion");
  const { cleanup, calls, operation } = harness();
  await expect(cleanup.run(async () => {
    await cleanup.withResponseListener(operation("listener"), async () => {
      if (fault === "refusal") await cleanup.stopRedis();
      else await cleanup.pauseRedis();
      throw assertion;
    });
  })).rejects.toBe(assertion);
  expect(calls).toEqual([fault === "refusal" ? "stop" : "pause", "listener", fault === "refusal" ? "start" : "unpause", "page", "fixture"]);
});

it.each(["fixture", "page"])("Redis admission %s cleanup rejection cannot skip other resources", async (resource) => {
  const cleanupFailure = new Error(`${resource} denied`);
  const { cleanup, calls } = harness({ [resource]: cleanupFailure });
  await expect(cleanup.run(async () => { await cleanup.pauseRedis(); })).rejects.toMatchObject({
    errors: [expect.objectContaining({ cause: cleanupFailure })],
  });
  expect(calls).toEqual(["pause", "unpause", "page", "fixture"]);
});

it("Redis admission accumulates all cleanup failures after the original failure", async () => {
  const assertion = new Error("assertion");
  const failures = Object.fromEntries(["listener", "unpause", "start", "page", "fixture"].map(resource => [resource, new Error(resource)]));
  const { cleanup, calls, operation } = harness(failures);
  await expect(cleanup.run(async () => {
    await cleanup.withResponseListener(operation("listener"), async () => {
      await cleanup.stopRedis();
      await cleanup.pauseRedis();
      throw assertion;
    });
  })).rejects.toMatchObject({
    cause: assertion,
    errors: [assertion, ...["listener", "listener", "unpause", "start", "page", "fixture"].map(resource => expect.objectContaining({ cause: failures[resource] }))],
  });
  expect(calls).toEqual(["stop", "pause", "listener", "listener", "unpause", "start", "page", "fixture"]);
});

it("Redis admission synchronous listener failure is retained and removal is retried before closing page", async () => {
  const listenerFailure = new Error("detach failed");
  const { cleanup, calls } = harness();
  let attempts = 0;
  await expect(cleanup.run(async () => {
    await cleanup.withResponseListener(() => {
      calls.push("listener");
      if (++attempts === 1) throw listenerFailure;
    }, async () => {});
  })).rejects.toMatchObject({ errors: [expect.objectContaining({ cause: listenerFailure })] });
  expect(calls).toEqual(["listener", "listener", "page", "fixture"]);
});

it.each(["stop", "pause"])("Redis admission rejected %s command retains its restoration obligation", async (command) => {
  const commandFailure = new Error("command failed after changing container state");
  const { cleanup, calls } = harness({ [command]: commandFailure });
  await expect(cleanup.run(async () => {
    if (command === "stop") await cleanup.stopRedis();
    else await cleanup.pauseRedis();
  })).rejects.toBe(commandFailure);
  expect(calls).toEqual([command, command === "stop" ? "start" : "unpause", "page", "fixture"]);
});

it("Redis admission successful restoration clears obligations and listener removal is not repeated", async () => {
  const { cleanup, calls, operation } = harness();
  await cleanup.run(async () => {
    for (const fault of ["refusal", "nonreply"]) {
      await cleanup.withResponseListener(operation("listener"), async () => {
        if (fault === "refusal") await cleanup.stopRedis();
        else await cleanup.pauseRedis();
        await cleanup.restoreRedis();
      });
    }
  });
  expect(calls).toEqual(["stop", "start", "listener", "pause", "unpause", "listener", "page", "fixture"]);
});

it.each(["start", "unpause"])("Redis admission failed in-scenario %s is retried by final cleanup", async (restoration) => {
  const restorationFailure = new Error("restoration failed once");
  const { cleanup, calls } = harness({ [restoration]: restorationFailure });
  await expect(cleanup.run(async () => {
    if (restoration === "start") await cleanup.stopRedis();
    else await cleanup.pauseRedis();
    await cleanup.restoreRedis();
  })).rejects.toMatchObject({ cause: restorationFailure, errors: [restorationFailure, expect.objectContaining({ cause: restorationFailure })] });
  expect(calls).toEqual([restoration === "start" ? "stop" : "pause", restoration, restoration, "page", "fixture"]);
});

it.each([undefined, null, false, "evidence write failed"])("Redis admission preserves original thrown value %s while attempting cleanup", async (failure) => {
  const { cleanup, calls } = harness();
  let caught = false;
  let observed: unknown;
  try { await cleanup.run(async () => { throw failure; }); }
  catch (error) { caught = true; observed = error; }
  expect(caught).toBe(true);
  expect(observed).toBe(failure);
  expect(calls).toEqual(["page", "fixture"]);
});

it("Redis admission fixture cleanup is eligible only after seed returns its token", async () => {
  const calls: string[] = [];
  const seeded = false;
  const cleanup = new RedisAdmissionCleanup({
    stopRedis: () => {}, pauseRedis: () => {}, startRedis: () => {}, unpauseRedis: () => {},
    closePage: () => { calls.push("page"); },
    cleanupFixture: () => { if (seeded) calls.push("fixture"); },
  });
  const seedFailure = new Error("no restoration token");
  await expect(cleanup.run(async () => { throw seedFailure; })).rejects.toBe(seedFailure);
  expect(calls).toEqual(["page"]);
});

it("Redis admission synchronous page closure failure still attempts fixture cleanup", async () => {
  const pageFailure = new Error("page closure failed");
  const calls: string[] = [];
  const cleanup = new RedisAdmissionCleanup({
    stopRedis: () => {}, pauseRedis: () => {}, startRedis: () => {}, unpauseRedis: () => {},
    closePage: () => { calls.push("page"); throw pageFailure; },
    cleanupFixture: () => { calls.push("fixture"); },
  });
  await expect(cleanup.run(async () => {})).rejects.toMatchObject({
    errors: [expect.objectContaining({ cause: pageFailure })],
  });
  expect(calls).toEqual(["page", "fixture"]);
});

it.each(["evidence write", "evidence attachment"])("Redis admission %s failure is preserved while cleaning owned resources", async (publication) => {
  const evidenceFailure = new Error(publication);
  const { cleanup, calls } = harness();
  await expect(cleanup.run(async () => { throw evidenceFailure; })).rejects.toBe(evidenceFailure);
  expect(calls).toEqual(["page", "fixture"]);
});
