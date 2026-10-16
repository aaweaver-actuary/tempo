import { randomUUID } from "node:crypto";

const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export function createMaiaRelease(request, job) {
  const operationId = randomUUID();
  const body = JSON.stringify({node_id: job.node_id, lease_id: job.lease_id});
  const release = () => request('/api/repertoire-coverage/maia/release', {
    method: 'POST', operationId, body, signal: AbortSignal.timeout(120_000),
  });
  return Object.assign(release, {operationId});
}

export class PendingMaiaCommandError extends Error {
  constructor(path, operationId, reason = "remains pending after 120 seconds") {
    super(`${path}: database command ${operationId} ${reason}`);
    this.name = "PendingMaiaCommandError";
    this.operationId = operationId;
  }
}

export async function requestMaiaApi(apiUrl, path, options = {}, transport = {}) {
  const fetchImpl = transport.fetchImpl ?? fetch;
  const wait = transport.pause ?? pause;
  const { operationId: suppliedOperationId, pollAttempts = 480, requestAttempts = 3, ...requestOptions } = options;
  const operationId = options.method === "POST" ? (suppliedOperationId ?? randomUUID()) : undefined;
  const headers = {
    "X-Tempo-Work-Class": "background",
    "Content-Type": "application/json",
    ...requestOptions.headers,
    ...(operationId ? { "Idempotency-Key": operationId } : {}),
  };
  const attempts = Number.isInteger(requestAttempts) ? Math.max(1, Math.min(3,requestAttempts)) : 3;
  const deadline = AbortSignal.timeout(120_000);
  const fetchBounded = (url, delivery) => {
    const signal = AbortSignal.any([deadline, AbortSignal.timeout(5_000), ...(requestOptions.signal ? [requestOptions.signal] : [])]);
    signal.throwIfAborted();
    return fetchImpl(url, {...delivery, signal});
  };
  try {
  let response;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      response = await fetchBounded(`${apiUrl}${path}`, { ...requestOptions, headers });
      if (response.status !== 503) break;
    } catch (error) {
      if (requestOptions.signal?.aborted || attempt === attempts-1) throw error;
    }
    requestOptions.signal?.throwIfAborted();
    if (attempt < attempts-1) await wait(250 * (attempt + 1));
  }
  if (!response.ok) {
    const error = new Error(`${path}: HTTP ${response.status} ${await response.text()}`);
    error.terminal = response.status < 500 && response.status !== 408 && response.status !== 429;
    throw error;
  }
  if (response.status !== 202) return response.json();

  const pending = await response.json();
  const receiptId = pending.operation_id;
  if (!receiptId) throw new Error(`${path}: pending command did not return an operation ID`);
  for (let attempt = 0; attempt < pollAttempts; attempt += 1) {
    await wait(250);
    const receiptResponse = await fetchBounded(
      `${apiUrl}/api/operations/${encodeURIComponent(receiptId)}`,
      { headers: { "X-Tempo-Work-Class": "background" } },
    );
    if (!receiptResponse.ok) {
      if (receiptResponse.status >= 500) continue;
      throw new Error(`${path}: operation lookup returned HTTP ${receiptResponse.status}`);
    }
    const receipt = await receiptResponse.json();
    if (receipt.state === "complete") return receipt.response;
    if (receipt.state === "failed") {
      const error = new Error(`${path}: ${receipt.error?.message ?? "database command failed"}`);
      error.terminal = true;
      throw error;
    }
    if (receipt.state === "blocked")
      throw new PendingMaiaCommandError(path, receiptId,
        `is blocked: ${receipt.last_error?.message ?? receipt.message ?? "check operation status"}`);
  }
  throw new PendingMaiaCommandError(path, receiptId,
    `remains pending after ${(pollAttempts * 250) / 1000} seconds`);
  } catch (error) {
    if (operationId && !error.terminal && !(error instanceof PendingMaiaCommandError))
      throw new PendingMaiaCommandError(path, operationId, `has an unconfirmed delivery: ${String(error)}`);
    throw error;
  }
}
