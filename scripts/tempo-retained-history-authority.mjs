// Unselected retained-history v1 binding guard. Owner observations are required;
// this module neither creates a fence nor proves the authenticity of evidence.
import { isDeepStrictEqual } from 'node:util';
import { isAbsolute } from 'node:path';

const text = x => typeof x === 'string' && x.trim().length > 0;
const digest = x => /^sha256:[0-9a-f]{64}$/.test(x ?? '');
const image = x => /^(?:sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64})$/.test(x ?? '');
const snapshot = value => { const copy = structuredClone(value); const freeze = item => { if (item && typeof item === 'object') { Object.values(item).forEach(freeze); Object.freeze(item); } }; freeze(copy); return copy; };
const families = ['browser','cards_history','content','games','issued','position','queue','tactics'];
const exactFamilies = values => Array.isArray(values) && isDeepStrictEqual([...values].sort(), families);
const shape = x => x && text(x.authority) && text(x.store) && text(x.schema_evidence) && Array.isArray(x.primary_key) && x.primary_key.length > 0 && x.primary_key.every(p => Array.isArray(p) && p.length === 2 && p.every(text));
const manifestShape = m => Array.isArray(m?.source_inventory) && m.source_inventory.length > 0 && m.source_inventory.every(i => shape(i?.shape) && Number.isSafeInteger(i.expected_count) && i.expected_count >= 0 && digest(i.expected_digest) && text(i.completeness_evidence))
  && Array.isArray(m.target_shapes) && m.target_shapes.length > 0 && m.target_shapes.every(shape)
  && exactFamilies(m.family_inventory) && Array.isArray(m.family_evidence) && m.family_evidence.every(e => Array.isArray(e) && e.length === 2 && text(e[1])) && exactFamilies(m.family_evidence.map(e => e[0]))
  && text(m.study_timezone) && text(m.scheduler_identity) && Array.isArray(m.facts) && m.facts.every(f => f && typeof f === 'object' && families.includes(f.family) && f.source && text(f.source.authority) && text(f.source.store) && Array.isArray(f.source.primary_key) && f.source.primary_key.length > 0 && f.source.primary_key.every(p => text(p.name) && text(p.codec) && typeof p.value === 'string') && text(f.codec) && typeof f.original_base64 === 'string' && digest(f.original_digest) && Array.isArray(f.references) && typeof f.required === 'boolean' && ['EXACT','TRANSLATE','ARCHIVE','BLOCK'].includes(f.disposition) && text(f.target_owner) && ['target_key','translation_proof','archive_readback','blocker'].every(k => Object.hasOwn(f,k))
    && f.disposition !== 'BLOCK' && !(f.required && f.disposition === 'ARCHIVE')
    && (!['EXACT','TRANSLATE'].includes(f.disposition) || f.target_key !== null)
    && (f.disposition !== 'TRANSLATE' || text(f.translation_proof))
    && (f.disposition !== 'ARCHIVE' || text(f.archive_readback)))
  && m.source_inventory.every(i => m.facts.filter(f => f.source.authority === i.shape.authority && f.source.store === i.shape.store).length === i.expected_count)
  && m.facts.every(f => m.source_inventory.some(i => f.source.authority === i.shape.authority && f.source.store === i.shape.store));
function validIngress(r, c) { try {
  const u = new URL(c.ingress.origin), host = u.hostname.replace(/^\[|\]$/g, '');
  const port = u.port || (u.protocol === 'https:' ? '443' : '80');
  const loopback = h => h === '127.0.0.1' || h === '::1';
  return ['http:', 'https:'].includes(u.protocol) && u.origin === c.ingress.origin && !u.username && !u.password
    && loopback(host) && c.ingress.hosts.every(loopback) && c.ingress.hosts.includes(host)
    && Array.isArray(r.ports) && r.ports.length === 1
    && r.ports.every(p => p && p.service === 'study-web' && p.host === host && p.port === port
      && p.target === 80 && (p.protocol === undefined || p.protocol === 'tcp'));
} catch { return false; } }

const fail = reason => { throw new Error(`retained_history_${reason}`); };

// Observations are returned by separate owning readers while the original lock
// is held. References alone and disposable fixtures do not prove authority.
export function verifyRetainedHistoryBindings(registration, observed) {
  const r = registration, c = r?.cutover, m = observed?.manifest;
  const v = observed?.verification, f = observed?.fence, a = observed?.activation;
  if (r?.version !== 2 || c?.mode !== 'retained-history-v1' || c.contractVersion !== 1
      || !/^[0-9a-f]{40}$/.test(c.revision ?? '') || !digest(c.schema)
      || !/^[0-9a-f]{40}$/.test(c.sourceSha ?? '') || !digest(c.sourceSchema)
      || !['sourceAuthority','targetAuthority','transferId','manifestDigest','generation','lockIdentity','workspace','graphSourceReference','graphReceipt'].every(k => text(c[k]))
      || !digest(c.manifestDigest) || c.sourceAuthority === c.targetAuthority
      || !c.images || !isDeepStrictEqual(Object.keys(c.images).sort(), ['learning','postgres','study-web'])
      || !Object.values(c.images).every(image) || !c.volumes || !Object.keys(c.volumes).length
      || !['root','envFile'].every(k => text(r[k]) && isAbsolute(r[k]))
      || !Array.isArray(c.composeFiles) || !c.composeFiles.length || !c.composeFiles.every(p => text(p) && isAbsolute(p))
      || !text(r.postgresVolumeKey) || !Number.isInteger(r.postgresMajor) || r.postgresMajor < 1
      || !image(r.postgresImage) || r.postgresImage !== c.images.postgres
      || !Object.values(c.volumes).every(v => v && text(v.name) && v.external === true)
      || new Set(Object.values(c.volumes).map(v => v.name)).size !== Object.keys(c.volumes).length
      || !text(c.database?.name) || c.database.volume !== c.volumes[r.postgresVolumeKey]?.name
      || !Array.isArray(c.ingress?.hosts) || !c.ingress.hosts.length || !c.ingress.hosts.every(text)
      || new Set(c.ingress.hosts).size !== c.ingress.hosts.length || !validIngress(r, c)
      || !isDeepStrictEqual(r.volumes, c.volumes) || !isDeepStrictEqual(r.composeFiles, c.composeFiles)
      || r.webUrl !== c.ingress.origin || !['daemonId','context','project'].every(k => text(r[k]))) fail('registration_incomplete');
  if (observed?.scope !== 'observed' || !text(observed.evidenceId) || !isDeepStrictEqual(observed.registration, r)
      || observed.manifestDigest !== c.manifestDigest || m?.version !== 1 || m.scope !== 'complete'
      || !manifestShape(m) || m.capture !== 'final_fenced' || m.transfer_id !== c.transferId || m.source_authority !== c.sourceAuthority
      || m.target_authority !== c.targetAuthority || m.source_sha !== c.sourceSha || m.source_schema !== c.sourceSchema
      || m.target_schema !== c.schema || !text(m.source_watermark) || observed.currentSourceWatermark !== m.source_watermark
      || !text(m.fence_id) || !Array.isArray(m.unresolved) || m.unresolved.length) fail('manifest_unverified');
  if (v?.version !== 1 || v.status !== 'verified' || !Array.isArray(v.blockers) || v.blockers.length
      || !text(v.verifier_evidence) || v.transfer_id !== c.transferId || v.manifest_digest !== c.manifestDigest
      || v.target_generation !== c.generation) fail('verification_mismatch');
  if (f?.version !== 1 || f.transfer_id !== c.transferId || f.fence_id !== m.fence_id
      || f.source_authority !== c.sourceAuthority || f.target_authority !== c.targetAuthority
      || f.source_watermark !== m.source_watermark || f.lock_identity !== c.lockIdentity
      || !digest(f.denied_writer_inventory_digest)
      || !['stale_session_denial','credential_denial','restart_denial','evidence'].every(k => text(f[k]))) fail('fence_mismatch');
  if (a?.version !== 1 || a.transfer_id !== c.transferId || a.manifest_digest !== c.manifestDigest
      || a.fence_id !== f.fence_id || a.target_generation !== c.generation || a.target_authority !== c.targetAuthority
      || !text(a.activation_id) || a.target_acceptance !== 'accepted') fail('activation_mismatch');
  if (!isDeepStrictEqual(observed.artifacts, {revision:c.revision, schema:c.schema, images:c.images,
    volumes:c.volumes, ingress:c.ingress, workspace:c.workspace, composeFiles:c.composeFiles,
    graphSourceReference:c.graphSourceReference, graphReceipt:c.graphReceipt, database:c.database,
    ports:r.ports, postgresImage:r.postgresImage, postgresMajor:r.postgresMajor, postgresVolumeKey:r.postgresVolumeKey,
    root:r.root, envFile:r.envFile, daemonId:r.daemonId, context:r.context, project:r.project})) fail('artifacts_mismatch');
  return true;
}

// The caller supplies the existing original transition lock and actual readers.
// No action is permitted unless both observations agree under the retained lock.
export async function withVerifiedRetainedHistory({registration, observe, withLock}, action) {
  if (typeof observe !== 'function' || typeof withLock !== 'function' || typeof action !== 'function') fail('provider_missing');
  const selected = snapshot(registration);
  const unchanged = () => { if (!isDeepStrictEqual(registration, selected)) fail('registration_changed'); };
  const before = snapshot(await observe());
  unchanged();
  verifyRetainedHistoryBindings(selected, before);
  let calls = 0, completed = false, open = true;
  try {
    const result = await withLock(snapshot({lockIdentity: selected.cutover.lockIdentity}), async () => {
      if (!open || ++calls !== 1) fail('lock_callback_invalid');
      unchanged();
      const current = snapshot(await observe());
      unchanged();
      if (!open) fail('lock_callback_invalid');
      verifyRetainedHistoryBindings(selected, current);
      if (!isDeepStrictEqual(before, current)) fail('observation_changed');
      const value = await action(current);
      if (!open) fail('lock_callback_invalid');
      unchanged();
      completed = true;
      return value;
    });
    unchanged();
    if (calls !== 1 || !completed) fail('lock_callback_invalid');
    return result;
  } finally { open = false; }
}
