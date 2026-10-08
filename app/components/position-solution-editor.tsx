import { historyKeyboardActions } from "../lib/keyboard-shortcuts";
import { useState } from "react";
import { TextInput } from "./inputs/TextInput";
import { Button } from "./buttons/BaseButton";
import { TextArea } from "./ui";
import { SelectInput } from "./inputs/SelectInput";
import { MoveNavigationControls } from "./board/MoveNavigationControls";
import { Chessboard, type BoardTheme, type PieceSet, type PromotionPiece } from "./chessboard";
import { pieceSymbols, STANDARD_FEN } from "../const";
import { EMPTY_SETUP_FEN, type PositionSolutionState } from "../hooks/use-position-solution-editor";

export function PositionSolutionTabs({ editor, requirePlayable = false }: { editor: PositionSolutionState; requirePlayable?: boolean }) {
  return <div className="editor-tabs">
    <Button className={editor.tab === "position" ? "active" : ""} onClick={() => editor.setTab("position")}>Position</Button>
    <Button className={editor.tab === "solution" ? "active" : ""} disabled={requirePlayable && Boolean(editor.positionError)}
      onClick={() => editor.setTab("solution")}>Solution</Button>
  </div>;
}

export function PositionSolutionBoard({ editor, theme, pieceSet, setupControls = false, locked = false, orientation, enableSanEntry = false }: {
  editor: PositionSolutionState; theme: BoardTheme; pieceSet: PieceSet; setupControls?: boolean; locked?: boolean;
  orientation?: "white" | "black"; enableSanEntry?: boolean;
}) {
  const [sanText, setSanText] = useState("");
  const inputLocked = locked || editor.pendingFen !== null;
  function addSanMoves() {
    if (!inputLocked && editor.playSanSolution(sanText)) setSanText("");
  }
  return <div className="editor-board-column" data-board-keyboard-scope>
    {editor.tab === "position" && <div className="piece-palette">
      {setupControls && <Button className={editor.piece === null ? "active move-pieces" : "move-pieces"}
        disabled={locked} onClick={() => editor.setPiece(null)}>Move pieces</Button>}
      {Object.entries(pieceSymbols).map(([id, symbol]) => <Button key={id || "remove"}
        disabled={locked} className={editor.piece === id ? "active" : ""}
        onClick={() => editor.setPiece(id)} aria-label={id ? `Place ${id}` : "Remove piece"}>{symbol}</Button>)}
    </div>}
    <Chessboard keyboard={{
      ...(editor.tab === "solution" && !inputLocked ? historyKeyboardActions(editor.cursor, editor.moves.length, editor.setCursor) : {}),
      reset: () => { if (editor.tab === "solution") editor.setCursor(Math.min(editor.workingCursor, editor.moves.length)); },
    }} orientation={orientation} fen={editor.tab === "position" ? editor.boardFen : editor.previewFen}
      locked={locked || editor.pendingFen !== null} showHint={false} theme={theme} pieceSet={pieceSet}
      editMode={editor.tab === "position"} onSquareSelect={editor.placePiece}
      promotion={editor.promotion} onFreeMove={editor.moveSetupPiece} onMove={editor.playSolution} />
    {editor.tab === "solution" && <>
      <MoveNavigationControls cursor={editor.cursor} length={editor.moves.length} onChange={editor.setCursor} />
      <div className="solution-line">{editor.moves.length ? editor.moves.map((move, index) =>
        <Button className={index < editor.cursor ? "shown" : ""} key={`${move}-${index}`}
          onClick={() => editor.setCursor(index + 1)}>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ""}{move}</Button>)
        : <span>{enableSanEntry ? "Play the solution on the board or enter SAN moves." : "Play the solution on the board."}</span>}</div>
      {enableSanEntry && <div className="san-entry">
        <label>SAN moves<TextInput value={sanText} disabled={inputLocked} autoComplete="off" spellCheck={false}
          aria-describedby="capture-san-help" placeholder="e4 e5 Nf3"
          onChange={event => setSanText(event.target.value)} onKeyDown={event => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault(); event.stopPropagation(); addSanMoves();
            }
          }} /></label>
        <p id="capture-san-help" className="editor-key-help">Enter one move or a line, with optional move numbers.
          Moves start at the selected position and replace any continuation.</p>
        <Button disabled={inputLocked || !sanText.trim()} onClick={addSanMoves}>Add moves</Button>
      </div>}
      <label>Promotion <SelectInput value={editor.promotion} disabled={locked} onChange={event => editor.setPromotion(event.target.value as PromotionPiece)}>
        <option value="q">Queen</option><option value="r">Rook</option><option value="b">Bishop</option><option value="n">Knight</option>
      </SelectInput></label>
    </>}
  </div>;
}

export function PositionFenField({ editor, setupControls = false, locked = false }: {
  editor: PositionSolutionState; setupControls?: boolean; locked?: boolean;
}) {
  return <>
    <label>FEN<TextArea aria-label="FEN" value={editor.startingFen} disabled={locked}
      onChange={event => editor.changeStartingFen(event.target.value)} /></label>
    {setupControls && <>
      <label>To move<SelectInput value={editor.boardFen.split(/\s+/)[1]} disabled={locked}
        onChange={event => { const fields = editor.boardFen.split(/\s+/); fields[1] = event.target.value; editor.changeStartingFen(fields.join(" ")); }}>
        <option value="w">White</option><option value="b">Black</option>
      </SelectInput></label>
      <div className="editor-actions"><Button disabled={locked} onClick={() => editor.changeStartingFen(EMPTY_SETUP_FEN)}>Clear</Button>
        <Button disabled={locked} onClick={() => editor.changeStartingFen(STANDARD_FEN)}>Standard position</Button></div>
      {editor.positionError && <p className="editor-key-help">{editor.positionError}</p>}
    </>}
    {editor.pendingFen !== null && <div role="alert" className="capture-confirmation">
      <p>Changing the starting position will clear the recorded solution.</p>
      <Button onClick={editor.confirmStartingChange}>Change position and clear solution</Button>
      <Button onClick={editor.cancelStartingChange}>Keep position</Button>
    </div>}
  </>;
}
