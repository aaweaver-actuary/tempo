import { z } from "zod";
const envelope = {schema_version:z.literal(1),workspace_id:z.string().min(1)};
const position = z.object({game_id:z.string().nullable(),ply:z.number().int().nullable(),fen:z.string(),actual_move:z.string().nullable(),learner_color:z.enum(["white","black"])}).passthrough();
export const findingSchema=z.object({finding_id:z.string(),finding_revision:z.number().int(),evidence_revision:z.string(),category:z.enum(["repertoire_miss","practical_gap","engine_opportunity"]),representative:position,expected_moves:z.array(z.string()),supporting_games:z.array(z.string()),supporting_game_count:z.number().int(),dismissal_state:z.enum(["open","dismissed"]),reason_codes:z.array(z.string()),evidence_status:z.string(),lineage:z.record(z.string(),z.unknown()).nullable(),actions:z.record(z.string(),z.object({available:z.boolean(),reason:z.string().nullish()})),engine:z.unknown().nullable()}).passthrough();
export type Finding=z.infer<typeof findingSchema>;
export const reportSchema=z.object({...envelope,run_id:z.string(),generation:z.number().int().nullable(),items:z.array(findingSchema),next_cursor:z.string().nullable(),status:z.string()}).passthrough();
export type Report=z.infer<typeof reportSchema>;
export const bootstrapSchema=z.strictObject({...envelope,capabilities:z.array(z.string()),policy_version:z.string()});
export const listSchema=z.object({...envelope,items:z.array(z.record(z.string(),z.unknown()))}).passthrough();
export const detailSchema=z.object({...envelope,run_id:z.string(),generation:z.number().int(),finding:findingSchema});
export const commandSchema=z.object({...envelope,accepted:z.literal(true),command_id:z.string(),request_digest:z.string().regex(/^sha256:[0-9a-f]{64}$/)}).passthrough();
export const runRequestSchema=z.strictObject({
  ...envelope,run_id:z.string().min(1),revision:z.number().int().positive(),
  expected_revision:z.number().int().positive().nullable().default(null),
  input_id:z.string().min(1),input_revision:z.number().int().positive(),
  policy_version:z.literal('c-analysis-v1').default('c-analysis-v1'),
  position_budget:z.number().int().min(0).max(10000).default(250),
  ranking_source:z.enum(['actual','lichess','masters','maia']).default('actual'),
  source_evidence_ids:z.array(z.string().min(1)).max(1024).default([]),
  ranking_evidence_id:z.string().min(1).nullable().default(null),
}).refine(scope=>scope.ranking_source==='actual'||scope.ranking_evidence_id!==null&&scope.source_evidence_ids.includes(scope.ranking_evidence_id),'Explicit ranking evidence is required.');
export type RunCreationRequest=Omit<z.input<typeof runRequestSchema>,'run_id'>&{run_id?:string};
export const runStatusSchema=z.object({...envelope,run_id:z.string().min(1),revision:z.number().int().positive(),scope:runRequestSchema}).passthrough();
