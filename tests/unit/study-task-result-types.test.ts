import { describe, expect, it } from 'vitest';
import { runStudyTask } from '../../app/lib/background-study';
import type { PracticeCard } from '../../app/types';
import type { RepertoireAlternativeSnapshot } from '../../app/lib/repertoire-alternative-index';

// These assignments are checked by tsc and never execute a worker.
function task_kind_determines_result_type() {
  const queue = runStudyTask({ kind: 'queue', payload: {} });
  const deck = runStudyTask({ kind: 'deck', records: [], deckId: 'x' });
  // @ts-expect-error queue results are cards, not a repertoire snapshot
  const snapshot: Promise<RepertoireAlternativeSnapshot> = queue;
  // @ts-expect-error deck results contain both a record and a card
  const cards: Promise<PracticeCard[]> = deck;
  return [snapshot, cards];
}

describe('task_kind_determines_result_type', () => {
  it('keeps the compile-time examples available to typecheck', () => {
    expect(task_kind_determines_result_type).toBeTypeOf('function');
  });
});
