import {
  EvaluationError as ExistingEvaluationError,
  LIMITS as existingLimits,
  createEvaluator as existingCreateEvaluator,
} from '../evaluate.mjs';
import {
  createOwnedLauncher as existingCreateOwnedLauncher,
  createStockfishEvaluator as existingCreateStockfishEvaluator,
  runStdio as existingRunStdio,
} from '../stdio.mjs';

// Typed aliases preserve the existing callable/constructor identities. Explicit
// JSDoc connects index.d.ts to .mjs consumers under the repository's allowJs mode.
/** @type {typeof import('./index').EvaluationError} */
export const EvaluationError = ExistingEvaluationError;
/** @type {typeof import('./index').LIMITS} */
export const LIMITS = existingLimits;
/** @type {typeof import('./index').createEvaluator} */
export const createEvaluator = existingCreateEvaluator;
/** @type {typeof import('./index').createOwnedLauncher} */
export const createOwnedLauncher = existingCreateOwnedLauncher;
/** @type {typeof import('./index').createStockfishEvaluator} */
export const createStockfishEvaluator = existingCreateStockfishEvaluator;
/** @type {typeof import('./index').runStdio} */
export const runStdio = existingRunStdio;
