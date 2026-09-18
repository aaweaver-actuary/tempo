"use client";
import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { PersistentBoardShell } from "./persistent-board-shell";
import { useBoardShellStore } from "../state/board-shell-store";

const BoardToolbarTarget = createContext<HTMLDivElement | null>(null);
export function BoardTools({ children }: { children: ReactNode }) {
  const toolbarTarget = useContext(BoardToolbarTarget);
  return toolbarTarget ? (
    createPortal(children, toolbarTarget)
  ) : (
    <div className="board-tools">{children}</div>
  );
}
export const DEFAULT_BOARD_SPLIT = 46;
export function readBoardSplit(raw: string | null): number {
  try {
    const value = JSON.parse(raw ?? "null");
    return value?.version === 1 &&
      typeof value.percent === "number" &&
      Number.isFinite(value.percent) &&
      value.percent >= 20 &&
      value.percent <= 75
      ? value.percent
      : DEFAULT_BOARD_SPLIT;
  } catch {
    return DEFAULT_BOARD_SPLIT;
  }
}
export function BoardWorkspace({
  children,
  view,
  enabled = true,
}: {
  children: ReactNode;
  view: string;
  enabled?: boolean;
}) {
  const workspaceRef = useRef<HTMLElement>(null);
  const boardColumnRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [toolbarTarget, setToolbarTarget] = useState<HTMLDivElement | null>(
    null,
  );
  const [split, setSplit] = useState(() =>
    readBoardSplit(
      typeof localStorage === "undefined"
        ? null
        : localStorage.getItem("tempo-board-split"),
    ),
  );
  const board = useBoardShellStore((state) => state.board);
  const dragPointer = useRef<number | null>(null);
  function changeSplit(percent: number) {
    const clamped = Math.max(20, Math.min(75, percent));
    setSplit(clamped);
    try {
      localStorage.setItem(
        "tempo-board-split",
        JSON.stringify({ version: 1, percent: clamped }),
      );
    } catch {
      /* Layout still works when browser storage is unavailable. */
    }
  }
  useLayoutEffect(() => {
    const workspace = workspaceRef.current;
    const boardColumn = boardColumnRef.current;
    if (!workspace || !boardColumn) return;
    let resizeFrame = 0;
    const measure = () => {
      resizeFrame = 0;
      const headerHeight =
        document.querySelector(".topbar")?.getBoundingClientRect().height ?? 70;
      const toolbarHeight =
        toolbarRef.current?.getBoundingClientRect().height ?? 44;
      const viewportHeight =
        window.visualViewport?.height ?? window.innerHeight;
      const availableWidth = workspace.getBoundingClientRect().width;
      const desktop = window.innerWidth >= 1100 && viewportHeight >= 600;
      const allocatedWidth = desktop
        ? Math.max(
            338,
            Math.min(availableWidth - 388, (availableWidth * split) / 100),
          )
        : Math.min(658, availableWidth);
      const boardWidth = Math.min(
        allocatedWidth,
        desktop
          ? Math.max(
              338,
              viewportHeight - headerHeight - 48 - toolbarHeight - 12,
            )
          : 658,
        778,
      );
      workspace.style.setProperty("--board-column-width", `${boardWidth}px`);
      workspace.style.setProperty("--board-frame-width", `${boardWidth}px`);
      boardColumn.style.setProperty("--board-frame-width", `${boardWidth}px`);
    };
    const schedule = () => {
      if (!resizeFrame) resizeFrame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(workspace);
    if (toolbarRef.current) observer.observe(toolbarRef.current);
    window.addEventListener("resize", schedule);
    window.visualViewport?.addEventListener("resize", schedule);
    measure();
    return () => {
      observer.disconnect();
      cancelAnimationFrame(resizeFrame);
      window.removeEventListener("resize", schedule);
      window.visualViewport?.removeEventListener("resize", schedule);
    };
  }, [split, enabled]);
  return (
    <section
      ref={workspaceRef}
      className={
        enabled ? "unified-board-shell-layout" : "application-workspace"
      }
      data-view={view}
      style={{ "--board-split": `${split}%` } as CSSProperties}
    >
      <div
        className="workspace-board-column"
        ref={boardColumnRef}
        hidden={!enabled}
      >
        <PersistentBoardShell />
        <div className="shared-board-toolbar" ref={toolbarRef}>
          <div className="board-tools" aria-label="Board controls">
            <button
              disabled={Boolean(board.unavailable)}
              aria-label="Flip board"
              title="Flip board (F)"
              onClick={() => {
                if (board.onFlip) board.onFlip();
                else window.dispatchEvent(new Event("tempo:flip-board"));
              }}
            >
              ⇅ <span>Flip</span>
            </button>
            <div className="workspace-board-actions" ref={setToolbarTarget} />
          </div>
        </div>
      </div>
      <div className="board-divider" hidden={!enabled}>
        <div
          role="separator"
          aria-label="Board size"
          aria-orientation="vertical"
          aria-valuenow={Math.round(split)}
          aria-valuemin={20}
          aria-valuemax={75}
          tabIndex={0}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key))
              return;
            event.preventDefault();
            event.stopPropagation();
            changeSplit(
              event.key === "Home"
                ? DEFAULT_BOARD_SPLIT
                : split + (event.key === "ArrowRight" ? 2 : -2),
            );
          }}
          onPointerDown={(event) => {
            dragPointer.current = event.pointerId;
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (dragPointer.current !== event.pointerId) return;
            const bounds = workspaceRef.current!.getBoundingClientRect();
            changeSplit(((event.clientX - bounds.left) / bounds.width) * 100);
          }}
          onPointerUp={() => {
            dragPointer.current = null;
          }}
          onPointerCancel={() => {
            dragPointer.current = null;
          }}
        />
        <button
          aria-label="Reset board size"
          title="Reset board size"
          onClick={() => changeSplit(DEFAULT_BOARD_SPLIT)}
        >
          ↺
        </button>
      </div>
      <BoardToolbarTarget.Provider value={toolbarTarget}>
        <div
          className={
            enabled ? "unified-board-shell-panel" : "application-content"
          }
        >
          {children}
        </div>
      </BoardToolbarTarget.Provider>
    </section>
  );
}
