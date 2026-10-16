// Installed-runtime transport fixture. Its child is deterministic, not Stockfish evidence.
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
const runtime = process.argv[2];
const { StockfishWorker } = await import(pathToFileURL(join(runtime, 'worker.mjs')));
const { serveProtocol } = await import(pathToFileURL(join(runtime, 'cli.mjs')));
const childSource = `
process.on('disconnect', () => process.exit(0));
process.send({type:'ready',identity:{engine_version:process.argv[1],runtime_name:'fixture',runtime_version:'1',implementation_version:'fixture-only'}});
process.on('message', ({token,job}) => {
 process.send({type:'started',token});
 if(job.max_nodes===999999)return;
 setTimeout(() => process.send({type:'result',token,variations:[{rank:1,depth:job.max_depth,moves:['e2e4'],score:{kind:'centipawns',value:20},score_perspective:'side_to_move'}]}), 5);
});`;
const worker = new StockfishWorker({
  resourceInspector: async () => ({ memory_enforced: true, memory_limit_bytes: 536870912, cpu_limit: 1, swap_limit_bytes: 0 }),
  childFactory: () => spawn(process.execPath, ['--input-type=module', '-e', childSource, process.argv[3]], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] }),
});
worker.on('diagnostic', event => process.stderr.write(JSON.stringify(event) + '\n'));
await worker.start();
const protocol = serveProtocol(worker, { input: process.stdin, output: process.stdout, diagnostic: event => process.stderr.write(JSON.stringify(event) + '\n') });
process.exitCode = (await protocol.done).success ? 0 : 1;
