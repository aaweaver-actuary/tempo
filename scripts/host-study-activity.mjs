import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

/** Registered producers expire unavailable; only explicit clean teardown removes one. */
export async function readHostStudyActivity({directory = process.env.TEMPO_STUDY_ACTIVITY_DIRECTORY ?? join(tmpdir(), 'tempo-study-activity-v1'),
  now = process.hrtime.bigint()} = {}) {
  let entries;
  try { entries = await readdir(directory); }
  catch (error) { return {available: error.code === 'ENOENT', active: false}; }
  let active = false;
  for (const name of entries.filter(name => name.endsWith('.json'))) {
    try {
      const snapshot = JSON.parse(await readFile(join(directory, name), 'utf8'));
      const deadline = value => {
        if (typeof value !== 'string' || !/^\d{1,24}$/.test(value)) throw new Error('Malformed activity deadline');
        return BigInt(value);
      };
      if (snapshot.version !== 1 || name !== `${snapshot.producer}.json` ||
          !snapshot.sessions || Array.isArray(snapshot.sessions) || typeof snapshot.sessions !== 'object')
        return {available: false, active};
      const validUntil = deadline(snapshot.validUntilNs);
      if (validUntil <= now || validUntil > now + 16_000_000_000n) return {available: false, active};
      if (typeof snapshot.pendingRequests !== 'boolean') return {available: false, active};
      active ||= snapshot.pendingRequests;
      for (const [session, expires] of Object.entries(snapshot.sessions)) {
        if (!/^[A-Za-z0-9_-]{1,80}$/.test(session)) return {available: false, active};
        const until = deadline(expires);
        if (until > now + 4_000_000_000n) return {available: false, active};
        active ||= until > now;
      }
    } catch (error) {
      // A concurrent clean shutdown may remove a producer; other errors deny admission.
      if (error.code !== 'ENOENT') return {available: false, active};
    }
  }
  return {available: true, active};
}
