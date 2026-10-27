import type TacticsView from "../../../views/tactics_view";

export type TacticsViewProps = Parameters<typeof TacticsView>[0];
export type TacticsComponent = typeof TacticsView;

/** Capture/catalog/solve behavior remains in the unchanged private view. */
export async function loadTacticsView(): Promise<TacticsComponent> {
  return (await import("../../../views/tactics_view")).default;
}
