import { test } from 'node:test';
import assert from 'node:assert/strict';
import { IDBFactory } from 'fake-indexeddb';
import { openIntentStore } from '@tempo/browser-intents';
import { createStudySession } from '@tempo/study-session';
const envelope = (key = 'review:a') => ({ key, kind: 'review', attemptId: key, requestId: key, endpoint: '/review', createdAt: '2026-10-15T16:00:00Z', payload: { outcome: 'again' } });
async function run(deliver, testBody) { const store = await openIntentStore({ workspace: 'session', indexedDB: new IDBFactory() }); const session = createStudySession({ store, learning: { deliver }, requestTimeoutMs: 30 }); session.start('review:a'); try { await testBody(session, store); } finally { session.dispose(); store.close(); } }
test('issue219_pending_transport_never_becomes_committed', async () => run(async () => ({ state: 'pending' }), async (session, store) => {
  await session.submit(envelope()); assert.equal(session.getSnapshot().phase, 'pending'); assert.equal((await store.get('review:a')).state, 'pending');
}));
test('issue219_lost_response_retries_original_immutable_request', async () => {
  const requests = []; await run(async record => { requests.push(record); if (requests.length === 1) throw new Error('lost response'); return { state: 'committed', receipt: { persisted: true } }; }, async (session, store) => {
    await assert.rejects(session.submit(envelope()), /lost response/); await session.retrySave('review:a');
    assert.deepEqual(requests[0].payload, requests[1].payload); assert.equal((await store.get('review:a')).state, 'committed');
  });
});
test('issue186_committed_review_next_load_failure_is_bounded_and_retry_never_resubmits', async () => {
  let submissions = 0; await run(async () => { submissions++; return { state: 'committed', receipt: { persisted: true } }; }, async (session, store) => {
    await session.submit(envelope()); let acquisitions = 0;
    await assert.rejects(session.retryNext('review:a', async () => { acquisitions++; throw new Error('queue unavailable'); }, () => true));
    assert.equal(acquisitions, 2); assert.equal(session.getSnapshot().phase, 'next-load-failed');
    assert.deepEqual(await store.checkpoint('progression:review:a'), { key: 'review:a', pending: true });
    await session.retryNext('review:a', async () => undefined, () => true); assert.equal(submissions, 1);
    assert.equal((await store.checkpoint('progression:review:a')).pending, false);
  });
});
test('issue219_late_settlement_cannot_replace_newer_attempt_state', async () => {
  let release; const held = new Promise(resolve => { release = resolve; });
  await run(async () => { await held; return { state: 'committed', receipt: { persisted: true } }; }, async (session, store) => {
    const submission = session.submit(envelope()); await new Promise(resolve => setTimeout(resolve, 5)); session.start('review:new'); release(); await submission;
    assert.equal(session.getSnapshot().key, 'review:new'); assert.equal(session.getSnapshot().phase, 'active'); assert.equal((await store.get('review:a')).state, 'committed');
  });
});
test('issue219_next_load_timeout_has_two_attempts_and_actionable_failure', async () => run(async () => ({ state: 'committed' }), async session => {
  await session.submit(envelope()); let acquisitions = 0;
  await assert.rejects(session.retryNext('review:a', async () => { acquisitions++; return new Promise(() => {}); }, () => true), /timed out/);
  assert.equal(acquisitions, 2); assert.equal(session.getSnapshot().phase, 'next-load-failed');
}));

test('issue186_installing_next_attempt_resolves_the_original_progression_checkpoint', async () => run(async () => ({ state: 'committed' }), async (session, store) => {
  await session.submit(envelope());
  await session.retryNext('review:a', async () => { session.start('review:next'); }, () => true);
  assert.deepEqual(await store.checkpoint('progression:review:a'), { key: 'review:a', pending: false });
  assert.equal(session.getSnapshot().key, 'review:next');
  assert.equal(session.getSnapshot().phase, 'active');
}));
test('issue219_unexpired_crashed_tab_lease_allows_receipt_confirmation_without_delivery', async () => {
  const store = await openIntentStore({ workspace: 'crash', indexedDB: new IDBFactory() });
  let posts = 0, reads = 0;
  const session = createStudySession({ store, now: () => 100, learning: {
    deliver: async () => { posts++; return { state: 'pending' }; },
    checkReceipt: async original => { reads++; assert.equal(original.requestId, 'review:a'); return { state: 'committed', receipt: { persisted: true } }; },
  } });
  try {
    await store.put(envelope()); await store.claim('review:a', 'closed-tab', 100, 60_000);
    session.start('review:a');
    await session.checkSave('review:a');
    assert.equal(posts, 0); assert.equal(reads, 1); assert.equal((await store.get('review:a')).state, 'committed');
  } finally { session.dispose(); store.close(); }
});
test('issue219_capture_commits_before_deferred_feedback_delivery', async () => run(async () => { throw new Error('delivery must wait'); }, async (session, store) => {
  await session.submit(envelope(), undefined, { deliver: false });
  assert.equal((await store.get('review:a')).state, 'pending');
  assert.equal(session.getSnapshot().phase, 'pending');
}));
