import { expect, expectTypeOf, it, vi } from "vitest";
import type { GameReviewViewProps, GapReviewIntent } from "../../app/features/game-review/interfaces";
import type { TrainingViewProps } from "../../app/features/study/interfaces";
import type { TacticsViewProps } from "../../app/features/tactics/interfaces";
import type { StudySessionOptions } from "../../packages/study-session/src/interfaces/index";

const retainedViews = vi.hoisted(() => ({
  game: vi.fn(() => null),
  training: vi.fn(() => null),
  tactics: vi.fn(() => null),
  loaded: { game: 0, training: 0, tactics: 0, home: 0 },
}));
vi.mock("../../app/features/game-review/GameReviewView", () => {
  retainedViews.loaded.game += 1;
  return { GameReviewView: retainedViews.game };
});
vi.mock("../../app/views/training_view", () => {
  retainedViews.loaded.training += 1;
  return { default: retainedViews.training };
});
vi.mock("../../app/views/tactics_view", () => {
  retainedViews.loaded.tactics += 1;
  return { default: retainedViews.tactics };
});
vi.mock("../../app/views/home_view", () => {
  retainedViews.loaded.home += 1;
  throw new Error("A public feature surface must not load the Home composition shell.");
});

it("cold UI surfaces expose only named entries and load retained views only on explicit request", async () => {
  const fetchRequest = vi.spyOn(globalThis, "fetch");
  const storage = vi.spyOn(indexedDB, "open");
  const writes = vi.spyOn(Storage.prototype, "setItem");
  const game = await import("../../app/features/game-review/interfaces");
  const study = await import("../../app/features/study/interfaces");
  const tactics = await import("../../app/features/tactics/interfaces");
  expect(Object.keys(game).sort()).toEqual(["createGameReviewApi", "loadGameReviewView"]);
  expect(Object.keys(study)).toEqual(["loadTrainingView"]);
  expect(Object.keys(tactics)).toEqual(["loadTacticsView"]);
  expect(retainedViews.loaded).toEqual({ game: 0, training: 0, tactics: 0, home: 0 });
  expect(fetchRequest).not.toHaveBeenCalled(); expect(storage).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  expect(await game.loadGameReviewView()).toBe(retainedViews.game);
  expect(await study.loadTrainingView()).toBe(retainedViews.training);
  expect(await tactics.loadTacticsView()).toBe(retainedViews.tactics);
  expect(retainedViews.loaded).toEqual({ game: 1, training: 1, tactics: 1, home: 0 });
  expect(retainedViews.game).not.toHaveBeenCalled(); expect(retainedViews.training).not.toHaveBeenCalled(); expect(retainedViews.tactics).not.toHaveBeenCalled();
});

it("public UI contracts retain the actual existing component signatures", () => {
  expectTypeOf<GameReviewViewProps>().toEqualTypeOf<import("../../app/features/game-review/GameReviewView").GameReviewViewProps>();
  expectTypeOf<GapReviewIntent>().toEqualTypeOf<import("../../app/features/game-review/GameReviewView").GapReviewIntent>();
  expectTypeOf<TrainingViewProps>().toEqualTypeOf<Parameters<typeof import("../../app/views/training_view").default>[0]>();
  expectTypeOf<TacticsViewProps>().toEqualTypeOf<Parameters<typeof import("../../app/views/tactics_view").default>[0]>();
  expectTypeOf<StudySessionOptions>().toEqualTypeOf<Parameters<typeof import("../../packages/study-session/src/index").createStudySession>[0]>();
});

it("public Game Review factory is canonical and uses transport only for an explicit query", async () => {
  const publicApi = await import("../../app/features/game-review/interfaces");
  const canonical = await import("../../app/features/game-review/api");
  expect(publicApi.createGameReviewApi).toBe(canonical.createGameReviewApi);
  const fetchRequest = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ games: [], sync: [] }), { status: 200 }));
  const api = publicApi.createGameReviewApi({ fetch: fetchRequest, endpoints: { recentGames: "/games", gameDetail: () => "/game", sync: "/sync", retryFeedback: "/retry" } });
  expect(fetchRequest).not.toHaveBeenCalled();
  await expect(api.recentGames()).resolves.toEqual({ games: [], sync: [] });
  expect(fetchRequest).toHaveBeenCalledTimes(1);
  expect(fetchRequest.mock.calls[0][1]?.method).toBe("GET");
});

it("public session factory preserves identity and performs no durable work during construction/start/dispose", async () => {
  const surface = await import("../../packages/study-session/src/interfaces/index");
  const canonical = await import("../../packages/study-session/src/index");
  expect(Object.keys(surface)).toEqual(["createStudySession"]);
  expect(surface.createStudySession).toBe(canonical.createStudySession);
  const storageAccess = vi.fn();
  const store = new Proxy({} as StudySessionOptions["store"], { get(_target, key) { storageAccess(key); throw new Error("Session construction must not use storage."); } });
  const deliver = vi.fn<StudySessionOptions["learning"]["deliver"]>();
  const session = surface.createStudySession({ store, learning: { deliver }, owner: "surface-test", now: () => 1 });
  expect(session.getSnapshot()).toEqual({ phase: "active", generation: 0 });
  const changed = vi.fn(); const unsubscribe = session.subscribe(changed);
  session.start("retained-original");
  expect(session.getSnapshot()).toEqual({ phase: "active", generation: 1, key: "retained-original" });
  expect(changed).toHaveBeenCalledTimes(1);
  unsubscribe(); session.dispose();
  expect(storageAccess).not.toHaveBeenCalled(); expect(deliver).not.toHaveBeenCalled();
});
