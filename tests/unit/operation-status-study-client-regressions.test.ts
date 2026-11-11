import { expect, it, vi } from 'vitest';
import { createStudyClient, type StudyClientStorage } from '../../app/lib/study-connection';
import { confirmOperationResponse, PendingOperationError, readOperationResponse, retryBlockedOperation } from '../../app/lib/operation-status';

function storage(): StudyClientStorage {
  let registry: string | null = null;
  return { sessionConnectionAvailable: () => false, readSessionConnection: () => null,
    writeSessionConnection: () => {}, removeSessionConnection: () => {},
    readAuthorityRegistry: () => registry, writeAuthorityRegistry: value => { registry = value; },
    readRetainedStudyData: async () => ({ databases: { 'tempo-study-intents': {} }, legacy: {} }) };
}
const bootstrap = (workspace: string) => ({ schema_version:1, workspace_id:workspace, timezone:'UTC', local_date:'2026-11-01',
  settings:{ study_only_mode:false, new_cards_per_day:1, study_new_per_day:1, tactics_new_per_day:1,
    include_defensive_cards_in_daily_stack:true, light_first_interval_days:1 } });

it('operation_receipts_use_supplied_clients_not_global_default', async () => {
  const calls: Request[] = [];
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init); calls.push(request);
    if (request.url.endsWith('/api/study/bootstrap')) return Response.json(bootstrap(request.headers.get('X-Tempo-Workspace')!));
    return Response.json({ operation_id:'original:id', state:'complete', response:{ saved:true } });
  };
  const a = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  const b = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  await a.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  await b.connectStudy('https://b.example', 'beta', 'b'.repeat(40));
  const defaultFetch = vi.spyOn(globalThis, 'fetch').mockImplementation(() => { throw new Error('global fetch used'); });
  try {
    for (const [client, origin, token, workspace] of [[a, 'https://a.example', 'a', 'alpha'], [b, 'https://b.example', 'b', 'beta']] as const) {
      const response = await readOperationResponse('original:id', { client });
      expect(await response.json()).toEqual({ saved:true });
      const request = calls.at(-1)!;
      expect(request.url).toBe(`${origin}/api/operations/original%3Aid`);
      expect(request.headers.get('Authorization')).toBe(`Bearer ${token.repeat(40)}`);
      expect(request.headers.get('X-Tempo-Workspace')).toBe(workspace);
    }
    expect(defaultFetch).not.toHaveBeenCalled();
    await expect(readOperationResponse('original:id', { client:a, baseUrl:'https://b.example' })).rejects.toThrow('unavailable');
  } finally { defaultFetch.mockRestore(); }
});

it('retry_and_receipt_keep_original_client_generation_and_operation_identity', async () => {
  const calls: string[] = [];
  let release!: (response:Response) => void;
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init); calls.push(request.url);
    if (request.url.endsWith('/api/study/bootstrap')) return Response.json(bootstrap(request.headers.get('X-Tempo-Workspace')!));
    if (request.url.endsWith('/retry')) return new Promise(resolve => { release = resolve; });
    return Response.json({ operation_id:'frozen', state:'complete', response:{ saved:true } });
  };
  const client = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  const pending = retryBlockedOperation('frozen', { client });
  await client.connectStudy('https://b.example', 'beta', 'b'.repeat(40));
  release(new Response(null, { status:202 }));
  await expect(pending).rejects.toThrow('authority changed');
  expect(calls.filter(url => url.includes('/api/operations/'))).toEqual(['https://a.example/api/operations/frozen/retry']);
});

it('default_receipt_path_uses_the_single_composed_client', async () => {
  const { defaultStudyClient } = await import('../../app/lib/study-connection');
  const request = vi.spyOn(defaultStudyClient, 'learningFetch').mockResolvedValue(
    Response.json({ operation_id:'default-original', state:'complete', response:{ saved:true } }));
  try {
    const response = await readOperationResponse('default-original');
    expect(request).toHaveBeenCalledWith(`${defaultStudyClient.origin}/api/operations/default-original`, undefined);
    expect(await response.json()).toEqual({ saved:true });
  } finally { request.mockRestore(); }
});

it('supplied_independent_client_rejects_a_different_receipt_identity', async () => {
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    return request.url.endsWith('/api/study/bootstrap') ? Response.json(bootstrap('alpha'))
      : Response.json({ operation_id:'different', state:'complete', response:{ saved:true } });
  };
  const client = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  await expect(readOperationResponse('original', { client })).rejects.toMatchObject({ name:'PendingOperationError', operationId:'original' });
});

it('confirmation_captures_original_client_before_async_202_body_and_refuses_reconnect', async () => {
  const { confirmOperationResponse } = await import('../../app/lib/operation-status');
  const { subscribeOperationStatusChange } = await import('../../app/lib/operation-status-events');
  const urls: string[] = [];
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init); urls.push(request.url);
    return request.url.endsWith('/api/study/bootstrap') ? Response.json(bootstrap(request.headers.get('X-Tempo-Workspace')!))
      : Response.json({ operation_id:'original', state:'complete', response:{ saved:true } });
  };
  const client = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  let finish!: (chunk: Uint8Array) => void;
  const body = new ReadableStream<Uint8Array>({ start(controller) {
    finish = chunk => { controller.enqueue(chunk); controller.close(); };
  } });
  const notice = vi.fn(); const unsubscribe = subscribeOperationStatusChange(notice);
  try {
    const confirmation = confirmOperationResponse(new Response(body, { status:202 }), { client });
    await client.connectStudy('https://b.example', 'beta', 'b'.repeat(40));
    finish(new TextEncoder().encode(JSON.stringify({ operation_id:'original' })));
    await expect(confirmation).rejects.toThrow('authority changed');
    expect(urls.filter(url => url.includes('/api/operations/'))).toEqual([]);
    expect(notice).not.toHaveBeenCalled();
  } finally { unsubscribe(); }
});

it("reports a blocked browser operation without losing its original identity", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
    state: "blocked", last_error: { message: "transaction budget exhausted" },
  })));
  await expect(confirmOperationResponse(Response.json(
    { operation_id: "original-id", state: "queued" }, { status: 202 },
  ))).rejects.toMatchObject({
    operationId: "original-id", blocked: true,
    message: expect.stringContaining("transaction budget exhausted"),
  } satisfies Partial<PendingOperationError>);
  vi.unstubAllGlobals();
});

it('receipt_body_reconnect_cannot_publish_a_complete_old_authority_result', async () => {
  let finish!: (chunk: Uint8Array) => void;
  let started!: () => void;
  const bodyStarted = new Promise<void>(resolve => { started = resolve; });
  const transport: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith('/api/study/bootstrap')) return Response.json(bootstrap(request.headers.get('X-Tempo-Workspace')!));
    return new Response(new ReadableStream<Uint8Array>({ start(controller) {
      finish = chunk => { controller.enqueue(chunk); controller.close(); }; started();
    } }));
  };
  const client = createStudyClient({ independent:true, baseUrl:'', storage:storage(), transport });
  await client.connectStudy('https://a.example', 'alpha', 'a'.repeat(40));
  const pending = readOperationResponse('original', { client });
  await bodyStarted;
  await client.connectStudy('https://b.example', 'beta', 'b'.repeat(40));
  finish(new TextEncoder().encode(JSON.stringify({ operation_id:'original', state:'complete', response:null })));
  await expect(pending).rejects.toThrow('authority changed');
});
