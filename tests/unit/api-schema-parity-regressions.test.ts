// @vitest-environment node
import { execFileSync } from "node:child_process";
import { expect, it } from "vitest";
import { settingsResponseSchema, syncStatusSchema } from "../../app/domain/schemas";
import { resolvePython } from "../../scripts/resolve-python.mjs";

it("Pydantic settings response and strict Zod adapter accept the same transport fixture", () => {
  const output = execFileSync(
    resolvePython(),
    [
      "-c",
      "from app.models import Settings; print(Settings().model_dump_json())",
    ],
    {
      env: { ...process.env, PYTHONPATH: "backend" },
      encoding: "utf8",
    },
  );
  const raw: unknown = JSON.parse(output);
  expect(settingsResponseSchema.parse(raw)).toEqual(raw);
  expect(
    settingsResponseSchema.safeParse({
      ...settingsResponseSchema.parse(raw),
      new_cards_per_day: -1,
    }).success,
  ).toBe(false);
});

it("backend sync serialization matches frontend contract across every status and legacy partial counters", () => {
  const output = execFileSync(resolvePython(), ["-c", `
import json
from app.models import GameSyncStatusResponse
from app.services.game_sync_coordinator import serialize_job
row = {'id':'11111111-1111-4111-8111-111111111111','status':'queued','created_at':'2026-09-29T12:00:00+00:00','started_at':None,'completed_at':None,'updated_at':'2026-09-29T12:00:00+00:00','error':None,'result_json':json.dumps({'imported':3})}
statuses = ['queued','running','paused','retrying','failed']
payloads = []
for status in statuses:
 row['status'] = status
 payloads.append(GameSyncStatusResponse(providers=[],active_filters={'days':90,'speeds':['rapid'],'rated_only':True},active_job=serialize_job(row)).model_dump(mode='json'))
row['status']='complete'
row['completed_at']='2026-09-29T12:01:00+00:00'
row['result_json']=json.dumps({'imported':3,'synced_at':'2026-09-29T12:01:00+00:00','cached':False,'incremental':True,'providers':{'lichess':{'provider':'lichess','username':'legacy-user','status':'idle','fetched':3,'inserted':2,'updated':0,'duplicates':1,'filtered':0,'rejected':0,'failed':0,'error':None,'retry_after':None}}})
payloads.append(GameSyncStatusResponse(providers=[],active_filters={'days':90,'speeds':['rapid'],'rated_only':True},active_job=serialize_job(row)).model_dump(mode='json'))
print(json.dumps(payloads))
`], { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" });
  const payloads = JSON.parse(output) as unknown[];
  expect(payloads).toHaveLength(6);
  for (const payload of payloads.slice(0, 5))
    expect(syncStatusSchema.parse(payload).active_job?.result).toBeNull();
  const complete = payloads[5];
  expect(syncStatusSchema.parse(complete).active_job?.result?.imported).toBe(3);
  expect(syncStatusSchema.parse(complete).active_job?.result?.providers.lichess).toMatchObject({
    inserted: 2, duplicates: 1, failed: 0,
  });
});
