/** Queue HTTP protocol; no composition, store, or browser singleton. */
import { queueCardSchema, queueEnvelopeSchema } from "../../domain/schemas";
import { decodeOperationAcceptance, decodeOperationResult } from "../../domain/adapters/operation-result-adapter";
import type { StudyNextExerciseOutcome } from "../../domain/transport";

export type QueueTimer = { setTimeout(callback: () => void, delay: number): ReturnType<typeof globalThis.setTimeout>;
  clearTimeout(handle: ReturnType<typeof globalThis.setTimeout>): void };
export type QueueAuthority = { baseUrl: string; isCurrent(): boolean };
export type QueueTransportOptions = { fetch: typeof globalThis.fetch; authority(): QueueAuthority; timer: QueueTimer };
export class QueueAuthorityChangedError extends Error {
  constructor() { super("Study authority changed while acquiring the queue. Retry with the current service."); this.name = "QueueAuthorityChangedError"; }
}

export type QueuePayload = {
  cards?: unknown[];
  count?: number;
  local_date?: string;
  projection?: { state?: string; generation?: number; last_error?: string | null };
};

export function queueOutcome(queue: QueuePayload): StudyNextExerciseOutcome<QueuePayload> {
  const head = queueCardSchema.safeParse(queue.cards?.[0]);
  if (head.success && head.data.position_study_hold && !head.data.position_presentation)
    return { kind: "held", reason: head.data.position_study_hold, queue };
  if (!queue.cards?.length && queue.projection?.state === "refreshing")
    return { kind: "held", reason: "queue_projection_refreshing", queue };
  return queue.cards?.length ? { kind: "ready", queue } : { kind: "empty", queue };
}

export class QueueProcessingError extends Error {}

export function awaitQueueStage<T>(operation: Promise<T> | T, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const cancelled = () => reject(signal.reason ?? new DOMException("Queue acquisition cancelled", "AbortError"));
    signal.addEventListener("abort", cancelled, { once: true });
    // Attach settlement handlers even after cancellation: late stage results
    // have no installation authority, and late rejections remain observed.
    Promise.resolve(operation).then(resolve, reject).finally(() => signal.removeEventListener("abort", cancelled));
    if (signal.aborted) cancelled();
  });
}


export function createQueueTransport(options: QueueTransportOptions) {
  function captureAuthority(): QueueAuthority {
    const authority = options.authority();
    assertCurrent(authority);
    return authority;
  }
  function assertCurrent(authority: QueueAuthority): void {
    if (!authority.isCurrent()) throw new QueueAuthorityChangedError();
  }
  async function fetchQueueWindow(signal: AbortSignal, observePreparation = false, operationId?: string,
    authority: QueueAuthority = captureAuthority()): Promise<{ response: Response; payload: unknown }> {
    assertCurrent(authority);
    const controller = new AbortController();
    const abortSupersededRequest = () => controller.abort();
    if (signal.aborted) abortSupersededRequest();
    else signal.addEventListener("abort", abortSupersededRequest, { once: true });
    let timedOut = false;
    const timeout = options.timer.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 15_000);
    try {
      const path = operationId ? `/api/operations/${encodeURIComponent(operationId)}` : "/api/queue/window?limit=20&include_opening_evidence=true";
      const response = await awaitQueueStage(options.fetch(`${authority.baseUrl}${path}`, {
        signal: controller.signal,
        ...(observePreparation ? { headers: { "X-Tempo-Work-Class": "background" } } : {}),
      }), controller.signal);
      assertCurrent(authority);
      let payload: unknown;
      try { payload = await awaitQueueStage(response.json(), controller.signal); }
      catch (error) {
        if (controller.signal.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
        if (response.ok) throw new QueueProcessingError(`The local queue returned invalid JSON. ${String(error)}`);
      }
      assertCurrent(authority);
      return { response, payload };
    } catch (error) {
      assertCurrent(authority);
      if (timedOut)
        throw new Error("Queue request timed out after 15 seconds. Retry loading the queue.", { cause: error });
      throw error;
    } finally {
      options.timer.clearTimeout(timeout);
      signal.removeEventListener("abort", abortSupersededRequest);
    }
  }

  function waitForQueueRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(signal.reason); return; }
      const onAbort = () => {
        options.timer.clearTimeout(timeout);
        reject(signal.reason);
      };
      const timeout = options.timer.setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, milliseconds);
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  const transientPositionHolds = new Set(["position_card_busy", "position_attempt_busy", "position_source_graph_busy",
    "position_owner_busy", "position_original_admission_unavailable"]);

  async function followQueueIssuance(response: Response, payload: unknown, signal: AbortSignal,
    authority: QueueAuthority = captureAuthority()): Promise<void> {
    assertCurrent(authority);
    const pending = decodeOperationAcceptance(payload);
    if (pending.kind === "malformed")
      throw new QueueProcessingError("The local queue returned an invalid pending operation. Retry loading the queue.");
    const operationId = pending.operationId;
    if (pending.kind === "blocked") throw new QueueProcessingError("Queue issuance is blocked. Check the operation status before retrying.");
    const retryAfter = Number(response.headers.get("Retry-After"));
    const delay = Number.isFinite(retryAfter) && retryAfter > 0 ? Math.max(250, retryAfter * 1000) : 1000;
    for (let poll = 0; poll < 20; poll += 1) {
      await waitForQueueRetry(delay, signal);
      assertCurrent(authority);
      const { response: status, payload: value } = await fetchQueueWindow(signal, false, operationId, authority);
      if (!status.ok) throw new QueueProcessingError(`Queue operation status could not be loaded (HTTP ${status.status}). Retry loading the queue.`);
      const receipt = decodeOperationResult(value, operationId, true);
      // The receipt's result is a command result, never a current queue envelope.
      if (receipt.kind === "complete") return;
      if (receipt.kind === "failed") throw new QueueProcessingError(receipt.error.message ?? "Queue issuance failed. Check the service before retrying.");
      if (receipt.kind === "blocked") throw new QueueProcessingError(`Queue issuance is blocked: ${receipt.error?.message ?? "check the operation status before retrying"}`);
      if (receipt.kind === "malformed") throw new QueueProcessingError("The local queue did not confirm its original issuance operation.");

    }
    throw new QueueProcessingError("Queue issuance is still pending. Retry loading the queue to check its result.");
  }

  async function loadTodayQueueWithRetry(
    signal: AbortSignal, acquisition: { observedBusyHead: boolean }, authority: QueueAuthority = captureAuthority(),
  ): Promise<QueuePayload> {
    assertCurrent(authority);
    let failedRequests = 0;
    let retryBusyHead = false;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      assertCurrent(authority);
      const { response, payload: responsePayload } = await fetchQueueWindow(signal, attempt > 0 && !retryBusyHead, undefined, authority);
      if (response.status === 202) {
        // Cancellation stops only this observation; the durable server operation
        // retains its identity and can finish after navigation or tab closure.
        acquisition.observedBusyHead = true;
        await followQueueIssuance(response, responsePayload, signal, authority);
        retryBusyHead = true;
        continue;
      }
      if (response.ok) {
        failedRequests = 0;
        const payload = responsePayload as QueuePayload;
        if (!Array.isArray(payload?.cards))
          throw new QueueProcessingError("The local queue returned an invalid queue response. Retry loading the queue.");
        if (payload.projection?.state === "failed")
          throw new QueueProcessingError(payload.projection.last_error ?? "Daily queue refresh failed");
        if (payload.projection?.state !== "refreshing" || payload.cards.length) {
          const validated = queueEnvelopeSchema.safeParse(responsePayload);
          if (!validated.success) throw new QueueProcessingError("The local queue returned an invalid queue response. Retry loading the queue.");
          const outcome = queueOutcome(validated.data);
          const headResult = queueCardSchema.safeParse(validated.data.cards[0]);
          const head = headResult.success ? headResult.data : undefined;
          if (outcome.kind === "held" && head?.position_study_mode === "position" &&
              head.position_presentation === null && transientPositionHolds.has(outcome.reason)) {
            acquisition.observedBusyHead = true;
            if (attempt === 19) throw new QueueProcessingError("Position study is temporarily busy. Retry loading the queue.");
            // A retry may issue the head, so it needs the same foreground admission
            // as the initial acquisition, not the preparation-observation class.
            retryBusyHead = true;
            await waitForQueueRetry(250, signal);
            continue;
          }
          return validated.data;
        }
        if (attempt === 19) throw new QueueProcessingError("Daily queue is still preparing. Check Tempo’s queue maintenance status and retry.");
        await waitForQueueRetry(250, signal);
        continue;
      }

      let detail = `HTTP ${response.status}`;
      let retryable = response.status === 503;
      try {
        const payload = responsePayload as {
          detail?: unknown;
          retryable?: unknown;
        };
        if (typeof payload.detail === "string") detail = payload.detail;
        retryable = payload.retryable === true || retryable;
      } catch {
        // Keep the stable HTTP fallback when the service returned no JSON body.
      }
      if (response.status === 503 && (responsePayload as { code?: unknown } | null)?.code === "foreground_wait") {
        await waitForQueueRetry(250, signal);
        continue;
      }
      failedRequests += 1;
      if (!retryable) throw new QueueProcessingError(detail);
      if (failedRequests >= 3) throw new Error(detail);
      await waitForQueueRetry(250 * (attempt + 1), signal);
    }
    throw new Error("The local queue could not be loaded.");
  }


  return { captureAuthority, assertCurrent, fetchQueueWindow, waitForQueueRetry, followQueueIssuance, loadTodayQueueWithRetry };
}
