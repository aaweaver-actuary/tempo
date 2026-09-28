import { randomUUID } from "node:crypto";

const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export class PendingMaiaCommandError extends Error {
  constructor(path, operationId) {
    super(`${path}: database command ${operationId} remains pending after 120 seconds`);
    this.name = "PendingMaiaCommandError";
    this.operationId = operationId;
  }
}

export async function requestMaiaApi(apiUrl, path, options = {}, transport = {}) {
  const fetchImpl = transport.fetchImpl ?? fetch;
  const wait = transport.pause ?? pause;
  const { operationId: suppliedOperationId, ...requestOptions } = options;
  const operationId = options.method === "POST" ? (suppliedOperationId ?? randomUUID()) : undefined;
  const headers = {
    "X-Tempo-Work-Class": "background",
    "Content-Type": "application/json",
    ...requestOptions.headers,
    ...(operationId ? { "Idempotency-Key": operationId } : {}),
  };
  let response;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      response = await fetchImpl(`${apiUrl}${path}`, { ...requestOptions, headers });
      if (response.status !== 503) break;
    } catch (error) {
      if (attempt === 2) throw error;
    }
    if (attempt < 2) await wait(250 * (attempt + 1));
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
  for (let attempt = 0; attempt < 480; attempt += 1) {
    await wait(250);
    const receiptResponse = await fetchImpl(
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
  }
  throw new PendingMaiaCommandError(path, receiptId);
}
