/** Private #491 endpoint wiring. Root mounting waits for the shared-file window. */
import {
  createGameReviewApi,
  type GameReviewEndpoints,
} from "../features/game-review/api";
import { API_URL } from "../const";

export const gameReviewBase = `${API_URL}/api/product/game-review`;

export const gameReviewEndpoints: GameReviewEndpoints = {
  recentGames: `${gameReviewBase}/games`,
  gameDetail: ({ provider, game_id, repertoire_id }) => {
    const path = `${gameReviewBase}/games/${encodeURIComponent(provider)}/${encodeURIComponent(game_id)}`;
    return repertoire_id === undefined
      ? path
      : `${path}?${new URLSearchParams({ repertoire_id })}`;
  },
  sync: `${gameReviewBase}/sync`,
  retryFeedback: `${gameReviewBase}/feedback/retry`,
};

/** Construct once in optional-shell composition; construction performs no I/O. */
export function createProductGameReviewApi(fetchRequest?: typeof globalThis.fetch) {
  return createGameReviewApi({ endpoints: gameReviewEndpoints, fetch: fetchRequest });
}
