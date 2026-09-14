import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';

const source = new URL('../assets/maia3/', import.meta.url);
const destination = new URL('../public/maia3/maia3_simplified.onnx', import.meta.url);
const parts = (await readdir(source)).filter((name) => name.startsWith('maia3_simplified.onnx.part-')).sort();
const expectedSize = (await Promise.all(parts.map((name) => stat(new URL(name, source))))).reduce((sum, item) => sum + item.size, 0);
try {
  if ((await stat(destination)).size === expectedSize) process.exit(0);
} catch { /* Assemble below. */ }
await mkdir(new URL('../public/maia3/', import.meta.url), { recursive: true });
const chunks = await Promise.all(parts.map((name) => readFile(new URL(name, source))));
await writeFile(destination, Buffer.concat(chunks));
