// @vitest-environment node
import { Chess } from 'chess.js';
import { afterEach, expect, it, vi } from 'vitest';
import { computeStudyTask } from '../../app/lib/study-computation';
import { decodeStudyResult } from '../../app/lib/study-result-decoder';
import { studyReplySchema } from '../../app/domain/schemas';
import { asFenString, asLineId, asRepertoireId, asUciMove } from '../../app/types';

const fen = new Chess().fen();
const line = (id: string, move: string) => ({ id: asLineId(id), repertoireId: asRepertoireId('rep'),
  repertoireName: 'Repertoire', title: id, side: 'white' as const,
  startingFen: asFenString(fen), moves: [asUciMove(move)] });
const queue = { kind: 'queue' as const, payload: { cards: [{ id:'card', queue_entry_id:1,
  start_fen:fen, moves:['e2e4'], content_type:'opening', repertoire_name:'Rep', repertoire_source:'test' }] } };

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it('task_result_decoders_accept_actual_computation_and_preserve_order', () => {
  const cards = computeStudyTask(queue);
  expect(decodeStudyResult(queue, structuredClone(cards))).toEqual(cards);
  const lines = { kind: 'lines' as const, lines: [line('b', 'd2d4'), line('a', 'e2e4')] };
  expect(decodeStudyResult(lines, structuredClone(computeStudyTask(lines))).map(item => item.id)).toEqual(['b', 'a']);
  const index = { kind: 'index' as const, lines: lines.lines };
  expect(decodeStudyResult(index, structuredClone(computeStudyTask(index)))).toEqual(computeStudyTask(index));
  const task = { kind: 'repertoireAlternatives' as const, generation: 3, lines: lines.lines };
  expect(decodeStudyResult(task, structuredClone(computeStudyTask(task)))).toEqual(computeStudyTask(task));
  const deck = { kind:'deck' as const, deckId:'deck', records:[2, 1].map(DeckPosition => ({ DeckId:'deck', DeckPosition,
    PuzzleId:String(DeckPosition), FEN:fen, Moves:'e2e4 e7e5', Rating:1200 })) };
  expect(decodeStudyResult(deck, structuredClone(computeStudyTask(deck))).map(item => item.record.DeckPosition)).toEqual([1, 2]);
});

it('wrong_task_result_and_malformed_required_values_are_rejected', () => {
  const cards = computeStudyTask(queue);
  expect(() => decodeStudyResult(queue, [{ ...cards[0], queueEntryId: 0 }])).toThrow();
  expect(() => decodeStudyResult({ kind: 'index', lines: [] }, cards)).toThrow();
  const task = { kind: 'repertoireAlternatives' as const, generation: 3, lines: [line('a', 'e2e4')] };
  const result = computeStudyTask(task);
  expect(() => decodeStudyResult(task, { ...result, generation: 4 })).toThrow();
  expect(() => decodeStudyResult(task, { ...result, movesByRepertoire: {} })).toThrow();
  expect(studyReplySchema.safeParse({ id: 1, state: 'running', error:'failure' }).success).toBe(false);
  expect(studyReplySchema.safeParse({ id: 1, error:'failure', result:[] }).success).toBe(false);
});

it.each(['crash', 'timeout'])('optional %s leaves concurrent essential queue working', async mode => {
  vi.resetModules(); vi.useFakeTimers();
  const workers: FakeWorker[] = [];
  class FakeWorker {
    onmessage: ((event: {data: unknown}) => void) | null = null;
    onerror: ((event: {message: string}) => void) | null = null;
    onmessageerror = null; postMessage = vi.fn(); terminate = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal('Worker', FakeWorker);
  const { runStudyTask } = await import('../../app/lib/background-study');
  const optional = runStudyTask({ kind:'index', lines:[] });
  const rejected = expect(optional).rejects.toThrow('Workspace preparation failed');
  const essential = runStudyTask(queue);
  if (mode === 'crash') workers[0].onerror!({ message:'crash' });
  else await vi.advanceTimersByTimeAsync(120_000);
  await rejected;
  const id = workers[1].postMessage.mock.calls[0][0].id;
  workers[1].onmessage!({ data:{ id, result:computeStudyTask(queue) } });
  await expect(essential).resolves.toHaveLength(1);
  expect(workers[1].terminate).not.toHaveBeenCalled();
});

it('cancelled_reply_cannot_settle_new_id_and_wrong_result_rejects_only_its_request', async () => {
  vi.resetModules();
  const workers: FakeWorker[] = [];
  class FakeWorker {
    onmessage: ((event: {data: unknown}) => void) | null = null;
    onerror = null; onmessageerror = null; postMessage = vi.fn(); terminate = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal('Worker', FakeWorker);
  const { runStudyTask } = await import('../../app/lib/background-study');
  const controller = new AbortController();
  const old = runStudyTask(queue, controller.signal);
  const cancelled = expect(old).rejects.toMatchObject({ name: 'AbortError' });
  const oldId = workers[0].postMessage.mock.calls[0][0].id;
  controller.abort(); await cancelled;
  const first = runStudyTask(queue);
  const firstId = workers[0].postMessage.mock.calls[1][0].id;
  let settled = false; void first.finally(() => { settled = true; });
  workers[0].onmessage!({ data: { id: oldId, result: computeStudyTask(queue) } });
  await Promise.resolve(); expect(settled).toBe(false);
  workers[0].onmessage!({ data: { id: firstId, result: computeStudyTask(queue) } });
  await expect(first).resolves.toHaveLength(1);
  const invalid = runStudyTask(queue);
  const invalidId = workers[0].postMessage.mock.calls[2][0].id;
  workers[0].onmessage!({ data: { id: invalidId, result: [{ lineId:'line', repertoireId:'rep',
    repertoireName:'Rep', fen, ply:0 }] } });
  await expect(invalid).rejects.toThrow('invalid data');
  expect(workers[0].terminate).not.toHaveBeenCalled();
});
