/** Game Review entry points. Concrete HTTP routes and mounting belong to composition. */
export { createGameReviewApi } from "../api";
export type { GameReviewClient, GameReviewEndpoints, GameSelection } from "../api";
export type { GameReviewViewProps, GapReviewIntent } from "../GameReviewView";

export type GameReviewComponent = typeof import("../GameReviewView").GameReviewView;

/** Importing the surface does not load the private view, board, or view state. */
export async function loadGameReviewView(): Promise<GameReviewComponent> {
  return (await import("../GameReviewView")).GameReviewView;
}
