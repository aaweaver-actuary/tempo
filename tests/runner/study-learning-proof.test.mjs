import assert from 'node:assert/strict';
import { test } from 'node:test';
import { proofTarget, proofEndpoint, observeComposedIngress, confirmComposedReview } from '../../scripts/prove-study-learning.mjs';

const input = { mode: 'composed-ingress', frontend: 'http://127.0.0.1:4187', learning: 'http://127.0.0.1:4187',
  workspace: 'disposable-workspace', scenario: 'normal' };
const target = proofTarget(input);
const body = { attempt_id: 'attempt-original', queue_entry_id: 7, expected_revision: 1, outcome: 'correct', guided: false };
const original = { origin: target.origin, method: 'POST', path: '/api/cards/card-a/review', key: 'review-attempt:attempt-original', body };
const receipt = { operation_id: original.key, state: 'complete', response: { persisted: true, review_id: 17 } };
const request = (path, method = 'GET', origin = target.origin) => ({ url: () => origin + path, method: () => method,
  headers: () => ({ 'idempotency-key': original.key }), postData: () => method === 'POST' ? JSON.stringify(body) : null });

function fixture() {
  return { evidence: { origin: target.origin, browser_requests: [
    { origin: target.origin, method: 'GET', path: '/api/study/bootstrap' },
    { origin: target.origin, method: 'GET', path: '/api/queue/window' },
    { origin: target.origin, method: 'POST', path: original.path }], fault: null, fault_dropped: false, failure: null,
    database_verification: 'required-external' },
  retained: [{ kind: 'review', requestId: original.key, attemptId: body.attempt_id, state: 'committed',
    workspace: `${target.origin}:study:${target.workspace}`, endpoint: original.path },
  { kind: 'review-delivery', requestId: original.key, payload: { body: structuredClone(body) } }],
  traffic: [structuredClone(original)] };
}

test('Study proof default preserves workspace-a and a real random-port native proxy', async () => {
  assert.deepEqual(proofTarget({}), { mode: 'native', workspace: 'workspace-a' });
  assert.deepEqual(proofTarget({ mode: 'native', workspace: 'ignored' }), proofTarget({}));
  let observed;
  const { proxy, endpoint } = await proofEndpoint(proofTarget({}), (request, response) => {
    observed = request.url; response.end('native-forwarding-boundary');
  });
  try {
    assert.match(endpoint, /^http:\/\/127\.0\.0\.1:[1-9]\d*$/);
    assert.equal(await (await fetch(endpoint + '/api/study/bootstrap')).text(), 'native-forwarding-boundary');
    assert.equal(observed, '/api/study/bootstrap');
  } finally { proxy.closeAllConnections(); await new Promise(resolve => proxy.close(resolve)); }
});

test('Study composed proof explicitly selects the supplied ingress without opening a proxy', async () => {
  assert.deepEqual(await proofEndpoint(target, () => assert.fail('Native forwarding must not run')), { endpoint: input.frontend });
  assert.equal(target.workspace, input.workspace);
  assert.equal(proofTarget({ ...input, frontend: input.frontend + '/' }).origin, input.frontend);
  assert.equal(proofTarget({ ...input, scenario: 'lost-response' }).mode, 'composed-ingress');
});

test('Study composed proof rejects ambiguous modes scenarios origins and workspace bindings', () => {
  for (const patch of [{ mode: 'composition' }, { scenario: 'before-commit' }, { workspace: undefined }, { workspace: '' },
    { workspace: ' another ' }, { workspace: 'x'.repeat(201) }, { workspace: 'bad\nworkspace' },
    { learning: 'http://127.0.0.1:8001' }, { frontend: 'http://localhost:4187' }, { frontend: input.frontend + '/study' },
    { frontend: input.frontend + '?origin=other' }, { frontend: 'http://user:secret@127.0.0.1:4187' },
    { frontend: 'https://example.com' }]) assert.throws(() => proofTarget({ ...input, ...patch }));
});

test('Study composed normal observation is passive and records actual escaped browser requests', async () => {
  let observe;
  const traffic = [];
  const evidence = await observeComposedIngress({ on: (event, listener) => { assert.equal(event, 'request'); observe = listener; },
    route: () => assert.fail('Normal must use the browser network directly') }, target, 'normal', traffic);
  observe(request('/api/study/bootstrap'));
  observe(request(original.path, 'POST'));
  observe(request('/api/queue/window', 'GET', 'http://127.0.0.1:8001'));
  assert.deepEqual(traffic[1], original);
  assert.equal(evidence.browser_requests[2].origin, 'http://127.0.0.1:8001');
  assert.equal(evidence.failure, 'Browser API request escaped ingress');
});

test('Study composed lost response reads one real ingress reply before aborting and then allows receipt recovery', async () => {
  let routeHandler;
  const evidence = await observeComposedIngress({ on() {}, route: async (pattern, handler) => {
    assert.equal(pattern, '**/api/**'); routeHandler = handler;
  } }, target, 'lost-response', []);
  const actions = [];
  const route = { request: () => request(original.path, 'POST'), fetch: async options => {
    assert.deepEqual(options, { maxRedirects: 0 }); actions.push('ingress');
    return { ok: () => true, status: () => 200, body: async () => { actions.push('body'); return Buffer.from(JSON.stringify(receipt)); } };
  }, abort: async reason => { assert.equal(reason, 'failed'); actions.push('lost'); },
  continue: async () => actions.push('continue') };
  await routeHandler(route);
  assert.deepEqual(actions, ['ingress', 'body', 'lost']);
  assert.equal(evidence.fault.operation_id, original.key);
  assert.equal(evidence.fault_dropped, true);
  await routeHandler({ ...route, request: () => request('/api/operations/' + original.key) });
  await routeHandler(route);
  assert.deepEqual(actions, ['ingress', 'body', 'lost', 'continue', 'continue']);
});

test('Study composed lost response rejects HTTP failure redirects empty bodies and an off-ingress review', async () => {
  for (const outcome of ['http-failure', 'empty-body', 'network-failure', 'escaped']) {
    let handler;
    const evidence = await observeComposedIngress({ on() {}, route: async (_, callback) => { handler = callback; } }, target, 'lost-response', []);
    let aborted = false;
    await handler({ request: () => request(original.path, 'POST', outcome === 'escaped' ? 'http://127.0.0.1:8001' : target.origin),
      fetch: async () => {
        if (outcome === 'escaped') assert.fail('An escaped review must never be forwarded');
        if (outcome === 'network-failure') throw new Error('connection closed');
        return { ok: () => outcome !== 'http-failure', status: () => outcome === 'http-failure' ? 302 : 200,
          body: async () => Buffer.from(outcome === 'empty-body' ? '' : '{}') };
      }, abort: async () => { aborted = true; } });
    assert.equal(aborted, true);
    assert.equal(evidence.fault, null);
    assert.equal(evidence.fault_dropped, false);
    assert.ok(evidence.failure);
  }
});

test('Study composed proof independently reads the original HTTP receipt and leaves database qualification external', async () => {
  const { evidence, retained, traffic } = fixture();
  assert.deepEqual(await confirmComposedReview(target, 'normal', evidence, traffic, retained, async (url, options) => {
    assert.equal(url, target.origin + '/api/operations/' + encodeURIComponent(original.key));
    assert.deepEqual(options, { redirect: 'error' });
    return { status: 200, json: async () => receipt };
  }), receipt);
  assert.equal(evidence.database_verification, 'required-external');
});

test('Study composed proof rejects proxy observations missing browser reads and changed original envelopes', async () => {
  for (const alter of [f => { f.evidence.browser_requests = []; },
    f => { f.evidence.browser_requests[0].origin = 'http://127.0.0.1:8001'; },
    f => { f.evidence.browser_requests.pop(); }, f => { f.traffic[0].origin = 'http://127.0.0.1:8001'; },
    f => { f.traffic.push(structuredClone(original)); }, f => { f.retained[0].state = 'pending'; },
    f => { f.retained[0].workspace = 'another-workspace'; }, f => { f.retained[0].attemptId = 'replacement'; },
    f => { f.retained[0].endpoint = '/api/cards/another/review'; },
    f => { f.retained[1].payload.body.outcome = 'again'; }]) {
    const state = fixture(); alter(state);
    await assert.rejects(confirmComposedReview(target, 'normal', state.evidence, state.traffic, state.retained,
      () => assert.fail('Invalid observations must not reach receipt verification')));
  }
});

test('Study composed proof rejects unrelated pending failed or nonpersisted HTTP receipts', async () => {
  for (const change of [{ operation_id: 'replacement' }, { state: 'queued' }, { state: 'failed' },
    { response: { persisted: false, review_id: 17 } }, { response: { persisted: true, review_id: null } }]) {
    const { evidence, retained, traffic } = fixture();
    await assert.rejects(confirmComposedReview(target, 'normal', evidence, traffic, retained,
      async () => ({ status: 200, json: async () => ({ ...receipt, ...change }) })));
  }
});

test('Study composed proof requires a completed original-operation fault for lost response', async () => {
  const { evidence, retained, traffic } = fixture();
  const read = async () => ({ status: 200, json: async () => receipt });
  await assert.rejects(confirmComposedReview(target, 'lost-response', evidence, traffic, retained, read));
  evidence.fault = { location: 'browser-route-after-ingress', operation_id: original.key, upstream_status: 200, upstream_bytes: 100 };
  evidence.fault_dropped = true;
  assert.deepEqual(await confirmComposedReview(target, 'lost-response', evidence, traffic, retained, read), receipt);
  evidence.fault.operation_id = 'replacement';
  await assert.rejects(confirmComposedReview(target, 'lost-response', evidence, traffic, retained, read));
});
