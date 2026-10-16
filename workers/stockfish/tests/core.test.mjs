import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareJob, IterationCollector, validateVariations } from '../core.mjs';
import { stockfishInputDigest, validatePayload, canonicalJson } from '../wire.mjs';
import { job, sample } from './helpers.mjs';
const line = (depth, rank, move, score = 'cp 20', tail = '') => `info depth ${depth} multipv ${rank} score ${score} nodes 100 time 1 pv ${move}${tail}`;

test('B_history_root', () => {
  const input = job({ moves: ['e2e4'], search_moves: ['e7e5'] });
  assert.equal(prepareJob(input).board.turn(), 'b');
  const collector = new IterationCollector(input); collector.receive(line(4, 1, 'e7e5', 'cp 25'));
  const result = collector.receive('bestmove e7e5');
  assert.equal(result[0].score.value, 25); assert.equal(result[0].score_perspective, 'side_to_move');
  assert.throws(() => prepareJob(job({ moves: ['e2e4'], search_moves: ['d2d4'] })), { code: 'illegal_search_move' });
});
test('B_both_colors', () => {
  for (const moves of [[], ['e2e4']]) {
    const input = job({ moves }); const root = prepareJob(input).roots[0];
    const collector = new IterationCollector(input); collector.receive(line(3, 1, root, 'cp -73'));
    assert.deepEqual(collector.receive('bestmove ' + root)[0].score, { kind: 'centipawns', value: -73 });
  }
});
test('B_special_moves', () => {
  const cases = [
    ['r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1', [], 'e1g1'],
    ['4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 10', [], 'e5d6'],
    ['4k3/P7/8/8/8/8/8/4K3 w - - 0 1', [], 'a7a8q'],
    [sample.fen, ['e2e4', 'a7a6', 'e4e5', 'd7d5'], 'e5d6'],
  ];
  for (const [fen, moves, move] of cases) assert.deepEqual(prepareJob(job({ fen, moves, search_moves: [move] })).roots, [move]);
});
test('B_illegal_history', () => {
  for (const moves of [['e2e5'], ['e2e4', 'd2d4']]) assert.throws(() => prepareJob(job({ moves })), { code: 'illegal_history' });
  for (const fen of ['8/8/8/8/8/8/8/8 w - - 0 1', '4k3/8/8/8/8/8/4K3/8 w K - 0 1', '4k3/8/8/8/8/8/4K3/8 w - e6 0 1']) assert.throws(() => prepareJob(job({ fen })), { code: 'invalid_fen' });
});
test('B_restricted_roots', () => {
  const input = job({ search_moves: ['e2e4'] });
  const collector = new IterationCollector(input);
  assert.throws(() => collector.receive(line(4, 1, 'd2d4')), { code: 'invalid_engine_output' });
  assert.throws(() => prepareJob(job({ search_moves: ['e2e4', 'e2e4'] })), { code: 'duplicate_search_move' });
});
test('B_multipv_integrity', () => {
  const input = job({ multi_pv: 2, search_moves: ['e2e4', 'd2d4'] });
  const collector = new IterationCollector(input);
  collector.receive(line(3, 1, 'e2e4')); collector.receive(line(3, 2, 'd2d4'));
  collector.receive(line(4, 1, 'd2d4')); // partial later depth must not mix.
  const result = collector.receive('bestmove d2d4'); assert.deepEqual(result.map(v => v.depth), [3, 3]);
  const duplicate = new IterationCollector(input); duplicate.receive(line(3, 1, 'e2e4'));
  assert.throws(() => duplicate.receive(line(3, 2, 'e2e4')), { code: 'invalid_engine_output' });
  assert.equal(prepareJob(job({ search_moves: ['e2e4'], multi_pv: 256 })).multiPv, 1);
});
test('B_score_types', () => {
  for (const [uci, expected] of [['mate -2', {kind:'mate',value:-2}], ['cp 123', {kind:'centipawns',value:123}]]) {
    const collector = new IterationCollector(job()); collector.receive(line(2, 1, 'e2e4', uci));
    assert.deepEqual(collector.receive('bestmove e2e4')[0].score, expected);
  }
});
test('B_partial_or_bound_output', () => {
  for (const bound of ['lowerbound', 'upperbound']) {
    const collector = new IterationCollector(job()); collector.receive(`info depth 4 score cp 25 ${bound} nodes 100 pv e2e4`);
    assert.throws(() => collector.receive('bestmove e2e4'), { code: 'incomplete_engine_output' });
  }
  const collector = new IterationCollector(job({ multi_pv: 2 })); collector.receive(line(3, 1, 'e2e4'));
  assert.throws(() => collector.receive('bestmove e2e4'), { code: 'incomplete_engine_output' });
  assert.throws(() => new IterationCollector(job()).receive('info depth 2 pv e2e4'), { code: 'invalid_engine_output' });
});
test('B_terminal_position', () => {
  assert.throws(() => prepareJob(job({ fen: '7k/6Q1/6K1/8/8/8/8/8 b - - 0 1' })), { code: 'terminal_position' });
  assert.throws(() => prepareJob(job({ max_depth: null, max_nodes: null })), { code: 'unsupported_search_limits' });
});
test('Stockfish digest preserves full history, order, nulls and clocks; v1 remains strict', () => {
  assert.equal(stockfishInputDigest(sample), sample.input_digest);
  assert.equal(stockfishInputDigest({...sample, execution_generation:2}), sample.input_digest);
  for (const changes of [{moves:['e2e4']}, {fen:sample.fen.replace('0 1','1 1')}, {max_nodes:null}]) assert.notEqual(stockfishInputDigest({...sample,...changes}), sample.input_digest);
  for (const changes of [{contract_version:true}, {wdl:true}, {max_depth:'20'}, {execution_generation:2**53}, {moves:['e2e4\n']}]) assert.throws(()=>validatePayload('StockfishJob',{...sample,...changes}));
  assert.equal(canonicalJson({b:null,a:[]}), '{"a":[],"b":null}');
});

test('malformed bestmove and illegal ponder cannot turn a coherent iteration into success',()=>{
  for(const bestmove of ['bestmove e2e4 unexpected','bestmove e2e4 ponder e2e5','bestmove 0000']){
    const collector=new IterationCollector(job());collector.receive('info depth 2 score cp 10 pv e2e4');
    assert.throws(()=>collector.receive(bestmove),{code:'invalid_engine_output'});
  }
});
