import * as z from 'zod';
import { analysisLineSchema, indexedPositionSchema, packagedPuzzleSchema, practiceCardSchema,
  fenKeySchema, identifierSchema, uciMoveSchema } from '../domain/schemas';
import { studySnapshotSchema } from '../domain/study-exercises';
import { decodePositionPresentation, positionPresentationSchema } from '../domain/position-presentation';
import { validateWorkspacePayload } from '../domain/adapters/workspace-adapters';
import { adaptPositionPresentation } from '../domain/adapters/position-presentation-adapter';
import type { StudyTask, StudyTaskResult } from './study-computation';
import type { ValidationReporter } from './data-validation';

// The older local-repertoire schema is a subset; worker cards include the
// Study, defense, encounter, and position fields supplied by the current card adapter.
const workerCard = practiceCardSchema.extend({
  kind: z.enum(['opening', 'middlegame', 'endgame', 'puzzle', 'defense', 'study']),
  priorityReason: z.string().optional(),
  encounterBadges: z.array(z.enum(['Seen recently', 'Frequent'])).optional(),
  encounterCount30d: z.number().int().nonnegative().optional(),
  lastEncounteredAt: z.string().optional(),
  hasPriorStudyReview: z.boolean().optional(),
  priorStudyReviewCount: z.number().int().nonnegative().optional(),
  defenseCandidateId: z.string().optional(),
  studyId: z.string().optional(), studyExerciseId: z.string().optional(),
  studySnapshot: studySnapshotSchema.optional(),
  positionStudyMode: z.literal('position').optional(),
  // Structured cloning removes freezes; restore the owner's immutable plan.
  positionPresentation: positionPresentationSchema.transform(decodePositionPresentation).optional(),
  positionStudyHold: z.string().min(1).optional(),
}).superRefine((card, context) => {
  const hasPlan = card.positionPresentation !== undefined;
  const hasHold = card.positionStudyHold !== undefined;
  if (card.positionStudyMode !== 'position') {
    if (hasPlan || hasHold) context.addIssue({ code: 'custom', message: 'Position result requires its study mode' });
    return;
  }
  if (hasPlan === hasHold) {
    context.addIssue({ code: 'custom', message: 'Position result requires either an issued plan or a hold' });
    return;
  }
  if (hasPlan) {
    try { adaptPositionPresentation(card.positionPresentation, card); }
    catch { context.addIssue({ code: 'custom', path: ['positionPresentation'], message: 'Position plan does not match its original card' }); }
  }
});
const position = indexedPositionSchema.omit({ distance: true });
const ranked = position.extend({ distance: z.number().int().min(0).max(2) });
const snapshot = z.strictObject({
  generation: z.number().int().nonnegative(), positions: z.array(position),
  movesByRepertoire: z.map(identifierSchema, z.map(fenKeySchema, z.set(uciMoveSchema))),
});

/** Validate the untrusted structured-clone result against its retained task. */
export function decodeStudyResult<Task extends StudyTask>(task: Task, raw: unknown, shouldPublishDiagnostics?: () => boolean, reporter?: ValidationReporter): StudyTaskResult<Task> {
  let result: unknown;
  switch (task.kind) {
    case 'workspace': result = validateWorkspacePayload(task.url, raw, shouldPublishDiagnostics, reporter); break;
    case 'queue': result = z.array(workerCard).parse(raw); break;
    case 'deck': result = z.array(z.strictObject({ record: packagedPuzzleSchema, card: workerCard })).parse(raw); break;
    case 'lines': case 'transportLines': result = z.array(analysisLineSchema).parse(raw); break;
    case 'index': result = z.array(position).parse(raw); break;
    case 'repertoireAlternatives': case 'repertoirePreparation': {
      const parsed = snapshot.parse(raw);
      if (parsed.generation !== task.generation) throw new Error('Study worker returned a different generation');
      result = parsed;
      break;
    }
    case 'matches': result = z.array(ranked).parse(raw); break;
    case 'similarity': result = z.array(ranked.extend({ nextUci: uciMoveSchema })).parse(raw); break;
  }
  // The switch has validated the task-specific schema; workspace remains unknown.
  return result as StudyTaskResult<Task>;
}
