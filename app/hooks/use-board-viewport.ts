import { useLayoutEffect, useState, type RefObject } from "react";

// Keep every square an integral CSS-pixel size, including after browser zoom.
export function fitBoardSurface(
  bounds: { width: number; height: number },
  frameInset: number,
): number {
  return Math.max(
    0,
    Math.floor(
      (Math.min(bounds.width, bounds.height, 778) - frameInset * 2) / 8,
    ) * 8,
  );
}

export function useBoardViewport(
  hostRef: RefObject<HTMLDivElement | null>,
  frameInset = 9,
) {
  const [surfaceSize, setSurfaceSize] = useState(0);
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const bounds = host.getBoundingClientRect();
      // Reserve the workspace's move history/toolbar below the board, not just
      // the board itself. Constrain its host so centering cannot put it lower.
      const visibleHeight = Math.max(
        0,
        (window.visualViewport?.height ?? window.innerHeight) -
          bounds.top -
          140,
      );
      host.style.maxHeight = `${visibleHeight}px`;
      const nextSize = fitBoardSurface(
        { width: bounds.width, height: Math.min(bounds.height, visibleHeight) },
        frameInset,
      );
      setSurfaceSize((current) => (current === nextSize ? current : nextSize));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(host);
    window.visualViewport?.addEventListener("resize", schedule);
    window.addEventListener("resize", schedule);
    measure();
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      window.visualViewport?.removeEventListener("resize", schedule);
      window.removeEventListener("resize", schedule);
    };
  }, [hostRef, frameInset]);
  return surfaceSize;
}
