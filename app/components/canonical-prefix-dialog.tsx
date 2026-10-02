import { useCallback, useEffect, useRef, useState } from "react";
import { Chess } from "chess.js";
import { API_URL } from "../const";
import { canonicalPrefixSchema, canonicalPrefixPreviewSchema, type CanonicalPrefixPreview } from "../domain/canonical-prefix";
import { requestCanonicalPrefixPreview, recoverCanonicalPrefixPreview, recoverCanonicalPrefixSave, saveCanonicalPrefixCommand } from "../lib/canonical-prefix-command";
import { readJsonResponse } from "../lib/validated-data";
import { useDialogFocus } from "../hooks/use-dialog-focus";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import { Button } from "./buttons/BaseButton";
import CloseButton from "./buttons/CloseButton";
import { TextInput } from "./inputs/TextInput";

export function CanonicalPrefixDialog({ repertoireId, repertoireName, side, theme, pieceSet, onClose, onSaved }: {
  repertoireId: string; repertoireName: string; side: "white" | "black";
  theme: BoardTheme; pieceSet: PieceSet; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const generation = useRef(0);
  const [movetext, setMovetext] = useState("");
  const [preview, setPreview] = useState<CanonicalPrefixPreview>();
  const [previewId, setPreviewId] = useState<string>();
  const [revision, setRevision] = useState(0);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [fen, setFen] = useState(new Chess().fen());
  const [conflictCursor, setConflictCursor] = useState<string | null>(null);

  const checkPrefix = useCallback(async (text: string) => {
    const requestGeneration = ++generation.current;
    setWorking(true); setError(""); setPreview(undefined); setPreviewId(undefined);
    try {
      const admitted = await requestCanonicalPrefixPreview(repertoireId, text);
      if (generation.current !== requestGeneration) return;
      setFen(admitted.ending_fen); setRevision(admitted.revision); setPreviewId(admitted.preview_id);
    } catch (failure) {
      if (generation.current === requestGeneration) setError(failure instanceof Error ? failure.message : "Could not check the canonical prefix");
    } finally {
      if (generation.current === requestGeneration) setWorking(false);
    }
  }, [repertoireId]);

  const load = useCallback(async () => {
    const loadGeneration = generation.current;
    setWorking(true); setError("");
    try {
      await recoverCanonicalPrefixSave(repertoireId);
      const recoveredPreview = await recoverCanonicalPrefixPreview(repertoireId);
      if (generation.current !== loadGeneration) return;
      if (recoveredPreview) {
        setMovetext(recoveredPreview.san); setRevision(recoveredPreview.revision);
        setFen(recoveredPreview.ending_fen); setPreviewId(recoveredPreview.preview_id);
        return;
      }
      const current = await readJsonResponse(await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/canonical-prefix`), canonicalPrefixSchema, "canonical prefix");
      if (generation.current !== loadGeneration) return;
      setMovetext(current.san); setRevision(current.revision); setFen(current.ending_fen);
      await checkPrefix(current.san);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not load the canonical prefix");
    } finally { setWorking(false); }
  }, [repertoireId, checkPrefix]);

  useEffect(() => {
    let cancelled = false;
    const requestGeneration = generation;
    queueMicrotask(() => { if (!cancelled) void load(); });
    return () => { cancelled = true; requestGeneration.current++; };
  }, [load]);
  const previewState = preview?.state;
  useEffect(() => {
    if (!previewId || (previewState && previewState !== "checking")) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await readJsonResponse(await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/canonical-prefix/preview/${encodeURIComponent(previewId)}`), canonicalPrefixPreviewSchema, "canonical prefix compatibility");
        if (cancelled) return;
        setPreview(next); setSuggestion(next.suggestion.san); setConflictCursor(next.next_cursor);
        if (next.state === "checking") timer = setTimeout(poll, 750);
      } catch (failure) {
        if (!cancelled) setError(failure instanceof Error ? failure.message : "Could not read the compatibility check");
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [previewId, repertoireId, previewState]);

  async function save() {
    if (!preview || preview.state !== "ready") return;
    setWorking(true); setError("");
    try {
      await saveCanonicalPrefixCommand(repertoireId, preview.preview_id, revision);
      await onSaved(); onClose();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not save the prefix"); }
    finally { setWorking(false); }
  }
  async function moreConflicts() {
    if (!previewId || !conflictCursor || !preview) return;
    try {
      const next = await readJsonResponse(await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/canonical-prefix/preview/${encodeURIComponent(previewId)}?after=${encodeURIComponent(conflictCursor)}`), canonicalPrefixPreviewSchema, "canonical prefix conflicts");
      setPreview({ ...next, conflicts: [...preview.conflicts, ...next.conflicts] }); setConflictCursor(next.next_cursor);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not load conflicting lines"); }
  }
  return <div className="modal-backdrop"><div className="ui-dialog canonical-prefix-dialog" tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="canonical-prefix-title" ref={dialogRef}>
    <header className="dialog-header"><div><h2 id="canonical-prefix-title">Canonical prefix</h2><p>{repertoireName}</p></div><CloseButton onClose={onClose} /></header>
    <p>Only this exact opening move order belongs to this repertoire. Discoveries and game feedback begin after it; training still practices these moves.</p>
    <label htmlFor="canonical-prefix-moves">Assumed SAN moves</label>
    <TextInput id="canonical-prefix-moves" value={movetext} disabled={working} placeholder="e4 e5 Nf3 Nc6 Bc4" autoComplete="off" spellCheck={false}
      onChange={event => { generation.current++; setMovetext(event.target.value); setPreview(undefined); setPreviewId(undefined); setError(""); }} />
    <div className="canonical-prefix-actions"><Button disabled={working} onClick={() => void checkPrefix(movetext)}>Check prefix</Button>
      {suggestion && <Button disabled={working} onClick={() => { setMovetext(suggestion); void checkPrefix(suggestion); }}>Use shared opening</Button>}
      <Button disabled={working} onClick={() => { setMovetext(""); void checkPrefix(""); }}>Clear prefix</Button></div>
    <div className="canonical-prefix-board"><Chessboard owner={`canonical-prefix:${repertoireId}`} fen={fen} orientation={side} theme={theme} pieceSet={pieceSet} locked showHint={false} onMove={() => {}} /></div>
    {preview?.san && <p className="canonical-prefix-notation">{preview.san}</p>}
    {(previewId && !preview || preview?.state === "checking") && <p role="status">Checking saved lines and cards…</p>}
    {preview?.state === "ready" && <p role="status">{preview.moves_uci.length ? "Compatible. Discoveries start after this opening." : "Compatible. Saving will remove the opening restriction."}</p>}
    {preview?.state === "conflicts" && <div role="alert"><p>{preview.conflict_count} saved {preview.conflict_count === 1 ? "line or card conflicts" : "lines or cards conflict"}. Move or remove these before applying this prefix.</p>
      <ul>{preview.conflicts.map(conflict => <li key={conflict.item_id}><strong>{conflict.name}</strong>: {conflict.reason}</li>)}</ul>
      {conflictCursor && <Button onClick={() => void moreConflicts()}>Show more conflicts</Button>}</div>}
    {preview?.error && <p role="alert">{preview.error}</p>}
    {error && <div role="alert"><p>{error}</p><Button disabled={working} onClick={() => void load()}>Retry operation</Button></div>}
    <footer className="canonical-prefix-actions"><Button disabled={working || preview?.state !== "ready"} onClick={() => void save()}>{working ? "Working…" : "Save prefix"}</Button><Button onClick={onClose}>Cancel</Button></footer>
  </div></div>;
}
