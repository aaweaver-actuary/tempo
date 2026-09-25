import { useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { z } from "zod";
import { API_URL } from "./const";
import { analysisPasteCommitSchema, analysisPastePreviewSchema } from "./domain/schemas";
import { useDialogFocus } from "./hooks/use-dialog-focus";
import { readJsonResponse } from "./lib/validated-data";
import { usesLocalApi } from "./utils/local";
import CloseButton from "./components/buttons/CloseButton";

type PastePreview = z.infer<typeof analysisPastePreviewSchema>;
export type AnalysisPasteContext = {
  startingFen?: string;
  sourceGapId?: string;
};

export function AnalysisPasteDialog({ context, onClose, onSaved }: {
  context: AnalysisPasteContext;
  onClose: () => void;
  onSaved: (affectedRepertoireIds: string[], conflictingRepertoireIds: string[]) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [text, setText] = useState("");
  const [startingFen, setStartingFen] = useState(context.startingFen ?? "");
  const [preview, setPreview] = useState<PastePreview>();
  const [destinations, setDestinations] = useState<Record<number, string>>({});
  const [included, setIncluded] = useState<Record<number, boolean>>({});
  const [confirmed, setConfirmed] = useState<Record<number, boolean>>({});
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [savedMessage, setSavedMessage] = useState("");
  const batchConflictIndices = useMemo(() => {
    const conflicts = new Set<number>();
    const responses = new Map<string, { move: string; index: number }>();
    for (const line of preview?.lines ?? []) {
      if (!included[line.index]) continue;
      const option = line.options.find((candidate) => candidate.repertoire_id === destinations[line.index]);
      if (!option?.trained_color) continue;
      const board = new Chess(line.starting_fen);
      for (const move of line.moves) {
        if (board.turn() === option.trained_color[0]) {
          const key = `${option.repertoire_id}:${board.fen().split(" ").slice(0, 4).join(" ")}`;
          const previous = responses.get(key);
          if (previous && previous.move !== move) {
            conflicts.add(line.index);
            conflicts.add(previous.index);
          } else responses.set(key, { move, index: line.index });
        }
        board.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
      }
    }
    return conflicts;
  }, [preview, destinations, included]);

  function updateText(value: string) {
    setText(value);
    setPreview(undefined);
    setError("");
    setSavedMessage("");
  }

  async function showPreview() {
    setWorking(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/repertoire/paste/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, starting_fen: startingFen || null, source_gap_id: context.sourceGapId }),
      });
      const result = await readJsonResponse(response, analysisPastePreviewSchema, "analysis paste preview");
      setPreview(result);
      setDestinations(Object.fromEntries(result.lines.map((line) => [line.index, line.suggested_repertoire_id ?? ""])));
      setIncluded(Object.fromEntries(result.lines.map((line) => [line.index, true])));
      setConfirmed({});
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not preview the pasted analysis.");
    } finally {
      setWorking(false);
    }
  }

  async function saveLines() {
    if (!preview) return;
    const selections = preview.lines.filter((line) => included[line.index]).map((line) => ({
      index: line.index,
      repertoire_id: destinations[line.index],
      acknowledge_conflict: Boolean(confirmed[line.index]),
    }));
    if (!selections.length || selections.some((item) => !item.repertoire_id)) {
      setError("Choose a repertoire for every included line, or skip that line.");
      return;
    }
    const unconfirmed = selections.find((item) => preview.lines[item.index].options.some((option) =>
      option.repertoire_id === item.repertoire_id && option.conflicts.length > 0 && !item.acknowledge_conflict));
    if (unconfirmed) {
      setError("Confirm each trained-move conflict before saving.");
      return;
    }
    if (selections.some((item) => batchConflictIndices.has(item.index) && !item.acknowledge_conflict)) {
      setError("Confirm conflicting trained moves between pasted lines before saving.");
      return;
    }
    if (selections.some((item) => preview.lines[item.index].options.some((option) =>
      option.repertoire_id === item.repertoire_id && !option.trainable))) {
      setError("Each included line must finish with a move by the side being trained.");
      return;
    }
    setWorking(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/repertoire/paste/commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text, starting_fen: startingFen || null, source_gap_id: context.sourceGapId,
          preview_token: preview.preview_token, selections,
        }),
      });
      const result = await readJsonResponse(response, analysisPasteCommitSchema, "saved pasted analysis");
      const conflictingRepertoireIds = [...new Set(result.saved.filter((line) => line.conflict).map((line) => line.repertoire_id))];
      setSavedMessage(`Saved ${result.saved.filter((line) => !line.duplicate).length} line(s)${result.gap_resolved ? " and resolved the coverage gap" : ""}.`);
      onSaved(result.affected_repertoire_ids, conflictingRepertoireIds);
    } catch (failure) {
      if (failure instanceof Error && failure.message.includes("Preview again")) setPreview(undefined);
      setError(failure instanceof Error ? failure.message : "Could not save the pasted analysis.");
    } finally {
      setWorking(false);
    }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="import-dialog analysis-paste-dialog" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="analysis-paste-title" onMouseDown={(event) => event.stopPropagation()}>
      <CloseButton onClose={onClose} ariaLabel="Close paste analysis" />
      <p className="eyebrow">Repertoire analysis</p>
      <h2 id="analysis-paste-title">Paste analysis</h2>
      <p className="dialog-copy">Paste SAN lines separated by blank lines, or PGN with variations. {context.startingFen ? "Partial moves start from the selected position." : "Partial moves need a FEN or an open Builder position."}</p>
      {!usesLocalApi() && <p role="status">Saving pasted analysis requires local Docker Tempo. This practice demo does not save repertoire changes.</p>}
      <label className="analysis-paste-input">SAN or PGN
        <textarea aria-label="SAN or PGN" value={text} onChange={(event) => updateText(event.target.value)} rows={7} placeholder="1. e4 e5 2. Nf3 Nc6\n\n1. d4 d5 2. c4" />
      </label>
      {!context.startingFen && <label className="analysis-paste-input">Starting FEN for a partial line (optional)
        <input aria-label="Starting FEN" value={startingFen} onChange={(event) => { setStartingFen(event.target.value); setPreview(undefined); }} placeholder="Paste a FEN if the moves start midgame" />
      </label>}
      {error && <p className="editor-error" role="alert">{error}</p>}
      {savedMessage && <p role="status">{savedMessage}</p>}
      {preview && <div className="analysis-paste-preview" aria-label="Pasted line preview">
        <h3>Review {preview.lines.length} {preview.lines.length === 1 ? "line" : "lines"}</h3>
        {preview.lines.map((line) => {
          const selectedOption = line.options.find((option) => option.repertoire_id === destinations[line.index]);
          return <div className="analysis-paste-line" key={line.index}>
            <label><input type="checkbox" checked={included[line.index] ?? true} onChange={(event) => setIncluded((current) => ({ ...current, [line.index]: event.target.checked }))} /> Include line {line.index + 1}</label>
            <p>{line.san}</p>
            <label>Destination repertoire
              <select aria-label={`Destination for line ${line.index + 1}`} value={destinations[line.index] ?? ""} disabled={!included[line.index]} onChange={(event) => {
                setDestinations((current) => ({ ...current, [line.index]: event.target.value }));
                setConfirmed((current) => ({ ...current, [line.index]: false }));
              }}>
                <option value="">Choose a repertoire</option>
                {line.options.map((option) => <option key={option.repertoire_id} value={option.repertoire_id}>{option.name}{option.matched ? " · matching route" : ""}</option>)}
              </select>
            </label>
            {selectedOption?.duplicate && <p>Already in this repertoire; saving will not duplicate it.</p>}
            {selectedOption && !selectedOption.trainable && <p role="status">This line needs one more move by the side trained in this repertoire.</p>}
            {selectedOption?.conflicts.length || batchConflictIndices.has(line.index) ? <div className="analysis-paste-conflict">
              <p>Trained-move conflict{batchConflictIndices.has(line.index) ? " with another selected line" : ""}: {selectedOption?.conflicts.map((conflict) => `${conflict.existing_moves.join("/")} vs ${conflict.pasted_move}`).join(", ")}. Tempo will open integrity repair after saving.</p>
              <label><input type="checkbox" checked={confirmed[line.index] ?? false} onChange={(event) => setConfirmed((current) => ({ ...current, [line.index]: event.target.checked }))} /> Save this conflict and open repair</label>
            </div> : null}
          </div>;
        })}
      </div>}
      <div className="dialog-footer">
        <button disabled={!usesLocalApi() || !text.trim() || working} onClick={() => void showPreview()}>{working ? "Working…" : "Preview lines"}</button>
        <button className="primary-button" disabled={!usesLocalApi() || !preview || working} onClick={() => void saveLines()}>{working ? "Saving…" : "Save selected lines"}</button>
      </div>
    </section>
  </div>;
}
