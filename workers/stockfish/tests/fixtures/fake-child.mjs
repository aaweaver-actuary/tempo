// Deterministic lifecycle fixture, never accepted as real engine evidence.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { canonicalJson } from '../../wire.mjs';
import { prepareJob } from '../../core.mjs';
const descriptor = JSON.parse(readFileSync(new URL('../../engine-descriptor.json', import.meta.url)));
const identity = { engine_version: 'stockfish-19-smallnet:sha256:' + createHash('sha256').update(canonicalJson(descriptor)).digest('hex'), runtime_name: 'fixture', runtime_version: '1', implementation_version: 'fixture-only' };
setTimeout(() => process.send({ type: 'ready', identity }), Number(process.argv[2] ?? 0));
process.on('disconnect', () => process.exit(0));
process.on('message', message => {
  if (message.type !== 'search') return;
  const { job, token } = message;
  process.send({ type: 'started', token });
  if (job.max_nodes === 999999) return;
  if (job.max_nodes === 666) { setTimeout(() => process.exit(7), 5); return; }
  const prepared = prepareJob(job);
  setTimeout(() => process.send({ type: 'result', token, variations: prepared.roots.slice(0, prepared.multiPv).map((move, index) => ({ rank: index + 1, depth: job.max_depth ?? 1, moves: [move], score: { kind: 'centipawns', value: 20 - index }, score_perspective: 'side_to_move' })), metrics: { nodes: 42, engine_time_ms: 1 } }), job.max_nodes === 2 ? 100 : 5);
});
