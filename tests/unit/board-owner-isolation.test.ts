import { beforeEach, expect, it } from "vitest";
import {
  defaultBoardState,
  useBoardShellStore,
} from "../../app/state/board-shell-store";
import { STANDARD_FEN } from "../../app/const";

beforeEach(() =>
  useBoardShellStore.setState(useBoardShellStore.getInitialState(), true),
);
it("builder annotations never appear in games", () => {
  const actions = useBoardShellStore.getState();
  const oldMove = () => {};
  actions.setShellBoardForOwner("builder", {
    ...defaultBoardState,
    fen: STANDARD_FEN,
    drawnShapes: [{ orig: "e4", brush: "green" }],
    shapes: [{ orig: "e2", dest: "e4", brush: "blue" }],
    onMove: oldMove,
  });
  actions.setShellBoardForOwner("games", {
    ...defaultBoardState,
    fen: STANDARD_FEN,
    interactionMode: "readonly",
  });
  expect(useBoardShellStore.getState().board.drawnShapes).toEqual([]);
  expect(useBoardShellStore.getState().board.shapes).toEqual([]);
  expect(useBoardShellStore.getState().board.onMove).toBeUndefined();
});

it("stale sessions cannot overwrite or release a replacement owner", () => {
  const actions = useBoardShellStore.getState();
  actions.setShellBoardForOwner(
    "builder",
    { ...defaultBoardState, fen: STANDARD_FEN },
    20,
  );
  actions.setShellBoardForOwner(
    "games",
    { ...defaultBoardState, fen: STANDARD_FEN },
    21,
  );
  actions.setShellBoardForOwner(
    "builder",
    { ...defaultBoardState, showHint: true },
    20,
  );
  actions.releaseShellBoardForOwner("games", 20);
  expect(useBoardShellStore.getState().board.owner).toBe("games");
  expect(useBoardShellStore.getState().board.showHint).toBe(false);
});
it("all board owner pairs clear transient fields on acquisition", () => {
  const owners = ["train", "tactics", "endgames", "builder", "games"] as const;
  for (const source of owners)
    for (const destination of owners.filter((owner) => owner !== source)) {
      const store = useBoardShellStore.getState();
      store.setShellBoardForOwner(source, {
        ...defaultBoardState,
        showHint: true,
        expectedSan: "e4",
        lastMove: ["e2", "e4"],
        drawnShapes: [{ orig: "e4", brush: "green" }],
        onFlip: () => {},
      });
      store.setShellBoardForOwner(destination, {
        ...defaultBoardState,
        fen: STANDARD_FEN,
      });
      expect(useBoardShellStore.getState().board).toMatchObject({
        owner: destination,
        showHint: false,
        drawnShapes: [],
        shapes: [],
      });
      for (const field of ["expectedSan", "lastMove", "onFlip"] as const) expect(useBoardShellStore.getState().board[field]).toBeUndefined();
    }
});
