import { test } from 'node:test';
import assert from 'node:assert/strict';
import { IDBFactory } from 'fake-indexeddb';
import { openIntentStore, IntentIdentityConflict } from '@tempo/browser-intents';
const envelope = (key = 'review:a') => ({ key, kind: 'review', attemptId: key, requestId: key, endpoint: '/review', createdAt: '2026-10-15T16:00:00Z', revision: 1, payload: { outcome: 'again', guided: true } });
async function stores(run) { const indexedDB = new IDBFactory(); const first = await openIntentStore({ workspace: 'one', indexedDB }); const second = await openIntentStore({ workspace: 'one', indexedDB }); try { await run(first, second, indexedDB); } finally { first.close(); second.close(); } }

test('issue221_two_tabs_retain_independent_intents_in_both_write_orders', async () => {
  for (const order of [[0, 1], [1, 0]]) await stores(async (first, second) => {
    const clients = [first, second]; await Promise.all(order.map(index => clients[index].put(envelope(`review:${index}`))));
    assert.deepEqual((await second.list()).map(record => record.key).sort(), ['review:0', 'review:1']);
  });
});
test('issue221_immutable_identity_rejects_grade_time_revision_and_payload_changes', async () => stores(async first => {
  const original = envelope(); await first.put(original);
  for (const changed of [{ payload: { outcome: 'correct', guided: true } }, { createdAt: 'later' }, { revision: 2 }, { requestId: 'other' }])
    await assert.rejects(first.put({ ...original, ...changed }), IntentIdentityConflict);
  assert.deepEqual((await first.get(original.key)).payload, original.payload);
}));
test('issue221_concurrent_claims_have_one_winner_and_old_release_cannot_clear_new_claim', async () => stores(async (first, second) => {
  await first.put(envelope()); const claims = await Promise.all([first.claim('review:a', 'first', 100, 10), second.claim('review:a', 'second', 100, 10)]);
  assert.equal(claims.filter(Boolean).length, 1); const old = claims.find(Boolean);
  const renewed = await second.claim('review:a', 'second', 111, 10); assert.ok(renewed);
  await first.release('review:a', old); assert.equal((await second.get('review:a')).claim.token, renewed);
}));
test('issue221_repeated_import_never_resurrects_committed_intent', async () => stores(async (first, second) => {
  await first.put(envelope()); await first.update('review:a', current => ({ ...current, state: 'committed', receipt: { persisted: true } }));
  await second.put(envelope()); assert.equal((await second.get('review:a')).state, 'committed');
  await assert.rejects(second.update('review:a', current => ({ ...current, state: 'pending' })));
  assert.equal((await first.get('review:a')).state, 'committed');
}));
test('issue221_aborted_update_retains_original_and_checkpoint_survives_reopen', async () => stores(async (first, second) => {
  await first.put(envelope()); await first.checkpoint('migration', { imported: ['a'], complete: false });
  await assert.rejects(first.update('review:a', () => { throw new Error('interrupted'); }));
  assert.equal((await second.get('review:a')).state, 'pending'); assert.deepEqual(await second.checkpoint('migration'), { imported: ['a'], complete: false });
}));
test('issue221_teaching_append_does_not_enumerate_or_rewrite_history', async () => stores(async (first, second) => {
  for (let ply = 0; ply < 30; ply++) await first.addTeachingFact({ key: `card:1:${ply}`, cardId: 'card', revision: 1, ply, kind: 'seen' });
  await second.addTeachingFact({ key: 'other:1:0', cardId: 'other', revision: 1, ply: 0, kind: 'seen' });
  await first.addTeachingFact({ key: 'card:1:1', cardId: 'card', revision: 1, ply: 1, kind: 'seen' });
  assert.equal((await first.teachingFacts('card')).length, 30); assert.equal((await second.teachingFacts('other')).length, 1);
}));
test('issue221_storage_denial_never_returns_a_durable_store', async () => {
  await assert.rejects(openIntentStore({ workspace: 'one', indexedDB: { open() { throw new DOMException('denied', 'SecurityError'); } } }), /Could not open durable study storage/);
});
test('issue221_workspace_keys_cannot_settle_another_workspace', async () => stores(async (first, second, indexedDB) => {
  const other = await openIntentStore({ workspace: 'other', indexedDB });
  try { await first.put(envelope()); assert.equal(await other.get('review:a'), undefined); assert.equal((await second.list(undefined, true)).length, 1); }
  finally { other.close(); }
}));

test('issue221_inflight_insertion_freezes_payload_before_callers_can_change_it', async () => stores(async first => {
  const command = envelope();
  const writing = first.put(command);
  command.payload.outcome = 'correct';
  await writing;
  assert.equal((await first.get('review:a')).payload.outcome, 'again');
}));

test('pr285_legacy_conflict_initial_state_is_atomic_and_existing_mutable_state_wins', async () => stores(async (first, second) => {
  const original = envelope();
  const [left, right] = await Promise.all([first.put(original, { reconciliationSequence: 1 }, { initialState: 'conflicted' }), second.put(original, { reconciliationSequence: 1 }, { initialState: 'conflicted' })]);
  assert.equal(left.state, 'conflicted'); assert.equal(right.sequence, left.sequence);
  await first.update(original.key, current => ({ ...current, state: 'pending', data: { reconciliationSequence: 2, automaticRecoverySuppressed: 'blocked' }, receipt: { sequence: 2 } }));
  const claim = await first.claim(original.key, 'owner', 1, 100);
  const before = await first.get(original.key);
  const repeated = await second.put(original, { reconciliationSequence: 1 }, { initialState: 'conflicted' });
  assert.deepEqual(repeated, before); assert.equal(repeated.claim.token, claim);
  await assert.rejects(second.put({ ...original, payload: { outcome: 'correct' } }, undefined, { initialState: 'conflicted' }), IntentIdentityConflict);
}));
test('pr285_legacy_conflict_reimport_preserves_both_terminal_tombstones', async () => {
  for (const state of ['committed', 'discarded']) await stores(async (first, second) => {
    await first.put(envelope(), undefined, { initialState: 'conflicted' });
    await first.update('review:a', current => ({ ...current, state, receipt: { reason: state } }));
    const before = await first.get('review:a');
    assert.deepEqual(await second.put(envelope(), { old: true }, { initialState: 'conflicted' }), before);
  });
});
test('pr285_practice_generation_reset_and_insertion_are_serialized_in_both_orders', async () => {
  for (const insertionFirst of [true, false]) await stores(async (first, second) => {
    const matches = record => record.kind === 'review';
    await first.put(envelope()); await first.update('review:a', current => ({ ...current, state: 'committed' }));
    assert.equal((await first.boundary('practice', matches)).value, 0);
    if (insertionFirst) {
      await second.put(envelope('review:new'), undefined, { boundary: { key: 'practice', value: 0 } });
      await assert.rejects(first.boundary('practice', matches, 0), /Confirm the saved answer/);
      assert.equal((await first.boundary('practice', matches)).value, 0);
    } else {
      const reset = await first.boundary('practice', matches, 0); assert.equal(reset.value, 1);
      await assert.rejects(second.put(envelope('review:new'), undefined, { boundary: { key: 'practice', value: 0 } }), /Practice changed/);
      assert.equal(await first.get('review:new'), undefined);
      await second.put(envelope('review:new'), undefined, { boundary: { key: 'practice', value: 1 } });
      // Existing immutable evidence remains readable through its old boundary.
      assert.equal((await second.put(envelope(), undefined, { boundary: { key: 'practice', value: 0 } })).state, 'committed');
    }
  });
});
test('pr285_practice_generation_transaction_abort_keeps_answer_and_assessment_together', async () => stores(async (first, second) => {
  const { IDBObjectStore } = await import('fake-indexeddb');
  for (const key of ['review:answer', 'review:assessment']) { await first.put(envelope(key)); await first.update(key, current => ({ ...current, state: 'committed' })); }
  const originalPut = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function(value, ...args) { if (this.name === 'metadata' && value.key === 'practice') throw new DOMException('reset interrupted', 'AbortError'); return originalPut.call(this, value, ...args); };
  try { await assert.rejects(first.boundary('practice', () => true, 0), /reset interrupted/); }
  finally { IDBObjectStore.prototype.put = originalPut; }
  const snapshot = await second.boundary('practice', () => true);
  assert.equal(snapshot.value, 0); assert.equal(snapshot.records.filter(record => record.sequence > snapshot.value).length, 2);
}));
test('pr285_recovery_cursor_stale_compare_cannot_overwrite_newer_progress', async () => stores(async (first, second) => {
  assert.equal(await first.compareCheckpoint('cursor', undefined, 20), true);
  assert.equal(await second.compareCheckpoint('cursor', undefined, 1), false);
  assert.equal(await second.checkpoint('cursor'), 20);
  assert.equal(await first.compareCheckpoint('cursor', 20, 1), true); // Authorized wraparound.
}));
test('pr285_teaching_cross_tab_notification_is_keyed_and_reads_committed_fact', async () => stores(async (first, second) => {
  let notify;
  const event = new Promise(resolve => { notify = resolve; });
  const unsubscribe = second.subscribe(change => { if (change.teachingKeys) notify(change); });
  try {
    const fact = { key: 'first-clean:card', cardId: 'card', revision: 1, kind: 'first-clean' };
    await first.addTeachingFact(fact);
    const change = await event; assert.deepEqual(change.teachingKeys, [fact.key]);
    assert.deepEqual(await second.teachingFact(fact.key), { ...fact, workspace: 'one' });
    assert.equal(await second.teachingFact('missing'), undefined);
  } finally { unsubscribe(); }
}));
