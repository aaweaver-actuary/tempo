import { useCallback, useLayoutEffect, useRef } from "react";

// Event consumers retain one callback while reading the latest committed closure.
// Updating in a layout effect avoids exposing an abandoned concurrent render.
export function useCommittedCallback<Arguments extends unknown[], Result>(
  callback: (...arguments_: Arguments) => Result,
): (...arguments_: Arguments) => Result {
  const callbackRef = useRef(callback);
  useLayoutEffect(() => { callbackRef.current = callback; });
  return useCallback((...arguments_: Arguments) => callbackRef.current(...arguments_), []);
}
