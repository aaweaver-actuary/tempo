import { expect, expectTypeOf, it, vi } from "vitest";
import type {
  RepertoireReviewClient, RepertoireReviewRead, RepertoireReviewViewProps,
} from "../../app/features/repertoire-review/interfaces";
import type { BoardProps } from "../../packages/board-ui/src/interfaces/index";
import type { GamesReader, WorkbenchOptions } from "../../apps/workbench-web/src/interfaces/index";

const retainedView = vi.hoisted(() => ({ component: vi.fn(() => null), loads: 0 }));
vi.mock("../../app/features/repertoire-review/RepertoireReviewView", () => {
  retainedView.loads += 1;
  return { RepertoireReviewView: retainedView.component };
});

it("cold public surfaces expose declared entries without loading the review controller or starting resources", async () => {
  const fetchRequest = vi.spyOn(globalThis, "fetch");
  const storage = vi.spyOn(indexedDB, "open");
  const reads = vi.spyOn(Storage.prototype, "getItem");
  const writes = vi.spyOn(Storage.prototype, "setItem");
  const children = document.body.childElementCount;
  const review = await import("../../app/features/repertoire-review/interfaces");
  const board = await import("../../packages/board-ui/src/interfaces/index");
  const workbench = await import("../../apps/workbench-web/src/interfaces/index");
  expect(Object.keys(review)).toEqual(["loadRepertoireReviewView"]);
  expect(Object.keys(board).sort()).toEqual(["Board", "fitBoardSize"]);
  expect(Object.keys(workbench).sort()).toEqual(["gameFrames", "gameSourceUrl", "intelligenceOrigin", "intelligenceOriginForBuild", "mountWorkbench", "studyHandoffNotice", "studyUrl", "studyUrlForBuild"]);
  expect(retainedView.loads).toBe(0);
  expect(fetchRequest).not.toHaveBeenCalled(); expect(storage).not.toHaveBeenCalled();
  expect(reads).not.toHaveBeenCalled(); expect(writes).not.toHaveBeenCalled();
  expect(document.body.childElementCount).toBe(children);
  expect(await review.loadRepertoireReviewView()).toBe(retainedView.component);
  expect(retainedView.loads).toBe(1); expect(retainedView.component).not.toHaveBeenCalled();
});

it("retains board and Workbench function/component identity without exposing private stores", async () => {
  const board = await import("../../packages/board-ui/src/interfaces/index");
  const canonicalBoard = await import("../../packages/board-ui/src/index");
  const workbench = await import("../../apps/workbench-web/src/interfaces/index");
  const canonicalMount = await import("../../apps/workbench-web/src/workbench");
  const canonicalFrames = await import("../../apps/workbench-web/src/game-browser");
  expect(board.Board).toBe(canonicalBoard.Board); expect(board.fitBoardSize).toBe(canonicalBoard.fitBoardSize);
  expect(workbench.mountWorkbench).toBe(canonicalMount.mountWorkbench);
  expect(workbench.gameFrames).toBe(canonicalFrames.gameFrames);
  expect(workbench).not.toHaveProperty("PendingCommands"); expect(workbench).not.toHaveProperty("IntelligenceClient");
});

it("public contracts alias actual owner signatures rather than copying DTOs", () => {
  expectTypeOf<RepertoireReviewViewProps>().toEqualTypeOf<import("../../app/features/repertoire-review/RepertoireReviewView").RepertoireReviewViewProps>();
  expectTypeOf<RepertoireReviewClient>().toEqualTypeOf<import("../../app/features/repertoire-review/api").RepertoireReviewClient>();
  expectTypeOf<RepertoireReviewRead>().toEqualTypeOf<import("../../app/features/repertoire-review/api").RepertoireReviewRead>();
  expectTypeOf<BoardProps>().toEqualTypeOf<import("../../packages/board-ui/src/types").BoardProps>();
  expectTypeOf<WorkbenchOptions>().toEqualTypeOf<import("../../apps/workbench-web/src/workbench").WorkbenchOptions>();
  expectTypeOf<GamesReader>().toEqualTypeOf<import("../../apps/workbench-web/src/games-reader").GamesReader>();
});

it("keeps pure original-history and conservative Study navigation behavior through the public entry", async () => {
  const workbench = await import("../../apps/workbench-web/src/interfaces/index");
  const start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  expect(workbench.gameFrames(start, ["e2e4", "e7e5"])).toHaveLength(3);
  expect(workbench.studyUrl("https://study.example/")).toBe("https://study.example/");
  expect(workbench.studyUrl("https://study.example/?card=unverified")).toBeNull();
  expect(workbench.studyHandoffNotice("practical_gap")).toContain("does not identify an existing Study card");
});
