import { useState } from "react";
import { Chess } from "chess.js";
import { Dialog } from "./dialog";
import { Button } from "./buttons/BaseButton";
import CloseButton from "./buttons/CloseButton";
import { TextInput } from "./inputs/TextInput";
import { SelectInput } from "./inputs/SelectInput";
import { TextArea } from "./ui";
import type { BoardTheme, PieceSet } from "./chessboard";
import { PositionFenField, PositionSolutionBoard, PositionSolutionTabs } from "./position-solution-editor";
import { EMPTY_SETUP_FEN, solutionUciMoves, usePositionSolutionEditor } from "../hooks/use-position-solution-editor";
import { asSanMove } from "../types";
import { pendingTacticCapture, saveTacticCapture, type TacticCaptureRequest } from "../lib/tactic-capture-command";
import { invalidateWorkspaceData } from "../lib/workspace-data";
import { publishNotification } from "../lib/notifications";
import { usesLocalApi } from "../utils/local";
import { parseChessComPuzzlePgn } from "../lib/tactic-capture-pgn";

export function TacticCaptureDialog({ theme, pieceSet, onClose, onQueueChanged }: {
  theme: BoardTheme; pieceSet: PieceSet; onClose: () => void; onQueueChanged: () => void;
}) {
  const [restored] = useState(() => {
    try { return { request: pendingTacticCapture(), error: "" }; }
    catch (error) { return { request: null, error: String(error) }; }
  });
  const [pending, setPending] = useState(restored.request !== null || Boolean(restored.error));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(restored.error);
  const [source, setSource] = useState<TacticCaptureRequest["source_kind"]>(restored.request?.source_kind ?? "manual");
  const [reference, setReference] = useState(restored.request?.source_ref ?? "");
  const [url, setUrl] = useState(restored.request?.source_url ?? "");
  const [note, setNote] = useState(restored.request?.note ?? "");
  const [pgnExpanded, setPgnExpanded] = useState(false);
  const [pgnText, setPgnText] = useState("");
  const [pgnError, setPgnError] = useState("");
  const [loadedPgnVersion, setLoadedPgnVersion] = useState(0);
  const [initialMoves] = useState(() => {
    if (!restored.request) return [];
    try {
    const board = new Chess(restored.request.starting_fen);
    return restored.request.moves.map(uci => asSanMove(board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] }).san));
    } catch { return []; }
  });
  const editor = usePositionSolutionEditor(restored.request?.starting_fen ?? EMPTY_SETUP_FEN, initialMoves, true);
  const locked = saving || pending;
  function loadPgn() {
    if (locked) return;
    try {
      const imported = parseChessComPuzzlePgn(pgnText);
      editor.loadPositionAndSolution(imported.startingFen, imported.solutionSanMoves);
      setSource(imported.sourceKind);
      setReference(imported.sourceRef ?? "");
      setUrl(imported.sourceUrl ?? "");
      setLoadedPgnVersion(version => version + 1);
      setPgnError("");
      setError("");
    } catch (failure) {
      setPgnError(failure instanceof Error ? failure.message : "Could not load this Chess.com puzzle PGN.");
    }
  }
  async function save() {
    setSaving(true); setError("");
    try {
      const result = await saveTacticCapture(pending ? undefined : {
        starting_fen: editor.startingFen, moves: solutionUciMoves(editor.startingFen, editor.moves),
        source_kind: source, source_ref: reference.trim() || null, source_url: url.trim() || null, note,
      });
      invalidateWorkspaceData();
      onQueueChanged();
      publishNotification({ severity: "success", source: "tactic-capture",
        message: result.reused ? "Existing tactic added back to today’s queue." : "Added to today’s queue." });
      onClose();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save the capture. Retry to check its result.");
      try { setPending(pendingTacticCapture() !== null); } catch { setPending(true); }
    } finally { setSaving(false); }
  }
  return <Dialog titleId="capture-tactic-title" onClose={onClose} className="card-editor tactic-capture">
    <CloseButton onClose={onClose} />
    <div className="editor-heading"><div><p className="eyebrow">Today’s training</p><h2 id="capture-tactic-title">Capture tactic</h2></div></div>
    {!usesLocalApi() ? <p>Durable tactic capture requires local Tempo. Open your local Tempo workspace to add a position to training.</p> : <>
      {pending && <p role="status">A capture is awaiting confirmation. Check its result before editing or creating another capture.</p>}
      <fieldset disabled={locked} className="capture-editor">
        <div className="capture-pgn-input">
          <div className="capture-input-toolbar">
            <PositionSolutionTabs editor={editor} requirePlayable />
            <Button disabled={locked} aria-expanded={pgnExpanded} aria-controls="capture-pgn-panel"
              onClick={() => setPgnExpanded(expanded => !expanded)}>Paste Chess.com puzzle PGN</Button>
          </div>
          {pgnExpanded && <div id="capture-pgn-panel" className="capture-pgn-panel">
            <label>Chess.com puzzle PGN<TextArea value={pgnText} rows={8} disabled={locked} spellCheck={false}
              aria-describedby="capture-pgn-help" onChange={event => setPgnText(event.target.value)} /></label>
            <p id="capture-pgn-help" className="editor-key-help">The first move sets up the puzzle; the remaining moves become its solution.
              Load PGN replaces the position, solution, and source fields. Your note is kept.</p>
            <Button disabled={locked} onClick={loadPgn}>Load PGN</Button>
            {pgnError && <p className="editor-error" role="alert">{pgnError}</p>}
          </div>}
        </div>
        <div className="editor-layout">
          <PositionSolutionBoard key={loadedPgnVersion} editor={editor} theme={theme} pieceSet={pieceSet} setupControls locked={locked}
            orientation={editor.boardFen.trim().split(/\s+/)[1] === "b" ? "black" : "white"} enableSanEntry />
          <div className="editor-fields">
            <PositionFenField editor={editor} setupControls locked={locked} />
            <label>Source<SelectInput value={source} onChange={event => setSource(event.target.value as typeof source)}>
              <option value="puzzle_rush">Puzzle Rush</option><option value="game">Game</option><option value="manual">Manual</option><option value="other">Other</option>
            </SelectInput></label>
            <label>Reference (optional)<TextInput value={reference} maxLength={500} onChange={event => setReference(event.target.value)} /></label>
            <label>URL (optional)<TextInput type="url" value={url} maxLength={2000} onChange={event => setUrl(event.target.value)} /></label>
            <label>Note (optional)<TextArea value={note} maxLength={10000} onChange={event => setNote(event.target.value)} /></label>
          </div>
        </div>
      </fieldset>
      {(error || editor.error) && <p className="editor-error" role="alert">{error || editor.error}</p>}
      <div className="editor-actions"><Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" className="primary-button" pending={saving} disabled={saving || (!pending && (Boolean(editor.positionError) || !editor.moves.length || editor.pendingFen !== null))}
          onClick={() => void save()}>{pending ? "Check pending capture" : "Add to training"}</Button></div>
    </>}
  </Dialog>;
}
