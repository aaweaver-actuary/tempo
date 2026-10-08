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
from app.services.game_sync_serialization import serialize_job
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


it("Python recommendation snapshots satisfy the frontend list and detail contract", async () => {
  const { segmentationListSchema, segmentationDetailSchema } = await import("../../app/domain/opening-segmentation");
  const raw = JSON.parse(execFileSync(resolvePython(), ["-c", `
import json
from app.opening_segmentation_api import recommendation_projection
state = {'run_id':'publication', 'content_version':3, 'graph_generation':2}
rec = recommendation_projection(state, {'run_id':'publication','id':'rec','repertoire_id':'rep','kind':'shared_trunk','source_fingerprint':'sources','decisions_before':48,'decisions_after':20,'decisions_avoided':28,'additional_starts':1,'segment_count':9})
base = {'version':1,'preview_only':True,'content_version':3,'graph_generation':2}
print(json.dumps({'list':{**base,'state':'ready','error':None,'invalidated_pins':0,'recommendations':[rec]},'detail':{**base,'snapshot_id':rec['snapshot_id'],'source_fingerprint':'sources','recommendation':rec,'rationale':'Shared opening','estimate_basis':'structural count','segments':[],'routes':[],'next_segment':None,'next_route':None}}))
`], { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" }));
  expect(segmentationDetailSchema.parse(raw.detail).snapshot_id).toBe(segmentationListSchema.parse(raw.list).recommendations[0].snapshot_id);
});


it("Python tactic capture result satisfies the frontend durable capture contract", async () => {
  const { tacticCaptureResultSchema } = await import("../../app/lib/tactic-capture-command");
  const raw = JSON.parse(execFileSync(resolvePython(), ["-c", "from app.models import TacticCaptureResponse; print(TacticCaptureResponse(capture_id='11111111-1111-4111-8111-111111111111',card_id='synthetic',reused=False,queued=True,introduced=True).model_dump_json())"],
    { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" }));
  expect(tacticCaptureResultSchema.parse(raw)).toEqual(raw);
  expect(tacticCaptureResultSchema.safeParse({ ...raw, queued: "true" }).success).toBe(false);
});

it("Python repertoire limit response matches the frontend contract for inherited, zero, and custom limits", async () => {
  const { repertoireSettingsResponseSchema } = await import("../../app/lib/repertoire-settings-save");
  const output = execFileSync(resolvePython(), ["-c", `
import json
from app.models import RepertoireSettingsResponse
print(json.dumps([RepertoireSettingsResponse(repertoire_id='rep',new_cards_per_day=value,effective_new_cards_per_day=value if value is not None else 10).model_dump(mode='json') for value in [None,0,5]]))
`], { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" });
  for (const raw of JSON.parse(output)) expect(repertoireSettingsResponseSchema.parse(raw)).toEqual(raw);
});

it("issue78_python_prefix_evaluator_matches_frontend_source_and_comparison_contracts", async () => {
  const { prefixSourceSchema, prefixComparisonSchema } = await import("../../app/domain/prefix-comparison");
  const raw = JSON.parse(execFileSync(resolvePython(), ["tests/fixtures/prefix-comparison/generate.py"],
    { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" }));
  expect(prefixSourceSchema.parse(raw.source)).toEqual(raw.source);
  for (const result of Object.values(raw.comparisons)) expect(prefixComparisonSchema.parse(result)).toEqual(result);
  const { default: checkedFixture } = await import("../fixtures/prefix-comparison/structural.json");
  expect(raw).toEqual(checkedFixture);
  expect(prefixSourceSchema.safeParse({ ...raw.source, version: 2 }).success).toBe(false);
  expect(prefixComparisonSchema.safeParse({ ...raw.comparisons["a,b:2"], preview_only: false }).success).toBe(false);
});
