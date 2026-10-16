// Deterministic offline measurement. Run serially after qualification completes.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';

const argument = name => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : undefined;
const packageRoot = resolve(argument('--package-root') ?? import.meta.dirname);
const positions = JSON.parse(await readFile(argument('--positions') ?? new URL('./fixtures/positions.json', import.meta.url), 'utf8'));
const rounds = Number(argument('--rounds') ?? 5);
if (!Number.isSafeInteger(rounds) || rounds < 1 || rounds > 100) throw new Error('Benchmark rounds must be 1–100');
const { createModel } = await import(pathToFileURL(join(packageRoot, 'model.mjs')).href);
const { inspectResources, validateResourceBudget } = await import(pathToFileURL(join(packageRoot, 'resources.mjs')).href);
const { validatePosition } = await import(pathToFileURL(join(packageRoot, 'core.mjs')).href);
const selected = ['opening-white', 'opening-black', 'black-castling', 'black-en-passant', 'black-promotion'].map(name => positions.find(position => position.name === name));
const resources = await inspectResources();
validateResourceBudget(null, resources);
const report = { schema_version: 1, measurement_scope: 'model_session', node: process.version, platform: process.platform, architecture: process.arch, concurrency: 1, resources, positions: selected.map(position => ({ name: position.name, rating: position.rating })), inference_count: 0, warm_count: 0, exit_status: null };
const cpuStarted = process.cpuUsage(), started = performance.now();
let model;
try {
  const coldStarted = performance.now();
  model = await createModel();
  report.identity = model.identity;
  report.cold_initialization_ms = performance.now() - coldStarted;
  async function infer(position) {
    const legalMoves = position.moves ?? validatePosition(position.fen).legalMoves;
    const job = { model_id: model.identity.model_id, model_version: model.identity.model_version, fen: position.fen, rating: position.rating, legal_moves: legalMoves };
    const sampleStarted = performance.now();
    const predictions = await model.infer(job);
    if (JSON.stringify(predictions.map(item => item.move).sort()) !== JSON.stringify([...job.legal_moves].sort())) throw new Error('Incomplete benchmark distribution');
    report.inference_count++;
    return performance.now() - sampleStarted;
  }
  report.first_inference_ms = await infer(selected[0]);
  const samples = [];
  for (let round = 0; round < rounds; round++) for (const position of selected) samples.push(await infer(position));
  const sorted = samples.toSorted((left, right) => left - right);
  report.warm_count = samples.length;
  report.warm_samples_ms = samples;
  report.warm_p50_ms = sorted[Math.ceil(sorted.length * 0.5) - 1];
  report.warm_p95_ms = sorted[Math.ceil(sorted.length * 0.95) - 1];
  report.warm_max_ms = sorted.at(-1);
  const cpu = process.cpuUsage(cpuStarted);
  report.cpu_user_ms = cpu.user / 1000; report.cpu_system_ms = cpu.system / 1000;
  report.peak_process_rss_bytes = process.resourceUsage().maxRSS * 1024;
  for (const [name, file] of [['cpu_quota', '/sys/fs/cgroup/cpu.max'], ['cgroup_peak_bytes', '/sys/fs/cgroup/memory.peak'], ['swap_limit_bytes', '/sys/fs/cgroup/memory.swap.max']]) {
    try { report[name] = (await readFile(file, 'utf8')).trim(); } catch { report[name] = null; }
  }
  report.exit_status = 0;
} catch (error) { report.error = error.stack; report.exit_status = 1; process.exitCode = 1; }
finally {
  if (model) { await model.dispose(); report.model_session_disposed = true; }
  report.wall_ms = performance.now() - started;
  const output = JSON.stringify(report, null, 2) + '\n';
  if (argument('--out')) await writeFile(resolve(argument('--out')), output);
  process.stdout.write(output);
}
