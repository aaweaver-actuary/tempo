// Local web-cache generations are serialized by managedBuild's existing builder lane.
import { constants } from 'node:fs';
import { lstat, mkdir, open, readdir, rename, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

const owner = 'tempo-development-web-cache';
const format = {owner, version: 1};
const marker = '.tempo-web-cache.json';
const generationPattern = /^generation-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const warn = error => console.warn(`[web build cache] ${error.message}`);

function settings(cache) {
  if (cache === false) return;
  if (cache !== undefined && (!cache || typeof cache !== 'object' || Array.isArray(cache))) throw new Error('invalid cache settings; automatic caching disabled');
  const directory = cache && Object.hasOwn(cache, 'directory') ? cache.directory :
    process.env.TEMPO_BUILD_CACHE_DIR ?? join(homedir(), '.cache', 'tempo', 'buildx-web-v1');
  if (directory === '') return;
  const maxBytes = cache && Object.hasOwn(cache, 'maxBytes') ? cache.maxBytes : 4 * 1024 ** 3;
  if (typeof directory !== 'string' || !isAbsolute(directory)) throw new Error('directory must be absolute; automatic caching disabled');
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) throw new Error('maxBytes must be a positive integer; automatic caching disabled');
  return {directory: resolve(directory), maxBytes};
}

function eligible(specification) {
  const web = specification.target?.web;
  if (!web || Object.hasOwn(web, 'cache-from') || Object.hasOwn(web, 'cache-to') ||
      Object.hasOwn(web, 'dockerfile-inline')) return false;
  const context = web.context ?? '.';
  const dockerfile = web.dockerfile ?? 'Dockerfile';
  if (typeof context !== 'string' || typeof dockerfile !== 'string' || /^[a-z][a-z0-9+.-]*:|^git@/i.test(context)) return false;
  return resolve(context, dockerfile) === join(resolve(context), 'Dockerfile.web');
}

async function readJson(path) {
  if (!(await lstat(path)).isFile()) throw new Error('cache metadata is not a regular file');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    if (!(await file.stat()).isFile()) throw new Error('cache metadata is not a regular file');
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}

function matchesFormat(value) { return value?.owner === owner && value.version === 1; }

// Check each component before creating or using it, including existing parents.
async function directoryPath(path, create = false) {
  const parent = dirname(path);
  if (parent !== path) await directoryPath(parent, create);
  if (create) {
    try { await mkdir(path, {mode: 0o700}); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  if (!(await lstat(path)).isDirectory()) throw new Error('cache directory contains a symlink or non-directory');
}

async function prepareRoot(directory) {
  await directoryPath(directory, true);
  if ((await lstat(directory)).mode & 0o077) throw new Error('cache root must be private (0700)');
  const entries = await readdir(directory);
  if (!entries.length) await writeFile(join(directory, marker), JSON.stringify(format), {mode: 0o600, flag: 'wx'});
  if (!entries.length || entries.includes(marker)) {
    if (!matchesFormat(await readJson(join(directory, marker)))) throw new Error('cache root has no matching ownership marker');
  } else throw new Error('nonempty cache root has no matching ownership marker');
}

async function ownedGeneration(directory, name) {
  if (!generationPattern.test(name)) throw new Error('invalid cache generation name');
  const path = join(directory, name);
  await directoryPath(path);
  const metadata = await readJson(join(path, marker));
  if (!matchesFormat(metadata) || metadata.generation !== name) throw new Error('cache generation ownership is unverified');
  return path;
}

async function removeGeneration(directory, name) {
  if (!matchesFormat(await readJson(join(directory, marker)))) throw new Error('cache root ownership changed');
  const path = await ownedGeneration(directory, name);
  // Node rm unlinks contained symlinks; it never recursively follows them.
  await rm(path, {recursive: true});
}

async function currentGeneration(directory) {
  try {
    const metadata = await readJson(join(directory, 'current.json'));
    if (!matchesFormat(metadata) || !generationPattern.test(metadata.generation)) throw new Error('invalid current pointer; abandoned generations retained');
    await ownedGeneration(directory, metadata.generation);
    return {name: metadata.generation, prune: true};
  } catch (error) {
    if (error.code === 'ENOENT') {
      // A missing pointer is distinct from a pointer to a missing generation.
      try { await lstat(join(directory, 'current.json')); }
      catch (missing) { if (missing.code === 'ENOENT') return {prune: true}; throw missing; }
    }
    warn(error);
    return {prune: false};
  }
}

async function generationBytes(path) {
  let bytes = 0;
  for (const name of await readdir(path)) {
    const entry = join(path, name), stat = await lstat(entry);
    if (stat.isDirectory()) bytes += await generationBytes(entry);
    else if (stat.isFile()) bytes += stat.size;
    else throw new Error('cache generation contains a symlink or unsupported file');
  }
  return bytes;
}

async function validateIndex(path) {
  const index = await readJson(join(path, 'index.json'));
  if (index.schemaVersion !== 2 || !Array.isArray(index.manifests) || !index.manifests.length)
    throw new Error('cache OCI index is malformed');
  await directoryPath(join(path, 'blobs', 'sha256'));
  for (const manifest of index.manifests) {
    if (!/^sha256:[0-9a-f]{64}$/.test(manifest.digest) || typeof manifest.mediaType !== 'string' ||
        !manifest.mediaType || !Number.isSafeInteger(manifest.size) || manifest.size < 0)
      throw new Error('cache OCI descriptor is malformed');
    const blob = await lstat(join(path, 'blobs', 'sha256', manifest.digest.slice(7)));
    if (!blob.isFile() || blob.size !== manifest.size) throw new Error('cache OCI manifest blob is unusable');
  }
}

// Bake accepts structured cache entries, avoiding CSV ambiguity in directory names.
/** Execute once, preserving build results/errors. Cache diagnostics never retry a build. */
export async function withWebBuildCache(specification, execute, cache) {
  if (!eligible(specification)) return execute(specification);
  let configuration;
  try { configuration = settings(cache); }
  catch (error) { warn(error); }
  if (!configuration) return execute(specification);
  let current, candidate;
  const cleanup = async () => {
    if (!candidate) return;
    try { await removeGeneration(configuration.directory, candidate); }
    catch (error) { warn(new Error(`candidate cleanup failed: ${error.message}`)); }
  };
  let enriched, cachePaths;
  try {
    const {directory} = configuration;
    await prepareRoot(directory);
    current = await currentGeneration(directory);
    if (current.prune) {
      for (const name of await readdir(directory)) {
        if (!generationPattern.test(name) || name === current.name) continue;
        // UUID-shaped names alone do not authorize deletion of unowned entries.
        try { await removeGeneration(directory, name); }
        catch (error) { warn(new Error(`abandoned generation retained: ${error.message}`)); }
      }
    }
    let source;
    if (current.name) {
      try {
        const path = await ownedGeneration(directory, current.name);
        await validateIndex(path);
        await generationBytes(path);
        source = path;
      } catch (error) { warn(new Error(`cold build: ${error.message}`)); }
    }
    candidate = `generation-${randomUUID()}`;
    const destination = join(directory, candidate);
    await mkdir(destination, {mode: 0o700});
    await writeFile(join(destination, marker), JSON.stringify({...format, generation: candidate}), {mode: 0o600, flag: 'wx'});
    enriched = {...specification, target: {...specification.target, web: {...specification.target.web,
      ...(source ? {'cache-from': [{type: 'local', src: source}]} : {}),
      'cache-to': [{type: 'local', dest: destination, mode: 'max'}]}}};
    cachePaths = {read: source, write: destination};
  } catch (error) {
    warn(new Error(`setup failed; automatic caching disabled: ${error.message}`));
    await cleanup();
    return execute(specification);
  }

  let result;
  try { result = await execute(enriched, cachePaths); }
  catch (error) { await cleanup(); throw error; }

  const {directory, maxBytes} = configuration;
  let pointerTemporary;
  try {
    const path = await ownedGeneration(directory, candidate);
    await validateIndex(path);
    const bytes = await generationBytes(path);
    if (bytes > maxBytes) throw new Error(`candidate is ${bytes} bytes, exceeding retained limit ${maxBytes}; prior cache retained`);
    pointerTemporary = join(directory, `.current-${randomUUID()}.json`);
    await writeFile(pointerTemporary, JSON.stringify({...format, generation: candidate, bytes}), {mode: 0o600, flag: 'wx'});
    await rename(pointerTemporary, join(directory, 'current.json'));
    candidate = undefined; // Publication transfers ownership to the current pointer.
    if (current.name) await removeGeneration(directory, current.name);
  } catch (error) { warn(new Error(`publication/retention failed: ${error.message}`)); }
  finally {
    await cleanup();
    if (pointerTemporary) {
      try { await rm(pointerTemporary, {force: true}); }
      catch (error) { warn(new Error(`metadata cleanup failed: ${error.message}`)); }
    }
  }
  return result;
}
