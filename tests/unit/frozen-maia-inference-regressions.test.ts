// @vitest-environment node
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { Chess } from 'chess.js';
import { expect, it } from 'vitest';

it('bounded Maia helper emits all legal moves for colors castling promotions and en passant without claiming jobs', () => {
  const positions = [
    'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
    'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',
    '4k3/P7/8/8/8/8/7p/4K3 w - - 0 1',
    '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
  ];
  const input = positions.map((fen, id) => JSON.stringify({fen, id, elo:1500})).join('\n')+'\n';
  const output = execFileSync('node',['scripts/frozen-maia-inference.mjs'],{input,encoding:'utf8',env:{...process.env,TEMPO_API_URL:'http://127.0.0.1:1'}});
  const results = output.trim().split('\n').map(line => JSON.parse(line));
  expect(results).toHaveLength(positions.length);
  for (const [index,result] of results.entries()) {
    expect(result.error).toBeUndefined();
    const legal = new Chess(positions[index]).moves({verbose:true}).map(move => `${move.from}${move.to}${move.promotion ?? ''}`).sort();
    expect(result.moves.map((move: {move_uci:string}) => move.move_uci).sort()).toEqual(legal);
    expect(result.moves.every((move: {probability:number}) => Number.isFinite(move.probability) && move.probability >= 0)).toBe(true);
    expect(result.moves.reduce((sum:number, move:{probability:number}) => sum+move.probability,0)).toBeCloseTo(1,10);
    expect(Object.keys(result.provenance.checksums)).toHaveLength(9);
    expect(result.provenance.model_id).toBe('maia3-onnx');
    expect(result.provenance.model_version).toMatch(/^maia3-v1-[0-9a-f]{16}$/);
    expect(result.provenance.implementation_version).toBe(`maia-worker-v1-${result.provenance.implementation_sha256.slice(0, 16)}`);
    expect(result.provenance.runtime_context.node).toBe(process.version);
    for (const file of ['core.mjs', 'model.mjs', 'errors.mjs', 'model-descriptor.json']) {
      const expected = createHash('sha256').update(readFileSync(`workers/maia/${file}`)).digest('hex');
      expect(result.provenance.implementation_files[file]).toBe(expected);
    }
  }
},30_000);
