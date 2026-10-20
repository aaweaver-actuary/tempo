/** Semantic read capability shared by Games views and the HTTP adapter. */
import { z } from 'zod';
export const gameScopeSchema=z.object({run_id:z.string().min(1),revision:z.number().int().positive(),input_id:z.string().min(1),input_revision:z.number().int().positive()});
const contextSchema=z.object({ply:z.number().int().nonnegative(),repertoire_id:z.string(),policy_id:z.string(),status:z.enum(['matched','missed','unknown']),reason:z.string(),coverage:z.string(),expected_moves:z.array(z.string())}).passthrough();
const gameSchema=z.object({game_id:z.string(),provider:z.string().nullable().optional(),provider_game_id:z.string().nullable().optional(),game_url:z.string().nullable().optional(),played_at:z.string().nullable(),learner_color:z.enum(['white','black']),starting_fen:z.string(),moves:z.array(z.string()).max(1000),contexts:z.array(contextSchema).max(2000)}).passthrough();
export const gamePageSchema=z.object({schema_version:z.literal(1),workspace_id:z.string(),...gameScopeSchema.shape,game_count:z.number().int().nonnegative(),selections:z.array(z.object({repertoire_id:z.string(),policy_id:z.string(),repertoire_title:z.string().nullish()}).passthrough()).max(32),items:z.array(gameSchema).max(10),next_after:z.string().nullable()});
export type GameScope=z.infer<typeof gameScopeSchema>;
export type GamePage=z.infer<typeof gamePageSchema>;
export type Game=GamePage['items'][number];
export interface GamesReader {
  listScopes():Promise<GameScope[]>;
  readScope(runId:string):Promise<GameScope>;
  readGames(scope:GameScope,after?:string):Promise<GamePage>;
}
