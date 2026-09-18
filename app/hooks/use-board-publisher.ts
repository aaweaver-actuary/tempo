import { useMemo, useState } from "react";
import {
  defaultBoardState,
  useBoardShellStore,
  type BoardShellOwner,
  type BoardShellSnapshot,
} from "../state/board-shell-store";

let nextBoardSession = 0;
// A mounted workspace owns a lease. Delayed work from a previous mount cannot
// publish into a later lease, even when both mounts have the same owner name.
export function useBoardPublisher() {
  const [session] = useState(() => ++nextBoardSession);
  return useMemo(
    () => ({
      setShellBoardForOwner(
        owner: BoardShellOwner,
        snapshot: Partial<BoardShellSnapshot>,
      ) {
        const completeSnapshot: BoardShellSnapshot = {
          ...defaultBoardState,
          ...snapshot,
          owner,
        };
        useBoardShellStore
          .getState()
          .setShellBoardForOwner(owner, completeSnapshot, session);
      },
      releaseShellBoardForOwner(owner: BoardShellOwner) {
        useBoardShellStore.getState().releaseShellBoardForOwner(owner, session);
      },
    }),
    [session],
  );
}
