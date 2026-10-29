import {
  GameDetailSchema, RecentGamesSchema, ProviderSyncStatusSchema, LearningReceiptSchema,
  type GameSummary, type GameReviewApi,
} from "../product-api/contracts";
import { confirmOperationResponse, PendingOperationError } from "../../lib/operation-status";

export interface GameSelection {
  provider: GameSummary["provider"];
  game_id: string;
  repertoire_id?: string;
}

/** Feature capability only. Durable state and idempotency belong to the receivers. */
export type GameReviewClient = Pick<GameReviewApi, "recentGames" | "gameDetail" | "sync" | "retryFeedback">;

export interface GameReviewEndpoints {
  recentGames: string;
  gameDetail: (selection: GameSelection) => string;
  sync: string;
  retryFeedback: string;
}

/** No default routes: #491 owns actual endpoint selection and product wiring. */
export function createGameReviewApi({ endpoints, fetch: fetchRequest = globalThis.fetch }: {
  endpoints: GameReviewEndpoints;
  fetch?: typeof globalThis.fetch;
}): GameReviewClient {
  async function request<T>(url: string, schema: { parse(value: unknown): T }, signal?: AbortSignal, body?: unknown): Promise<T> {
    let response = await fetchRequest(url, {
      method: body === undefined ? "GET" : "POST", signal, credentials: "same-origin",
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (body !== undefined) {
      if (response.status === 202) {
        const pending = await response.clone().json() as { operation_id?: string };
        const requested = (body as { operation_id?: string }).operation_id;
        if (!pending.operation_id || pending.operation_id !== requested)
          throw new Error("Game Review did not confirm the original operation. Retry the original request.");
      }
      response = await confirmOperationResponse(response, { signal, fetch: fetchRequest });
      if (response.status === 202) {
        const pending = await response.json() as { operation_id: string };
        throw new PendingOperationError(pending.operation_id);
      }
    }
    if (!response.ok) throw new Error(`Game Review request failed (HTTP ${response.status}). Retry is available.`);
    let parsed: T;
    try { parsed = schema.parse(await response.json()); }
    catch { throw new Error("Game Review returned invalid data. Refresh or retry."); }
    if (signal?.aborted) throw new DOMException("Game Review request cancelled", "AbortError");
    return parsed;
  }
  return {
    recentGames: ({ signal } = {}) => request(endpoints.recentGames, RecentGamesSchema, signal),
    gameDetail: ({ signal, ...selection }) => request(endpoints.gameDetail(selection), GameDetailSchema, signal),
    sync: ({ signal, ...command }) => request(endpoints.sync, ProviderSyncStatusSchema, signal, command),
    retryFeedback: ({ signal, ...command }) => request(endpoints.retryFeedback, LearningReceiptSchema, signal, command),
  };
}
