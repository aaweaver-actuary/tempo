import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { Chess } from 'chess.js';
export const positions = JSON.parse(readFileSync(new URL('./positions.json', import.meta.url), 'utf8'));
const descriptor = JSON.parse(readFileSync(new URL('../model-descriptor.json', import.meta.url), 'utf8'));
export function jobFor(position, overrides = {}) {
  const legalMoves = position.moves ?? new Chess(position.fen).moves({ verbose: true }).map(move => `${move.from}${move.to}${move.promotion ?? ''}`).sort();
  return {
    contract_version: 1, workspace_id: 'maia-isolated-qualification', job_id: position.name,
    request_revision: 1, execution_generation: 1,
    input_digest: 'sha256:' + createHash('sha256').update(JSON.stringify([position.fen, position.rating, legalMoves])).digest('hex'),
    constraints: { max_wall_time_ms: 60000, max_memory_mb: 1024 }, worker: 'maia', fen: position.fen,
    rating: position.rating, model_id: descriptor.model_id, model_version: descriptor.model_version, legal_moves: legalMoves, ...overrides,
  };
}
