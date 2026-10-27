import { MaiaError as ExistingMaiaError } from '../errors.mjs';
import { SUPPORTED_RATINGS as existingRatings } from '../core.mjs';
import { resolveRatingInputs as existingResolveRatingInputs } from '../model.mjs';
import { createProbabilityProvider as existingProvider, createProcessExecution as existingExecution } from '../probability-provider.mjs';
import { probabilityStdio as existingStdio } from '../probability-stdio.mjs';

/** @type {typeof import('./index').MaiaError} */
export const MaiaError = ExistingMaiaError;
/** @type {typeof import('./index').SUPPORTED_RATINGS} */
export const SUPPORTED_RATINGS = existingRatings;
/** @type {typeof import('./index').resolveRatingInputs} */
export const resolveRatingInputs = existingResolveRatingInputs;
/** @type {typeof import('./index').createProbabilityProvider} */
export const createProbabilityProvider = existingProvider;
/** @type {typeof import('./index').createProcessExecution} */
export const createProcessExecution = existingExecution;
/** @type {typeof import('./index').probabilityStdio} */
export const probabilityStdio = existingStdio;
