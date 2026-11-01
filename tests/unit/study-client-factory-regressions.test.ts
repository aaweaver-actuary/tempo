import { expect, it, vi } from 'vitest';
import { createStudyClient, type StudyClientStorage } from '../../app/lib/study-connection';

function storage() {
  let session: string | null = null, registry: string | null = null;
  const adapter: StudyClientStorage = {
    sessionConnectionAvailable: () => true, readSessionConnection: () => session,
    writeSessionConnection: value => { session = value; }, removeSessionConnection: () => { session = null; },
    readAuthorityRegistry: () => registry, writeAuthorityRegistry: value => { registry = value; },
    readRetainedStudyData: async () => ({ databases: { 'tempo-study-intents': {} }, legacy: {} }),
  };
  return adapter;
}
const bootstrap = (workspace: string) => ({ schema_version:1, workspace_id:workspace, timezone:'UTC', local_date:'2026-11-01',
  settings:{ study_only_mode:false, new_cards_per_day:1, study_new_per_day:1, tactics_new_per_day:1,
    include_defensive_cards_in_daily_stack:true, light_first_interval_days:1 } });

it('two_clients_cannot_cross_authorities_or_credentials', async () => {
  const requests: Request[] = [];
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init); requests.push(request);
    if (request.url.endsWith('/api/study/bootstrap')) return Response.json(bootstrap(request.headers.get('X-Tempo-Workspace')!));
    return Response.json({ ok:true });
  };
  const sharedStorage = storage();
  const one = createStudyClient({ independent:true, baseUrl:'', storage:sharedStorage, transport });
  const two = createStudyClient({ independent:true, baseUrl:'', storage:sharedStorage, transport });
  const keyA = 'a'.repeat(40), keyB = 'b'.repeat(40);
  await Promise.all([one.connectStudy('https://a.example', 'alpha', keyA), two.connectStudy('https://b.example', 'beta', keyB)]);
  const original = new Request('https://a.example/api/cards/card/review', { method:'POST', body:'original body',
    headers:{ 'Idempotency-Key':'original-id', Authorization:'Bearer wrong', 'X-Tempo-Work-Class':'background' } });
  await one.learningFetch(original);
  const delivered = requests.at(-1)!;
  expect(delivered.headers.get('Authorization')).toBe(`Bearer ${keyA}`);
  expect(delivered.headers.get('X-Tempo-Workspace')).toBe('alpha');
  expect(delivered.headers.get('Idempotency-Key')).toBe('original-id');
  expect(delivered.headers.has('X-Tempo-Work-Class')).toBe(false);
  expect(delivered.credentials).toBe('omit'); expect(delivered.redirect).toBe('error'); expect(delivered.cache).toBe('no-store');
  expect(await delivered.text()).toBe('original body');
  await two.learningFetch('https://b.example/api/queue/today');
  expect(requests.at(-1)!.headers.get('Authorization')).toBe(`Bearer ${keyB}`);
  await expect(one.learningFetch('https://b.example/api/queue/today')).rejects.toThrow('unavailable');
  const controller = new AbortController();
  const asset = new Request('https://b.example/assets/x', { method:'POST', body:'asset bytes',
    signal:controller.signal, headers:{ 'X-Caller':'retained' } });
  await one.learningFetch(asset);
  const passthrough = requests.at(-1)!;
  expect(await passthrough.text()).toBe('asset bytes');
  expect(passthrough.headers.get('X-Caller')).toBe('retained');
  expect(passthrough.headers.has('Authorization')).toBe(false);
  expect(passthrough.headers.has('X-Tempo-Workspace')).toBe(false);
  controller.abort(); expect(passthrough.signal.aborted).toBe(true);
  expect(one.studyWorkspace()).toBe('https://a.example:study:alpha');
  expect(two.studyWorkspace()).toBe('https://b.example:study:beta');
  one.disconnectStudy(); expect(two.studyConnection()?.workspace).toBe('beta');
  expect(one.studyConnection()).toBeUndefined();
  expect(sharedStorage.readSessionConnection()).toBeNull();
});

it('superseded_client_response_cannot_publish_authentication_for_new_authority', async () => {
  let complete!: (response: Response) => void;
  const expired = vi.fn();
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith('/api/study/bootstrap')) return Response.json(bootstrap(request.headers.get('X-Tempo-Workspace')!));
    return new Promise(resolve => { complete = resolve; });
  };
  const client = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport, onAuthenticationRequired:expired });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  const old = client.learningFetch('https://a.example/api/queue/today');
  await client.connectStudy('https://b.example', 'beta', 'b'.repeat(40));
  complete(new Response(null, { status:401 })); await old;
  expect(expired).not.toHaveBeenCalled(); expect(client.studyConnection()?.workspace).toBe('beta');
});

it('local_client_preserves_ordinary_transport_arguments', async () => {
  const transport = vi.fn<typeof fetch>(async () => new Response(null));
  const client = createStudyClient({ independent:false, baseUrl:'https://local.example', storage:storage(), transport });
  const input = new Request('https://other.example/ordinary', { headers:{ Authorization:'caller' } });
  const options = { method:'GET' };
  await client.learningFetch(input, options);
  expect(transport).toHaveBeenCalledWith(input, options);
});

it('configured_client_preserves_request_signal_and_explicit_overrides', async () => {
  const captured: Request[] = [];
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init); captured.push(request);
    return request.url.endsWith('/api/study/bootstrap') ? Response.json(bootstrap('alpha')) : new Response(null);
  };
  const client = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  const controller = new AbortController();
  const request = new Request('https://a.example/api/cards/card/review', {
    method:'POST', body:'retained bytes', signal:controller.signal,
    headers:{ 'Idempotency-Key':'original-id', Authorization:'old' },
  });
  await client.learningFetch(request);
  const delivered = captured.at(-1)!;
  expect(await delivered.text()).toBe('retained bytes');
  expect(delivered.headers.get('Idempotency-Key')).toBe('original-id');
  controller.abort(); expect(delivered.signal.aborted).toBe(true);
  const next = new Request('https://a.example/api/cards/card/review', { method:'POST', body:'first', headers:{ 'X-Old':'old' } });
  await client.learningFetch(next, { body:'replacement', headers:{ 'X-New':'new' } });
  expect(await captured.at(-1)!.text()).toBe('replacement');
  expect(captured.at(-1)!.headers.get('X-New')).toBe('new');
  expect(captured.at(-1)!.headers.has('X-Old')).toBe(false);
});
