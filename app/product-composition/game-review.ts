/** Private #491 endpoint wiring. Root mounting waits for the shared-file window. */
import {
  createGameReviewApi,
  type GameReviewEndpoints,
} from "../features/game-review/api";

export const gameReviewEndpoints: GameReviewEndpoints = {
  recentGames: "/api/product/game-review/games",
  gameDetail: ({ provider, game_id, repertoire_id }) => {
    const path = `/api/product/game-review/games/${encodeURIComponent(provider)}/${encodeURIComponent(game_id)}`;
    return repertoire_id === undefined
      ? path
      : `${path}?${new URLSearchParams({ repertoire_id })}`;
  },
  sync: "/api/product/game-review/sync",
  retryFeedback: "/api/product/game-review/feedback/retry",
};

/** Construct once in optional-shell composition; construction performs no I/O. */
export function createProductGameReviewApi(fetchRequest?: typeof globalThis.fetch) {
  return createGameReviewApi({ endpoints: gameReviewEndpoints, fetch: fetchRequest });
}
