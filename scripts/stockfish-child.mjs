import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import StockfishFactory from '../public/engines/sf_19_smallnet.js';
const assets = resolve(import.meta.dirname, '../public/engines');
const engine = await StockfishFactory({ locateFile: name => resolve(assets, name),
  listen: text => process.send?.({ text }) });
engine.setNnueBuffer(new Uint8Array(await readFile(resolve(assets, 'nn-61e7af4bb97d.nnue'))));
engine.onError = error => process.send?.({ error: String(error) });
process.on('message', ({ command }) => engine.uci(command));
process.send?.({ ready: true });
