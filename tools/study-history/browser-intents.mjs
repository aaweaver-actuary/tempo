/** Pure preparation of retained browser facts. No storage, network, or settlement authority. */
const states = new Set(['pending', 'conflicted', 'committed', 'discarded']);
const immutableFields = ['key', 'kind', 'attemptId', 'requestId', 'endpoint', 'revision', 'createdAt', 'payload'];
const authorityRegistryKey = 'tempo-learning-authorities-v1';
const cutoverKey = 'tempo-study-intents-cutover-v1';

export class BrowserHistoryError extends Error {
  constructor(reason) { super(reason); this.name = 'BrowserHistoryError'; }
}
const fail = reason => { throw new BrowserHistoryError(reason); };
function object(value, reason) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype)
    fail(reason);
}
function text(value, reason) { if (typeof value !== 'string' || !value.length) fail(reason); }
function integer(value, minimum, reason) { if (!Number.isSafeInteger(value) || value < minimum) fail(reason); }
function json(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value)) return;
  if (typeof value !== 'object' || seen.has(value)) fail('Facts must be finite, acyclic JSON values.');
  if (!Array.isArray(value)) object(value, 'Facts must be plain JSON objects.');
  if (Object.values(Object.getOwnPropertyDescriptors(value)).some(descriptor => descriptor.get || descriptor.set)) fail('Facts must not contain dynamic accessors.');
  const keys = Reflect.ownKeys(value);
  if (keys.some(key => typeof key !== 'string') || keys.some(key => key !== 'length' && !Object.prototype.propertyIsEnumerable.call(value, key))) fail('Facts must contain only enumerable JSON fields.');
  if (Array.isArray(value) && (Object.keys(value).length !== value.length || Object.keys(value).some((key, index) => key !== String(index)))) fail('Facts must not contain sparse arrays or hidden array fields.');
  seen.add(value);
  for (const item of Object.values(value)) json(item, seen);
  seen.delete(value);
}
const copy = value => JSON.parse(JSON.stringify(value));
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function fields(value, required, optional, reason) {
  object(value, reason);
  if (required.some(key => !Object.hasOwn(value, key)) || Object.keys(value).some(key => !required.includes(key) && !optional.includes(key))) fail(reason);
}
function rows(value) { if (!Array.isArray(value)) fail('Every observed store must have an explicit row list.'); return value; }
function validateIntent(record, workspace) {
  object(record, 'An intent must be a complete original record.');
  for (const field of ['workspace', 'key', 'kind', 'attemptId', 'requestId', 'endpoint', 'createdAt']) text(record[field], 'An intent has incomplete original identity.');
  if (record.workspace !== workspace) fail('An intent belongs to another original storage authority.');
  if (!Object.hasOwn(record, 'payload')) fail('An intent is missing its original payload.');
  if (!states.has(record.state)) fail('An intent has an unknown delivery state.');
  integer(record.sequence, 1, 'An intent has an invalid original sequence.');
  integer(record.version, 1, 'An intent has an invalid record version.');
  if (Object.hasOwn(record, 'revision')) integer(record.revision, 1, 'An intent has an invalid original revision.');
  if (Object.hasOwn(record, 'claim')) {
    fields(record.claim, ['owner', 'token', 'expiresAt'], [], 'An intent has an incomplete retained claim.');
    text(record.claim.owner, 'A retained claim is missing its original owner.');
    text(record.claim.token, 'A retained claim is missing its original token.');
    if (!Number.isFinite(record.claim.expiresAt)) fail('A retained claim has an invalid expiry.');
  }
}
function approvedSource(source, approval) {
  // Clone first, so caller-owned getters/changes cannot replace checked facts after approval.
  json(source);
  const retained = copy(source);
  fields(retained, ['schema_version', 'unresolved', 'authority', 'storageWorkspace', 'recordKeys', 'intentDatabase', 'localStorage', 'offlineDatabases'], [], 'Expected the complete browser source packet.');
  if (retained.schema_version !== 1 || !Array.isArray(retained.unresolved) || retained.unresolved.length) fail('Only resolved browser source version 1 is accepted.');
  object(retained.recordKeys, 'Reviewed normalized record keys are required.');
  object(retained.authority, 'Original authority evidence is required.');
  text(retained.storageWorkspace, 'Original browser storage workspace is required.');
  if (typeof approval !== 'function' || approval(copy(retained)) !== true) fail('Original source authority is not approved.');
  return retained;
}

/**
 * Convert explicitly supplied v1 intent/v2 offline rows into #388 browser_intents.
 * `approval` is the composition root's synchronous authority qualification capability.
 * It must bind the complete source packet, including rows, keys, and original authority.
 */
export function convertBrowserIntents(source, { approval } = {}) {
  const retained = approvedSource(source, approval);
  const { authority, storageWorkspace, recordKeys, intentDatabase, localStorage, offlineDatabases } = retained;
  fields(intentDatabase, ['name', 'version', 'intents', 'metadata', 'teaching'], [], 'Expected all original intent object stores.');
  if (intentDatabase.name !== 'tempo-study-intents' || intentDatabase.version !== 1) fail('Unmapped browser intent schema.');
  const result = [], seen = new Set(), sourceIdentities = new Set();
  const append = (identity, data) => {
    const coordinate = canonical(identity);
    if (sourceIdentities.has(coordinate)) fail('Duplicate original browser identity; no records were deduplicated.');
    sourceIdentities.add(coordinate);
    const key = Object.hasOwn(recordKeys, coordinate) ? recordKeys[coordinate] : undefined;
    text(key, 'Original browser identity has no reviewed normalized record key.');
    if (seen.has(key)) fail('Duplicate original browser identity; no records were deduplicated.');
    seen.add(key);
    result.push({ key, data: { authority: copy(authority), ...data } });
  };
  // Retain an authority row even for an observed-empty database/device.
  append(['browser-authority', storageWorkspace], { storageWorkspace });
  const intentSequences = new Set();
  for (const intent of rows(intentDatabase.intents)) {
    validateIntent(intent, storageWorkspace);
    if (intentSequences.has(intent.sequence)) fail('Duplicate original intent sequence.');
    intentSequences.add(intent.sequence);
    append(['indexeddb', intentDatabase.name, 'intents', intent.workspace, intent.key], { intent });
  }
  for (const metadata of rows(intentDatabase.metadata)) {
    object(metadata, 'Metadata must retain its original row.');
    if (metadata.workspace !== storageWorkspace) fail('Metadata belongs to another original storage authority.');
    text(metadata.key, 'Metadata is missing its original key.');
    if (!Object.hasOwn(metadata, 'value')) fail('Metadata is missing its original value.');
    append(['indexeddb', intentDatabase.name, 'metadata', metadata.workspace, metadata.key], { metadata });
  }
  for (const teaching of rows(intentDatabase.teaching)) {
    object(teaching, 'Teaching facts must retain their original row.');
    if (teaching.workspace !== storageWorkspace) fail('Teaching belongs to another original storage authority.');
    text(teaching.key, 'Teaching is missing its original key.'); text(teaching.cardId, 'Teaching is missing its original card identity.');
    integer(teaching.revision, 1, 'Teaching is missing its original revision.');
    if (!['seen', 'first-clean'].includes(teaching.kind)) fail('Unknown browser teaching fact.');
    if (Object.hasOwn(teaching, 'ply')) integer(teaching.ply, 0, 'Teaching has an invalid original ply.');
    append(['indexeddb', intentDatabase.name, 'teaching', teaching.workspace, teaching.key], { teaching });
  }
  const capturedLocalKeys = new Set();
  for (const entry of rows(localStorage)) {
    fields(entry, ['key', 'value'], [], 'Local storage must retain an exact key and raw value or observed absence.');
    text(entry.key, 'Local storage is missing its original key.');
    if (entry.key === 'tempo-learning-connection-v1') fail('Session credentials must not enter history fixtures.');
    if (!(entry.key === authorityRegistryKey || entry.key === cutoverKey || /^tempo-(?:pending-(?:training|teaching|endgame|burial|tactic-capture-v1$|card-delete-v1$)|bury-(?:operation|identity)|seen-moves|first-clean-passes)/.test(entry.key)))
      fail('Unmapped retained local-storage key.');
    if (entry.value !== null && typeof entry.value !== 'string') fail('Retain the exact raw local-storage value.');
    capturedLocalKeys.add(entry.key);
    append(['localStorage', storageWorkspace, entry.key], { cold: entry });
  }
  if (![authorityRegistryKey, cutoverKey].every(key => capturedLocalKeys.has(key))) fail('Authority registry and cutover acknowledgement need explicit value or observed absence.');
  const databases = new Set();
  for (const database of rows(offlineDatabases)) {
    fields(database, ['name', 'version', 'training', 'opening_attempts', 'opening_events'], [], 'Expected all original offline object stores.');
    text(database.name, 'An offline database needs its original name.');
    if (database.version !== 2 || !(database.name === 'tempo-offline-training' || database.name.startsWith('tempo-offline-training:'))) fail('Unmapped offline database schema.');
    if (databases.has(database.name)) fail('Duplicate original offline database.');
    databases.add(database.name);
    // Empty databases and explicit versions are facts too.
    append(['indexeddb', database.name, 'schema'], { offlineDatabase: { name: database.name, version: database.version } });
    for (const training of rows(database.training)) {
      // training uses an out-of-line IndexedDB key. getAll() alone cannot prove it.
      fields(training, ['key', 'value'], [], 'Prepared training requires its observed out-of-line key.');
      if (training.key !== 'prepared-daily-queue') fail('Unmapped prepared-training key.');
      object(training.value, 'Prepared training must retain its complete original value.');
      for (const field of ['localDate', 'preparedAt']) text(training.value[field], 'Prepared training is incomplete.');
      rows(training.value.cards);
      for (const attempt of rows(training.value.attempts)) {
        object(attempt, 'An offline attempt must retain its complete original value.');
        for (const field of ['cardId', 'completedAt']) text(attempt[field], 'An offline attempt is incomplete.');
        for (const field of ['localEntryId', 'expectedReviewId', 'expectedRevision']) if (!Number.isSafeInteger(attempt[field])) fail('An offline attempt has an invalid original identity or revision.');
        if (!['again', 'correct'].includes(attempt.outcome) || typeof attempt.guided !== 'boolean') fail('An offline attempt lacks its original assessment.');
      }
      if (!Number.isSafeInteger(training.value.nextTemporaryId)) fail('Prepared training has an invalid temporary identity.');
      append(['indexeddb', database.name, 'training', training.key], { training });
    }
    const attempts = new Set();
    for (const attempt of rows(database.opening_attempts)) {
      object(attempt, 'An opening attempt must retain its complete original value.');
      text(attempt.attempt_id, 'An opening attempt lacks original identity.');
      attempts.add(attempt.attempt_id);
      append(['indexeddb', database.name, 'opening_attempts', attempt.attempt_id], { openingAttempt: attempt });
    }
    for (const event of rows(database.opening_events)) {
      object(event, 'An opening event must retain its complete original value.');
      text(event.attempt_id, 'An opening event lacks original attempt identity.');
      integer(event.sequence, 1, 'An opening event lacks original sequence.');
      if (!attempts.has(event.attempt_id)) fail('An opening event has no retained original attempt.');
      append(['indexeddb', database.name, 'opening_events', event.attempt_id, event.sequence], { openingEvent: event });
    }
  }
  if (Object.keys(recordKeys).some(key => !sourceIdentities.has(key))) fail('A reviewed browser identity is absent from the supplied source.');
  return { browser_intents: result.sort((left, right) => left.key < right.key ? -1 : left.key > right.key ? 1 : 0) };
}

/** Receipt lookup instructions only. This cannot authorize a send, settlement, claim, or cutover. */
export function planOriginalAuthorityReplay(source, { approval } = {}) {
  const normalized = convertBrowserIntents(source, { approval });
  const plans = normalized.browser_intents.filter(row => row.data.intent).map(({ key, data: { authority, intent } }) => {
    const envelope = Object.fromEntries(immutableFields.filter(field => Object.hasOwn(intent, field)).map(field => [field, copy(intent[field])]));
    const base = { key, authority, storageWorkspace: intent.workspace, envelope };
    if (intent.state === 'committed' || intent.state === 'discarded') return { ...base, action: 'retain_terminal', state: intent.state };
    if (intent.state === 'conflicted') return { ...base, action: 'blocked', reason: 'original_intent_conflicted' };
    if (['legacy-time-unavailable', 'teaching-fact'].includes(intent.createdAt)) return { ...base, action: 'blocked', reason: 'original_time_unverified' };
    return { ...base, action: 'lookup_original_receipt', requestId: intent.requestId, attemptId: intent.attemptId };
  });
  for (const { key, data } of normalized.browser_intents) {
    if (data.training?.value.attempts.some(attempt => attempt.serverAcknowledged !== true) || data.openingAttempt && data.openingAttempt.delivery_state !== 'complete')
      plans.push({ key, authority: data.authority, action: 'blocked', reason: 'offline_replay_requires_owner_contract' });
    if (data.cold && ![authorityRegistryKey, cutoverKey].includes(data.cold.key) && data.cold.value !== null && data.cold.value !== '[]')
      plans.push({ key, authority: data.authority, action: 'blocked', reason: 'legacy_replay_requires_owner_contract' });
  }
  return plans;
}
