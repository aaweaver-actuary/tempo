import { StrictMode, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Board, type BoardHandle, type BoardMode, type BoardProps, type MoveEvent } from "@tempo/board-ui";
import "@tempo/board-ui/styles.css";

const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const initial: BoardProps = { fen, positionIdentity: "first", positionRevision: 0,
  orientation: "white", turn: "white", check: false, mode: "legal",
  destinations: new Map([["e2", ["e3", "e4"]], ["d2", ["d3", "d4"]]]) };

function Consumer() {
  const [props, setProps] = useState(initial);
  const [status, setStatus] = useState(0);
  const [moves, setMoves] = useState<MoveEvent[]>([]);
  const [handlerStatuses, setHandlerStatuses] = useState<number[]>([]);
  const [heading, setHeading] = useState<HTMLDivElement | null>(null);
  const [banner, setBanner] = useState(false);
  const [mounted, setMounted] = useState(true);
  const board = useRef<BoardHandle>(null);
  const recordMove = (event: MoveEvent) => {
    setMoves(current => [...current, event]); setHandlerStatuses(current => [...current, status]);
  };
  return <>
    <h1>Installed Board UI consumer</h1>
    <div ref={setHeading} id="heading">{banner && <p style={{ padding: 30 }}>A heading changes the board's vertical position.</p>}</div>
    <div id="board-container" style={{ width: "min(100%, 643px)" }}>
      {mounted && <Board {...props} ref={board} layoutAnchor={heading} ariaDescribedBy="keyboard-help"
        onMove={event => recordMove(event)} onFreeMove={event => recordMove(event)}
        onKeyDown={event => { if (event.key === "Escape") board.current?.clearInput(); }} />}
    </div>
    <p id="keyboard-help">Focus the board. Escape cancels input. Application navigation belongs to this consumer.</p>
    <output id="moves">{JSON.stringify(moves)}</output><output id="status">{status}</output>
    <output id="handler-statuses">{JSON.stringify(handlerStatuses)}</output>
    <button id="status-update" onClick={() => { setStatus(value => value + 1); setProps(value => ({ ...value,
      destinations: new Map([["e2", ["e4", "e3"]], ["d2", ["d4", "d3"]]]) })); }}>Status update</button>
    <button id="annotations" onClick={() => setProps(value => ({ ...value, theme: "blue", pieceSet: "merida",
      lastMove: ["a7", "a6"], shapes: [{ orig: "g1", dest: "f3", brush: "blue", label: { text: "Plan" } }] }))}>Annotations</button>
    <button id="replace" onClick={() => setProps(value => ({ ...value, positionIdentity: "replacement" }))}>Replace same FEN</button>
    <button id="revision" onClick={() => setProps(value => ({ ...value, positionRevision: value.positionRevision + 1 }))}>Next revision</button>
    <button id="lock" onClick={() => setProps(value => ({ ...value, locked: !value.locked }))}>Lock</button>
    <button id="flip" onClick={() => setProps(value => ({ ...value, orientation: value.orientation === "white" ? "black" : "white" }))}>Flip</button>
    {(["legal", "free", "select"] as BoardMode[]).map(mode => <button id={`mode-${mode}`} key={mode}
      onClick={() => setProps(value => ({ ...value, mode }))}>{mode}</button>)}
    <button id="translate" onClick={() => setBanner(value => !value)}>Translate</button>
    <button id="focus" onClick={() => board.current?.focus()}>Focus board</button>
    <button id="remount" onClick={() => setMounted(value => !value)}>Toggle mount</button>
    <button id="reset" onClick={() => { setProps(initial); setMoves([]); setHandlerStatuses([]); }}>Reset</button>
  </>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Consumer /></StrictMode>);
