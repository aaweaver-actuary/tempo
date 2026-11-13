import { expect, it, vi } from 'vitest';
import { createStudyClient, type StudyClientStorage } from '../../app/lib/study-connection';

function storage() {
  let session: string | null = null;
  const registry = new Map<string, { identity: string; origin: string; workspace: string }>();
  const adapter: StudyClientStorage = {
    sessionConnectionAvailable: () => true, readSessionConnection: () => session,
    writeSessionConnection: value => { session = value; }, removeSessionConnection: () => { session = null; },
    readRegisteredAuthorities: () => [...registry.values()], bindAuthority: value => { registry.set(value.identity, value); },
    offlineStoreLocator: identity => identity ? `locator:${identity}` : "local-locator",
    readRetainedStudyData: async () => ({ databases: {}, legacy: {} }),
    inspectRetainedStudyAuthority: async () => ({ facts: [], retained: { databases: {}, legacy: {} } }),
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

it.each(["terminal", "unresolved", "unknown"] as const)("authority policy consumes semantic %s facts without storage-shaped fakes", async disposition => {
  const port = storage();
  port.inspectRetainedStudyAuthority = async () => ({ facts: [{ authority: "https://foreign.example:study:foreign", disposition }],
    retained: { databases: {}, legacy: {} } });
  const client = createStudyClient({ independent: true, baseUrl: "", storage: port, transport: vi.fn() });
  const decision = client.assertStudyAuthority("https://target.example", "target");
  if (disposition === "terminal") await expect(decision).resolves.toEqual({ databases: {}, legacy: {} });
  else await expect(decision).rejects.toMatchObject({ kind: "authority" });
});

it.each(['absent', 'throwing'])('C03 explicit factory import with %s browser globals does not compose browser services', async mode => {
  vi.resetModules();
  const forbidden = () => { throw new Error('browser composition imported'); };
  vi.doMock('../../database/browser/study-connection-storage', forbidden);
  vi.doMock('../../app/lib/study-priority', forbidden);
  vi.doMock('../../app/const', forbidden);
  const names = ['window', 'location', 'localStorage', 'sessionStorage', 'indexedDB'] as const;
  const descriptors = names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)] as const);
  try {
    for (const name of names) {
      if (mode === "absent") Reflect.deleteProperty(globalThis, name);
      else Object.defineProperty(globalThis, name, { configurable: true, get: () => { throw new Error(`read ${name}`); } });
    }
    const factory = await import('../../app/lib/study-client');
    expect('defaultStudyClient' in factory).toBe(false);
    const callbacks = { transport: vi.fn(), resolveBaseHref: vi.fn(() => 'https://local.example/'),
      onAuthenticationRequired: vi.fn(), acquireActivityHold: vi.fn(() => vi.fn()) };
    const client = factory.createStudyClient({ independent: false, baseUrl: '/', storage: storage(), ...callbacks });
    expect(callbacks.resolveBaseHref).not.toHaveBeenCalled();
    expect(callbacks.transport).not.toHaveBeenCalled();
    expect(callbacks.acquireActivityHold).not.toHaveBeenCalled();
    expect(callbacks.onAuthenticationRequired).not.toHaveBeenCalled();
    expect(client.studyWorkspace()).toBe('https://local.example:study');
  } finally {
    for (const [name, descriptor] of descriptors) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else Reflect.deleteProperty(globalThis, name);
    }
    vi.doUnmock('../../database/browser/study-connection-storage');
    vi.doUnmock('../../app/lib/study-priority'); vi.doUnmock('../../app/const');
  }
});

it('C03 explicit activity dependency retains foreground release and background isolation', async () => {
  const { createStudyClient: createExplicit } = await import('../../app/lib/study-client');
  const release = vi.fn(), hold = vi.fn(() => release), auth = vi.fn();
  const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(null));
  const client = createExplicit({ independent: false, baseUrl: 'https://local.example', storage: storage(), transport,
    resolveBaseHref: () => 'https://local.example/', onAuthenticationRequired: auth, acquireActivityHold: hold });
  await client.learningFetch('/api/queue/window');
  expect(hold).toHaveBeenCalledTimes(1); expect(release).toHaveBeenCalledTimes(1);
  await client.learningFetch('/api/queue/window', { headers: { 'X-Tempo-Work-Class': 'background' } });
  expect(hold).toHaveBeenCalledTimes(1);
  transport.mockImplementationOnce(() => { throw new Error('transport failed'); });
  expect(() => client.learningFetch('/api/operations/original')).toThrow('transport failed');
  expect(release).toHaveBeenCalledTimes(2); expect(auth).not.toHaveBeenCalled();
});

it('C03 explicit rejected bootstrap notifies authentication and releases its connection hold', async () => {
  const { createStudyClient: createExplicit } = await import('../../app/lib/study-client');
  const release = vi.fn(), hold = vi.fn(() => release), auth = vi.fn();
  const client = createExplicit({ independent: true, baseUrl: '', storage: storage(),
    transport: vi.fn(async () => new Response(null, { status: 401 })),
    resolveBaseHref: () => 'https://shell.example/', onAuthenticationRequired: auth, acquireActivityHold: hold });
  await expect(client.connectStudy('https://learning.example', 'alpha', 'a'.repeat(40))).rejects.toMatchObject({ kind: 'http' });
  expect(auth).toHaveBeenCalledTimes(1); expect(hold).toHaveBeenCalledTimes(1); expect(release).toHaveBeenCalledTimes(1);
});

it('FE06 semantic registry supplies an opaque locator without browser layout or JSON', async () => {
  const port = storage();
  const client = createStudyClient({ independent: true, baseUrl: '', storage: port,
    transport: vi.fn(async () => Response.json(bootstrap('alpha'))) });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  expect(port.readRegisteredAuthorities()).toEqual([{ identity: 'https://a.example:study:alpha', origin: 'https://a.example', workspace: 'alpha' }]);
  expect(client.studyOfflineDatabaseName()).toBe('locator:https://a.example:study:alpha');
  expect(port.readRegisteredAuthorities()[0]).not.toHaveProperty('offlineDatabase');
});
