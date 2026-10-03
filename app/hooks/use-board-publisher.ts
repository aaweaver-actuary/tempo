import { useLayoutEffect, useRef } from "react";
import {
  defaultBoardState,
  useBoardShellStore,
  type BoardShellOwner,
  type BoardShellSnapshot,
} from "../state/board-shell-store";
import { useCommittedCallback } from "./use-committed-callback";

export function useBoardPublisher(owner: BoardShellOwner, snapshot: Partial<BoardShellSnapshot> | null) {
  const sessionRef = useRef<number | null>(null);
  const onMove = useCommittedCallback<Parameters<NonNullable<BoardShellSnapshot["onMove"]>>, void>(
    (...arguments_) => snapshot?.onMove?.(...arguments_));
  const onFreeMove = useCommittedCallback<Parameters<NonNullable<BoardShellSnapshot["onFreeMove"]>>, void>(
    (...arguments_) => snapshot?.onFreeMove?.(...arguments_));
  const onSquareSelect = useCommittedCallback<Parameters<NonNullable<BoardShellSnapshot["onSquareSelect"]>>, void>(
    (...arguments_) => snapshot?.onSquareSelect?.(...arguments_));
  const onDrawnShapesChange = useCommittedCallback<Parameters<NonNullable<BoardShellSnapshot["onDrawnShapesChange"]>>, void>(
    (...arguments_) => snapshot?.onDrawnShapesChange?.(...arguments_));
  const onFlip = useCommittedCallback(() => snapshot?.onFlip?.());
  const onHintExposure = useCommittedCallback((fen: string) => snapshot?.onHintExposure?.(fen));
  const previous = useCommittedCallback(() => snapshot?.keyboard?.previous?.());
  const next = useCommittedCallback(() => snapshot?.keyboard?.next?.());
  const start = useCommittedCallback(() => snapshot?.keyboard?.start?.());
  const end = useCommittedCallback(() => snapshot?.keyboard?.end?.());
  const reset = useCommittedCallback(() => snapshot?.keyboard?.reset?.());
  const hint = useCommittedCallback(() => snapshot?.keyboard?.hint?.());
  const nextItem = useCommittedCallback(() => snapshot?.keyboard?.nextItem?.());
  const completeSnapshot: BoardShellSnapshot = {
    ...defaultBoardState, ...snapshot, owner,
    keyboard: snapshot?.keyboard ? { ...snapshot.keyboard,
      previous: snapshot.keyboard.previous ? previous : undefined, next: snapshot.keyboard.next ? next : undefined,
      start: snapshot.keyboard.start ? start : undefined, end: snapshot.keyboard.end ? end : undefined,
      reset: snapshot.keyboard.reset ? reset : undefined, hint: snapshot.keyboard.hint ? hint : undefined,
      nextItem: snapshot.keyboard.nextItem ? nextItem : undefined,
    } : undefined,
    onMove: snapshot?.onMove ? onMove : undefined,
    onFreeMove: snapshot?.onFreeMove ? onFreeMove : undefined,
    onSquareSelect: snapshot?.onSquareSelect ? onSquareSelect : undefined,
    onDrawnShapesChange: snapshot?.onDrawnShapesChange ? onDrawnShapesChange : undefined,
    onFlip: snapshot?.onFlip ? onFlip : undefined,
    onHintExposure: snapshot?.onHintExposure ? onHintExposure : undefined,
  };
  const committedSnapshot = useRef(completeSnapshot);
  useLayoutEffect(() => { committedSnapshot.current = completeSnapshot; });
  const enabled = snapshot !== null;
  useLayoutEffect(() => {
    if (!enabled) return;
    const session = useBoardShellStore.getState().acquireShellBoardForOwner(owner, committedSnapshot.current);
    sessionRef.current = session;
    return () => {
      useBoardShellStore.getState().releaseShellBoardForOwner(owner, session);
      sessionRef.current = null;
    };
  }, [owner, enabled]);
  useLayoutEffect(() => {
    if (enabled && sessionRef.current !== null)
      useBoardShellStore.getState().updateShellBoardForOwner(owner, completeSnapshot, sessionRef.current);
  });
}
