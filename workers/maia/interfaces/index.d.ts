import type { Readable } from 'node:stream';
import type { ProbabilityEvidence } from '../../../app/features/product-api/contracts';
export type { ProbabilityEvidence };
export interface Ratings { readonly elo_self: number; readonly elo_oppo: number; }
export interface ProbabilityRequest {
  readonly position: ProbabilityEvidence['position'];
  readonly fen: string;
  readonly variant: 'standard';
  readonly population: NonNullable<ProbabilityEvidence['population']>;
  readonly ratings: Ratings;
  readonly rating_reference: string;
  readonly limits: Readonly<{initialization_ms: number; inference_ms: number; memory_mb: number}>;
}
/** Model identity is model-owned provenance, not a second shared probability DTO. */
export interface ModelIdentity {
  readonly model_id: string;
  readonly model_version: string;
  readonly implementation_version: string;
  readonly [key: string]: unknown;
}
export interface Prediction { readonly move: string; readonly probability: number; }
export interface ModelJob {
  readonly fen: string;
  readonly legal_moves: readonly string[];
  readonly model_id: string;
  readonly model_version: string;
  readonly ratings: Ratings;
}
export interface Execution {
  initialize(limits: ProbabilityRequest['limits']): Promise<{
    identity: ModelIdentity;
    resources?: Readonly<{hard_memory_mb: number; threads: number; concurrency: number}> | null;
  }>;
  infer(job: ModelJob): Promise<readonly Prediction[]>;
  /** Resolve only after native work is stopped and owned output is fenced. */
  terminate(): Promise<void>;
}
export interface ProbabilityResult {
  readonly evidence: ProbabilityEvidence;
  readonly provenance: Readonly<{
    adapter_version: 'maia-probability-v1'; model_identity: ModelIdentity | null;
    resources: Readonly<{hard_memory_mb: number; threads: number; concurrency: number}> | null;
    input: ProbabilityRequest; requested_moves: readonly string[];
    scope: 'all_legal_moves' | 'conditional_requested_moves'; limitations: readonly string[];
  }>;
  readonly diagnostic: Readonly<{
    scope: 'conditional_requested_moves'; predictions: readonly Prediction[] | null;
    unavailable_reason: string | null;
  }> | null;
}
export interface ProbabilityOptions { readonly signal?: AbortSignal; }
export interface ProbabilityProvider {
  evidence(request: ProbabilityRequest, options?: ProbabilityOptions & {readonly requestedMoves?: readonly string[]}): Promise<ProbabilityResult>;
  diagnostic(request: ProbabilityRequest, moves: readonly string[], options?: ProbabilityOptions): Promise<ProbabilityResult>;
  close(): Promise<void>;
}
export interface ProviderOptions {
  readonly createExecution?: () => Execution;
  readonly now?: () => string;
}
export class MaiaError extends Error {
  constructor(code: string, message: string, options?: {retryable?: boolean; cause?: unknown});
  code: string;
  retryable: boolean;
}
export const SUPPORTED_RATINGS: readonly number[];
export function resolveRatingInputs(input: {readonly rating: number; readonly ratings?: never} | {readonly ratings: Ratings; readonly rating?: never}, options?: {legacy?: boolean}): Ratings;
export function createProbabilityProvider(options?: ProviderOptions): ProbabilityProvider;
export function createProcessExecution(options?: {modelOptions?: Parameters<typeof import('../model.mjs').createModel>[0]}): Execution;
export function probabilityStdio(options?: ProbabilityOptions & {
  readonly input?: Readable;
  readonly output?: {write(chunk: string): unknown};
  readonly provider?: ProbabilityProvider;
}): Promise<void>;
