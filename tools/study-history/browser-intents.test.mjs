import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BrowserHistoryError, convertBrowserIntents, planOriginalAuthorityReplay } from './browser-intents.mjs';

const workspace = 'https://source.example.invalid:study:original-workspace';
const coordinate = (...parts) => JSON.stringify(parts);
function source() {
  return {
    schema_version: 1, unresolved: [],
    authority: { sourceFingerprint: 'synthetic-only', origin: 'https://source.example.invalid' },
    storageWorkspace: workspace,
    recordKeys: {
      [coordinate('browser-authority', workspace)]: 'approved-device',
      [coordinate('indexeddb', 'tempo-study-intents', 'intents', workspace, 'review:original')]: 'approved-review-envelope',
      [coordinate('indexeddb', 'tempo-study-intents', 'metadata', workspace, 'legacy-import')]: 'approved-checkpoint',
      [coordinate('indexeddb', 'tempo-study-intents', 'teaching', workspace, 'first-clean:card')]: 'approved-browser-fact',
      [coordinate('localStorage', workspace, 'tempo-learning-authorities-v1')]: 'approved-registry',
      [coordinate('localStorage', workspace, 'tempo-study-intents-cutover-v1')]: 'approved-cutover-observation',
      [coordinate('localStorage', workspace, 'tempo-pending-training-reviews-v1')]: 'approved-cold-legacy',
    },
    intentDatabase: { name: 'tempo-study-intents', version: 1,
      intents: [{ workspace, key: 'review:original', kind: 'review', attemptId: 'original', requestId: 'review-attempt:original', endpoint: '/api/cards/card/review', revision: 3, createdAt: '2030-01-01T12:00:00Z', payload: { attemptId: 'original', outcome: 'again', expectedRevision: 3, completedAt: '2030-01-01T12:00:00Z' }, sequence: 7, state: 'pending', version: 4, data: { deliveryStarted: true }, claim: { owner: 'original-tab', token: 'old-claim', expiresAt: 123 }, futureField: { remains: [null, false, 7] } }],
      metadata: [{ workspace, key: 'legacy-import', value: { complete: true, sourcesRetained: true } }],
      teaching: [{ workspace, key: 'first-clean:card', cardId: 'card', revision: 3, kind: 'first-clean' }],
    },
    localStorage: [
      { key: 'tempo-learning-authorities-v1', value: '{"original":"registry bytes"}' },
      { key: 'tempo-study-intents-cutover-v1', value: null },
      { key: 'tempo-pending-training-reviews-v1', value: '{malformed but retained' },
    ], offlineDatabases: [],
  };
}
const approved = { approval: () => true };
const intent = packet => packet.intentDatabase.intents[0];
const fail = packet => assert.throws(() => convertBrowserIntents(packet, approved), BrowserHistoryError);

// Synthetic contract tests: no database/browser/network capture and no live acceptance.
test('retains the entire original envelope, delivery state, claim, metadata, teaching and malformed cold data', () => {
  const packet = source(), before = structuredClone(packet);
  const result = convertBrowserIntents(packet, approved);
  assert.deepEqual(result.browser_intents.find(row => row.key === 'approved-review-envelope').data.intent, intent(packet));
  assert.equal(result.browser_intents.find(row => row.key === 'approved-cold-legacy').data.cold.value, '{malformed but retained');
  assert.deepEqual(result.browser_intents.find(row => row.key === 'approved-cutover-observation').data.cold, packet.localStorage[1]);
  assert.equal(result.browser_intents.length, 7);
  assert.deepEqual(packet, before);
  result.browser_intents[0].data.authority.origin = 'changed';
  assert.deepEqual(packet, before);
});

test('approval receives and must bind the complete packet, including original values and reviewed keys', () => {
  const packet = source(); let checked;
  convertBrowserIntents(packet, { approval: value => { checked = structuredClone(value); value.intentDatabase.intents[0].payload.outcome = 'changed'; return true; } });
  assert.deepEqual(checked, packet);
  assert.equal(intent(packet).payload.outcome, 'again');
  assert.throws(() => convertBrowserIntents(packet), /not approved/);
  assert.throws(() => convertBrowserIntents(packet, { approval: () => Promise.resolve(true) }), /not approved/);
  assert.throws(() => convertBrowserIntents(packet, { approval: () => 'true' }), /not approved/);
});

test('unknown source version or unresolved mapping fails before any output', () => {
  for (const modify of [packet => { packet.schema_version = 2; }, packet => { packet.unresolved.push('missing-original-authority'); }, packet => { delete packet.unresolved; }]) {
    const packet = source(); modify(packet); fail(packet);
  }
});

test('missing explicit object store, observed acknowledgement, or reviewed key fails closed', () => {
  const missingStore = source(); delete missingStore.intentDatabase.metadata; fail(missingStore);
  const missingAck = source(); missingAck.localStorage.splice(1, 1); fail(missingAck);
  const missingKey = source(); delete missingKey.recordKeys[coordinate('indexeddb', 'tempo-study-intents', 'intents', workspace, 'review:original')]; fail(missingKey);
  const extraKey = source(); extraKey.recordKeys.unknown = 'unobserved'; fail(extraKey);
});

test('source identity is never relabelled to a target workspace', () => {
  const packet = source(); intent(packet).workspace = 'https://target.example.invalid:study:target'; fail(packet);
  assert.equal(intent(packet).workspace, 'https://target.example.invalid:study:target');
});

test('duplicate source keys and distinct identities sharing a reviewed normalized key are conflicts', () => {
  const packet = source(); packet.intentDatabase.intents.push(structuredClone(intent(packet))); fail(packet);
  const alias = source(); alias.recordKeys[coordinate('localStorage', workspace, 'tempo-study-intents-cutover-v1')] = 'approved-registry'; fail(alias);
});

test('a changed answer under one immutable identity cannot pass a source-bound approval', () => {
  const packet = source(); const original = JSON.stringify(packet);
  intent(packet).payload.outcome = 'correct';
  assert.throws(() => convertBrowserIntents(packet, { approval: value => JSON.stringify(value) === original }), /not approved/);
});

test('pending and ambiguous delivery always require original-authority receipt lookup first', () => {
  const packet = source(); intent(packet).receipt = { httpStatus: 202 }; intent(packet).claim.expiresAt = 999999999999;
  const [plan] = planOriginalAuthorityReplay(packet, approved);
  assert.equal(plan.action, 'lookup_original_receipt');
  assert.deepEqual(plan.authority, packet.authority);
  assert.equal(plan.storageWorkspace, workspace);
  assert.deepEqual(plan.envelope.payload, intent(packet).payload);
  assert.equal(plan.envelope.requestId, intent(packet).requestId);
  assert.equal(Object.hasOwn(plan.envelope, 'claim'), false);
  assert.equal(Object.hasOwn(plan.envelope, 'data'), false);
  assert.equal(Object.hasOwn(plan.envelope, 'receipt'), false);
});

test('terminal tombstones remain terminal on repeated preparation', () => {
  for (const state of ['committed', 'discarded']) {
    const packet = source(); intent(packet).state = state;
    assert.deepEqual(convertBrowserIntents(packet, approved), convertBrowserIntents(packet, approved));
    assert.equal(planOriginalAuthorityReplay(packet, approved)[0].action, 'retain_terminal');
    assert.equal(intent(packet).state, state);
  }
});

test('conflicted and unavailable-time envelopes remain blocked without a replacement answer', () => {
  const conflict = source(); intent(conflict).state = 'conflicted';
  assert.equal(planOriginalAuthorityReplay(conflict, approved)[0].reason, 'original_intent_conflicted');
  for (const sentinel of ['legacy-time-unavailable', 'teaching-fact']) {
    const packet = source(); intent(packet).createdAt = sentinel;
    assert.equal(planOriginalAuthorityReplay(packet, approved)[0].reason, 'original_time_unverified');
    assert.equal(intent(packet).createdAt, sentinel);
  }
});

test('invalid field types and non-JSON payloads are never coerced or dropped', () => {
  for (const modify of [packet => { intent(packet).revision = '3'; }, packet => { intent(packet).sequence = 0; }, packet => { intent(packet).version = 1.5; }, packet => { intent(packet).state = 'successful'; }, packet => { intent(packet).payload = undefined; }, packet => { intent(packet).payload = { value: Infinity }; }, packet => { intent(packet).payload = { value: new Date() }; }]) {
    const packet = source(); modify(packet); fail(packet);
  }
});

test('session credentials and unknown retained keys are blocked', () => {
  for (const key of ['tempo-learning-connection-v1', 'unmapped-retained-key']) {
    const packet = source(); packet.localStorage.push({ key, value: 'private' }); fail(packet);
  }
});

test('output order and keys do not depend on input collection order', () => {
  const packet = source(), reversed = structuredClone(packet);
  reversed.localStorage.reverse();
  assert.deepEqual(convertBrowserIntents(packet, approved), convertBrowserIntents(reversed, approved));
});

function withOffline() {
  const packet = source();
  packet.offlineDatabases.push({ name: 'tempo-offline-training', version: 2,
    training: [{ key: 'prepared-daily-queue', value: { localDate: '2030-01-01', preparedAt: '2030-01-01T10:00:00Z', cards: [{ id: 'physical-string-card', originalTransport: true }], attempts: [{ localEntryId: -3, parentLocalEntryId: -2, cardId: 'physical-string-card', outcome: 'again', guided: false, completedAt: '2030-01-01T11:00:00Z', expectedReviewId: 8, expectedRevision: 3, answer: { original: ['e2e4'] }, assessment: { unknown: true }, serverAcknowledged: false }], nextTemporaryId: -4 } }],
    opening_attempts: [{ attempt_id: 'journal-original', manifest: { original: 'unchanged' }, owner_session_id: 'tab-original', final_sequence: 3, delivery_state: 'retained', retention_reason: 'local_storage_quota', delivery: { checkpoint: { original: 'frozen' }, operationKey: 'original-operation' }, local_capture_gap: 'quota' }],
    opening_events: [{ attempt_id: 'journal-original', sequence: 3, kind: 'first_response', originalUnknownField: true }],
  });
  for (const [parts, key] of [
    [['indexeddb', 'tempo-offline-training', 'schema'], 'approved-offline-schema'],
    [['indexeddb', 'tempo-offline-training', 'training', 'prepared-daily-queue'], 'approved-offline-queue'],
    [['indexeddb', 'tempo-offline-training', 'opening_attempts', 'journal-original'], 'approved-journal'],
    [['indexeddb', 'tempo-offline-training', 'opening_events', 'journal-original', 3], 'approved-event'],
  ]) packet.recordKeys[coordinate(...parts)] = key;
  return packet;
}

test('prepared answers, temporary/string identities, journal gaps, frozen delivery and exact events are retained', () => {
  const packet = withOffline(), before = structuredClone(packet);
  const rows = convertBrowserIntents(packet, approved).browser_intents;
  assert.deepEqual(rows.find(row => row.key === 'approved-offline-queue').data.training, packet.offlineDatabases[0].training[0]);
  assert.deepEqual(rows.find(row => row.key === 'approved-journal').data.openingAttempt, packet.offlineDatabases[0].opening_attempts[0]);
  assert.deepEqual(rows.find(row => row.key === 'approved-event').data.openingEvent, packet.offlineDatabases[0].opening_events[0]);
  assert.deepEqual(packet, before);
});

test('getAll-only training values cannot fabricate their missing out-of-line key', () => {
  const packet = withOffline(); packet.offlineDatabases[0].training = [packet.offlineDatabases[0].training[0].value]; fail(packet);
});

test('unmapped offline versions, omitted stores, duplicate keys and orphan events fail closed', () => {
  for (const modify of [packet => { packet.offlineDatabases[0].version = 1; }, packet => { delete packet.offlineDatabases[0].opening_events; }, packet => { packet.offlineDatabases[0].opening_events.push(structuredClone(packet.offlineDatabases[0].opening_events[0])); }, packet => { packet.offlineDatabases[0].opening_attempts = []; }]) {
    const packet = withOffline(); modify(packet); fail(packet);
  }
});

test('unmapped legacy and offline replay stays explicitly blocked while original bytes remain retained', () => {
  const packet = withOffline();
  const plans = planOriginalAuthorityReplay(packet, approved);
  assert.equal(plans.find(plan => plan.key === 'approved-cold-legacy').reason, 'legacy_replay_requires_owner_contract');
  assert.equal(plans.find(plan => plan.key === 'approved-offline-queue').reason, 'offline_replay_requires_owner_contract');
  assert.equal(plans.find(plan => plan.key === 'approved-journal').reason, 'offline_replay_requires_owner_contract');
  assert.equal(plans.some(plan => plan.action === 'send' || plan.action === 'settle'), false);
});

test('sparse arrays and symbol-valued fields cannot be silently changed by JSON copying', () => {
  const sparse = source(); intent(sparse).payload = Array(1); fail(sparse);
  const symbol = source(); intent(symbol).payload = { [Symbol('hidden')]: 'retained?' }; fail(symbol);
});

test('dynamic getters and malformed offline attempts cannot change facts after validation', () => {
  const dynamic = source(); Object.defineProperty(intent(dynamic).payload, 'dynamic', { enumerable: true, get: () => 'changing' }); fail(dynamic);
  const malformed = withOffline(); malformed.offlineDatabases[0].training[0].value.attempts = [null]; fail(malformed);
});
