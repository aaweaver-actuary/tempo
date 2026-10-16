import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { canonicalJson, WireError } from './wire.mjs';
import { IterationCollector } from './core.mjs';

export const runtimeFiles = ['index.mjs', 'core.mjs', 'wire.mjs', 'engine.mjs', 'worker.mjs', 'child.mjs', 'resources.mjs', 'cli.mjs', 'engine-descriptor.json'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export async function inspectEngine() {
  const descriptor = JSON.parse(await readFile(new URL('./engine-descriptor.json', import.meta.url), 'utf8'));
  if (process.versions.node !== descriptor.node) throw new WireError('unsupported_runtime', 'Exact Node runtime required');
  for (const [name, expected] of Object.entries(descriptor.assets)) if (hash(await readFile(new URL('./assets/' + name, import.meta.url))) !== expected) throw new WireError('engine_asset_mismatch', 'Engine asset hash mismatch');
  const sources = {};
  for (const name of runtimeFiles) sources[name] = hash(await readFile(new URL('./' + name, import.meta.url)));
  return { engine_version: 'stockfish-19-smallnet:sha256:' + hash(canonicalJson(descriptor)), implementation_version: 'sha256:' + hash(canonicalJson({ sources, node: process.versions.node, ajv: '8.20.0', chess: '1.4.0' })), runtime_name: 'node-stockfish-wasm', runtime_version: process.versions.node, descriptor };
}

export async function createEngine() {
  const identity = await inspectEngine();
  const { default: factory } = await import('./assets/sf_19_smallnet.js');
  let listener = () => {};
  const engine = await factory({ locateFile: name => new URL('./assets/' + name, import.meta.url).pathname, listen: line => listener(line), onError: () => { process.exitCode = 1; process.exit(1); }, print: () => {}, printErr: () => {} });
  engine.setNnueBuffer(new Uint8Array(await readFile(new URL('./assets/nn-61e7af4bb97d.nnue', import.meta.url))));
  const wait = (command, expected, before = () => {}) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new WireError('engine_initialization_timeout', 'UCI handshake timed out')), 10000);
    listener = line => { before(line); if (line === expected) { clearTimeout(timer); resolve(); } };
    engine.uci(command);
  });
  const options = new Set(); let name;
  await wait('uci', 'uciok', line => { if (line.startsWith('id name ')) name = line.slice(8); const option = line.match(/^option name (.+) type /); if (option) options.add(option[1]); });
  if (name !== 'Stockfish 19') throw new WireError('engine_identity_mismatch', 'Unexpected UCI identity');
  for (const [option, value] of Object.entries(identity.descriptor.options)) {
    if (!options.has(option)) throw new WireError('unsupported_engine_option', 'Declared UCI option missing');
    engine.uci(`setoption name ${option} value ${value === '' ? '<empty>' : value}`);
  }
  await wait('isready', 'readyok');
  return { identity, async search(job, started = () => {}) {
    const collector = new IterationCollector(job);
    engine.uci('ucinewgame');
    await wait('isready', 'readyok');
    engine.uci(`setoption name MultiPV value ${collector.prepared.multiPv}`);
    engine.uci(`position fen ${job.fen}${job.moves.length ? ' moves ' + job.moves.join(' ') : ''}`);
    return new Promise((resolve, reject) => {
      listener = line => { try { const variations = collector.receive(line); if (variations) { listener = () => {}; resolve({ variations, metrics: collector.metrics }); } } catch (error) { listener = () => {}; reject(error); } };
      started();
      engine.uci(`go${job.max_depth === null ? '' : ' depth ' + job.max_depth}${job.max_nodes === null ? '' : ' nodes ' + job.max_nodes}${job.search_moves.length ? ' searchmoves ' + job.search_moves.join(' ') : ''}`);
    });
  } };
}
