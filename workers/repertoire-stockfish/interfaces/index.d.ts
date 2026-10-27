import type { fork, spawn } from 'node:child_process';
import type { Readable } from 'node:stream';
import type { EngineEvidence } from '../../../app/features/product-api/contracts';

export type { EngineEvidence };
export type EngineContext = EngineEvidence['context'];
export type CandidateEvaluation = EngineEvidence['candidates'][number];

/** Adapter envelope around the existing shared context; no second evidence DTO. */
export interface EvaluationRequest {
  readonly request_id: string;
  readonly context: EngineContext;
  readonly candidates: readonly string[];
}
export interface EvaluationOptions { readonly signal?: AbortSignal; }
export interface EvaluationTimeouts {
  readonly startupTimeoutMs?: number;
  readonly searchTimeoutMs?: number;
  readonly totalTimeoutMs?: number;
}
export interface OwnedTransport {
  send(command: string): void;
  /** Resolve only after verified owned-process exit; reject if unverified. */
  close(): Promise<void>;
}
export interface TransportCallbacks {
  readonly onLine: (line: string) => void;
  readonly onError: () => void;
}
export type OwnedLauncher = (callbacks: TransportCallbacks) => OwnedTransport;
export interface EvaluatorOptions extends EvaluationTimeouts { readonly launch: OwnedLauncher; }
export interface StockfishEvaluatorOptions extends EvaluationTimeouts { readonly executable?: string; }
export interface OwnedLauncherOptions {
  readonly executable?: string;
  readonly spawnProcess?: typeof spawn;
  readonly forkProcess?: typeof fork;
  readonly closeGraceMs?: number;
  readonly killWaitMs?: number;
}
export interface EvaluationDiagnostics {
  readonly elapsed_ms: number;
  readonly cleanup: 'verified';
}
export interface CompletedEvaluation {
  readonly request_id: string;
  readonly status: 'ok' | 'inconclusive';
  readonly evidence: EngineEvidence;
  readonly diagnostics: EvaluationDiagnostics & {
    readonly inconsistent_candidates: readonly CandidateEvaluation[];
  };
}
export interface UnavailableEvaluation {
  readonly request_id: string;
  readonly status: 'unavailable' | 'cancelled';
  readonly evidence: EngineEvidence;
  readonly error: Readonly<{ code: string }>;
  readonly diagnostics: EvaluationDiagnostics & {
    readonly incomplete_baseline: CandidateEvaluation | null;
  };
}
export type EvaluationResult = CompletedEvaluation | UnavailableEvaluation;
export interface Evaluator {
  /** May reject EvaluationError before evidence exists or cleanup is verified. */
  evaluate(request: EvaluationRequest, options?: EvaluationOptions): Promise<EvaluationResult>;
}
export interface StdioError {
  readonly request_id: string | null;
  readonly status: 'error';
  readonly error: Readonly<{ code: string }>;
}
export interface StdioOptions extends EvaluationOptions {
  readonly input?: Readable;
  readonly output?: { write(chunk: string): unknown };
  readonly evaluator?: Evaluator;
  readonly inputTimeoutMs?: number;
}
export type StdioResult = EvaluationResult | StdioError;

export class EvaluationError extends Error {
  constructor(code: string);
  code: string;
}
export const LIMITS: Readonly<{
  candidates: number; history: number; requestBytes: number; responseBytes: number;
  engineBytes: number; lineBytes: number; depth: number; nodes: number;
  timeMs: number; threads: number; hashMb: number;
}>;
export function createEvaluator(options: EvaluatorOptions): Evaluator;
export function createStockfishEvaluator(options?: StockfishEvaluatorOptions): Evaluator;
export function createOwnedLauncher(options?: OwnedLauncherOptions): OwnedLauncher;
export function runStdio(options?: StdioOptions): Promise<StdioResult>;
