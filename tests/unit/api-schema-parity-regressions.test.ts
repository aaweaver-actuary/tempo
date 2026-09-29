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

it("backend sync serialization matches strict frontend status contract across progress and completion", () => {
  const output = execFileSync(resolvePython(), ["-c", `
import json
from app.models import GameSyncStatusResponse
from app.services.game_sync_coordinator import serialize_job
row = {'id':'11111111-1111-4111-8111-111111111111','status':'running','created_at':'2026-09-29T12:00:00+00:00','started_at':None,'completed_at':None,'updated_at':'2026-09-29T12:00:00+00:00','error':None,'result_json':json.dumps({'imported':3})}
def response(): return GameSyncStatusResponse(providers=[],active_filters={'days':90,'speeds':['rapid'],'rated_only':True},active_job=serialize_job(row)).model_dump(mode='json')
running=response()
row['status']='complete'
row['result_json']=json.dumps({'imported':3,'synced_at':'2026-09-29T12:01:00+00:00','cached':False,'incremental':True,'providers':{}})
print(json.dumps([running,response()]))
`], { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" });
  const [running, complete] = JSON.parse(output) as unknown[];
  expect(syncStatusSchema.parse(running).active_job?.result).toBeNull();
  expect(syncStatusSchema.parse(complete).active_job?.result?.imported).toBe(3);
});
