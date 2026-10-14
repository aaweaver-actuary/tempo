// Bounded stdin/stdout inference; no API client, job claim or database access.
import { createInterface } from 'node:readline';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { analyzeMaiaPosition } from './maia-inference.mjs';
const files = ['scripts/maia-inference.mjs','app/lib/all_moves_maia3.json','app/lib/all_moves_maia3_reversed.json',
  ...Array.from({length:6}, (_, index) => `public/maia3/parts/part-${String(index).padStart(2,'0')}`)];
const checksums = {};
for (const file of files) checksums[file] = createHash('sha256').update(await readFile(new URL(`../${file}`,import.meta.url))).digest('hex');
const provenance = {checksums, node:process.version, runtime:JSON.parse(await readFile(new URL('../node_modules/onnxruntime-web/package.json',import.meta.url))).version};
for await (const line of createInterface({input:process.stdin})) {
  try {
    const request = JSON.parse(line);
    if (request.operation === 'identity') { console.log(JSON.stringify({provenance})); continue; }
    if (!Number.isFinite(request.elo) || request.elo <= 0) throw new Error('Invalid Maia rating');
    const moves = await analyzeMaiaPosition(request.fen, request.elo);
    console.log(JSON.stringify({id:request.id,moves,provenance}));
  } catch (error) { console.log(JSON.stringify({error:String(error)})); }
}
