import { afterEach, expect, it, vi } from "vitest";
import { enqueueGameSyncCommand } from "../../app/lib/game-sync-command";
import { PendingOperationError } from "../../app/lib/operation-status";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

it("game sync retains its operation ID until Celery confirms admission", async () => {
  const keys: string[] = [];
  let receiptReads = 0;
  const requester = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const key = String((init?.headers as Record<string, string>)["Idempotency-Key"]);
    keys.push(key);
    return Response.json({ operation_id: key, state: "pending" }, { status: 202 });
  });
  vi.stubGlobal("fetch", vi.fn(async () => {
    receiptReads += 1;
    return Response.json(receiptReads < 3 ? { state: "pending" } : {
      state: "complete", response: {
        imported: 0, job_id: "sync-job", status: "queued", providers: {},
      },
    });
  }));
  const request = { lichess_username: "alice" };
  await expect(enqueueGameSyncCommand(request, requester)).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  expect(localStorage.getItem("tempo-pending-game-sync-command-v1")).not.toBeNull();
  const confirmed = await enqueueGameSyncCommand(request, requester);
  expect((await confirmed.json() as { job_id: string }).job_id).toBe("sync-job");
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect(localStorage.getItem("tempo-pending-game-sync-command-v1")).toBeNull();
});

it("game sync does not overtake a pending command with a different repair request", async () => {
  const requester = vi.fn(async () => Response.json(
    { operation_id: "sync-first", state: "pending" }, { status: 202 },
  ));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "pending" })));
  await expect(enqueueGameSyncCommand({ repair: false }, requester)).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  await expect(enqueueGameSyncCommand({ repair: true }, requester)).rejects.toBeInstanceOf(
    PendingOperationError,
  );
  expect(requester).toHaveBeenCalledTimes(1);
});
