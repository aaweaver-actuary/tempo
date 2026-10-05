import { Button } from "./buttons/BaseButton";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import { API_URL } from "../const";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import CloseButton from "./buttons/CloseButton";
import { useDialogFocus } from "../hooks/use-dialog-focus";
import { loadExplorer } from "../lib/lichess-explorer";
import { readLichessSessionToken } from "../lib/lichess-session";
import { adaptExplorerMoves } from "../domain/adapters/analysis-adapters";
import type { CandidateMove } from "../domain";
import {
  repertoireIntegritySchema,
} from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { enqueueIntegrityRepair, pendingIntegrityRepairs, subscribeIntegrityRepairs, INTEGRITY_REPAIR_CONFIRMED, type PendingIntegrityRepair } from "../lib/integrity-repair-outbox";
import { asFenString, asUciMove } from "../types";
import { MoveComparisonTable } from "./move-comparison-table";
import { reportDebugError } from "../lib/debug-reporting";

export function RepertoireIntegrityDialog({
  repertoireId,
  theme,
  pieceSet,
  onClose,
}: {
  repertoireId: string;
  theme: BoardTheme;
  pieceSet: PieceSet;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [payload, setPayload] =
    useState<ReturnType<typeof repertoireIntegritySchema.parse>>();
  const [selectedChoice, setSelectedChoice] = useState<{ identity: string; move: string }>();
  const [personal, setPersonal] = useState<
    { move_uci: string; games: number; score_percentage: number }[]
  >([]);
  const [lichess, setLichess] = useState<CandidateMove[]>([]);
  const [masters, setMasters] = useState<CandidateMove[]>([]);
  const [error, setError] = useState("");
  const [integrityLoadError, setIntegrityLoadError] = useState("");
  const [repairs, setRepairs] = useState<PendingIntegrityRepair[]>([]);
  const observedRepairs = useRef<PendingIntegrityRepair[]>([]);
  const [removedChoiceIdentities, setRemovedChoiceIdentities] = useState<Set<string>>(new Set());
  const suppressedIssues = new Set(repairs.filter(repair => repair.repertoireId === repertoireId && repair.phase !== "stale")
    .map(repair => repair.issueId));
  const remainingIssues = payload?.issues.filter(candidate => !suppressedIssues.has(candidate.id)
    && !removedChoiceIdentities.has(JSON.stringify([repertoireId, candidate.id, candidate.signature]))) ?? [];
  const issue = remainingIssues[0];
  const issueIdentity = issue ? JSON.stringify([repertoireId, issue.id, issue.signature]) : "";
  const selected = selectedChoice?.identity === issueIdentity ? selectedChoice.move : undefined;
  const setSelected = (move: string | undefined) => setSelectedChoice(move ? { identity: issueIdentity, move } : undefined);
  const loadController = useRef<AbortController | null>(null);
  const board = useMemo(() => {
    try {
      return issue?.fen ? new Chess(issue.fen) : undefined;
    } catch {
      return undefined;
    }
  }, [issue]);
  const repertoireMoves = useMemo<CandidateMove[]>(
    () => (issue?.moves ?? []).map((move) => ({ uci: asUciMove(move.uci) })),
    [issue],
  );

  const load = useCallback(async () => {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    try {
      const response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/integrity`, { signal: controller.signal });
      const next = await readJsonResponse(response, repertoireIntegritySchema, "repertoire integrity");
      if (!controller.signal.aborted) {
        setPayload(next);
        setIntegrityLoadError("");
        setRemovedChoiceIdentities(new Set());
      }
    } catch (failure) {
      if (controller.signal.aborted) return;
      reportDebugError(failure, { kind: "api", source: "repertoire-integrity", operation: "load integrity data" });
      setIntegrityLoadError(failure instanceof Error ? failure.message : "Integrity data unavailable.");
    }
  }, [repertoireId]);

  useEffect(() => {
    let active = true;
    const update = () => {
      try {
        const nextRepairs = pendingIntegrityRepairs();
        const removedChoices = observedRepairs.current.filter(repair => repair.repertoireId === repertoireId
          && repair.phase !== "stale" && !nextRepairs.some(next => next.operationId === repair.operationId));
        observedRepairs.current = nextRepairs;
        setRepairs(nextRepairs);
        if (removedChoices.length) {
          // The loaded snapshot can still contain completed choices until a refresh succeeds.
          setRemovedChoiceIdentities(current => new Set([...current, ...removedChoices.map(repair =>
            JSON.stringify([repair.repertoireId, repair.issueId, repair.signature]))]));
          void load();
        }
      }
      catch (failure) { setError(failure instanceof Error ? failure.message : "Saved choices could not be read."); }
    };
    const confirmed = () => { update(); void load(); };
    queueMicrotask(() => { if (active) { update(); void load(); } });
    const unsubscribe = subscribeIntegrityRepairs(update);
    window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
    return () => {
      active = false;
      loadController.current?.abort();
      unsubscribe();
      window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
    };
  }, [load, repertoireId]);

  useEffect(() => {
    if (!payload || !["queued", "running", "retrying"].includes(payload.scan_status)) return;
    const timer = window.setTimeout(() => void load(), 3_000);
    return () => window.clearTimeout(timer);
  }, [payload, load]);

  useEffect(() => {
    const controller = new AbortController();
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setSelectedChoice(current => current?.identity === issueIdentity ? current : undefined);
      setPersonal([]); setLichess([]); setMasters([]);
    });
    if (issue?.fen) {
      const fen = issue.fen;
      void fetch(`${API_URL}/api/games/position-summary?fen=${encodeURIComponent(fen)}`, { signal: controller.signal })
        .then(async response => {
          if (!response.ok) return;
          const games = await response.json() as { moves?: { move_uci: string; games: number; score_percentage: number }[] };
          if (!controller.signal.aborted) setPersonal(games.moves ?? []);
        }).catch(() => { /* Optional evidence remains unavailable; selection is independent. */ });
      void loadExplorer(fen, "blitz,rapid,classical", "1600,1800,2000,2200,2500", readLichessSessionToken())
        .then(explorer => {
          if (controller.signal.aborted || !explorer) return;
          setLichess(adaptExplorerMoves(fen, explorer.lichess.moves));
          setMasters(adaptExplorerMoves(fen, explorer.masters.moves));
        }).catch(() => { /* Explorer already reports source failures. */ });
    }
    return () => controller.abort();
  }, [issueIdentity, issue?.fen]);

  function resolve() {
    if (!issue || !selected) return;
    try {
      // Persist the whole choice before the changed event advances the dialog.
      enqueueIntegrityRepair({ repertoireId, issueId: issue.id, signature: issue.signature, selectedMoveUci: selected });
      setSelected(undefined); setError("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The choice could not be saved on this device.");
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="ui-dialog integrity-dialog"
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="integrity-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <CloseButton onClose={onClose} ariaLabel="Defer repertoire repair" />
        <p className="eyebrow">Repertoire repair</p>
        <h2 id="integrity-title">Choose one response per position</h2>
        {!payload && !error && !integrityLoadError && <p>Checking saved lines…</p>}
        {integrityLoadError && <p className="editor-error" role="alert">{integrityLoadError}</p>}
        {error && (
          <p className="editor-error" role="alert">
            {error}
          </p>
        )}
        {payload && !issue && <>
          <p role="status">{repairs.some(repair => repair.repertoireId === repertoireId)
            ? "All choices queued. Repairs are still awaiting confirmation."
            : payload.scan_status === "failed" ? payload.last_scan_error ?? "The integrity scan failed. Check Analysis activity."
              : payload.scan_status !== "idle" || payload.status === "unchecked" ? "Checking repertoire integrity…"
                : payload.status === "clean" ? "This repertoire is clean." : "Refresh repair evidence to review remaining issues."}</p>
          <Button onClick={onClose}>Return to study</Button>
        </>}
        {issue && (
          <>
            <p className="dialog-copy">
              {remainingIssues.length} issue{remainingIssues.length === 1 ? "" : "s"}{" "}
              remaining ·{" "}
              {issue.kind === "missing_response"
                ? "a response is missing"
                : issue.kind === "multiple_responses"
                  ? "multiple responses are saved"
                  : "a source is invalid"}
            </p>
            {issue.fen && board ? (
              <div className="integrity-layout">
                <div className="integrity-board">
                  <Chessboard
                    fen={asFenString(issue.fen)}
                    locked={false}
                    showHint={false}
                    theme={theme}
                    pieceSet={pieceSet}
                    orientation={
                      issue.trained_color === "black" ? "black" : "white"
                    }
                    onMove={(from, to) => {
                      try {
                        const chess = new Chess(issue.fen!);
                        const move = chess.move({ from, to, promotion: "q" });
                        setSelected(
                          asUciMove(
                            `${move.from}${move.to}${move.promotion ?? ""}`,
                          ),
                        );
                      } catch {
                        /* Chessground only emits legal moves. */
                      }
                    }}
                  />
                </div>
                <div className="integrity-evidence">
                  <p>
                    <strong>Saved responses</strong>:{" "}
                    {issue.moves.length
                      ? issue.moves
                          .map(
                            (move) =>
                              `${move.uci} (${move.line_count} lines, ${move.card_count} cards, ${move.review_count} reviews)`,
                          )
                          .join(" · ")
                      : "none"}
                  </p>
                  <p className="source-status">
                    Affected sources:{" "}
                    {issue.sources.length
                      ? issue.sources
                          .map((source) => `${source.type} ${source.id}`)
                          .join(" · ")
                      : "none"}
                  </p>
                  <MoveComparisonTable
                    repertoire={repertoireMoves}
                    engine={[]}
                    maia={[]}
                    lichess={lichess}
                    masters={masters}
                    turn={board.turn() === "w" ? "white" : "black"}
                    onPlay={(move) => setSelected(move)}
                    onHover={() => undefined}
                  />
                  <p className="source-status">
                    Personal games:{" "}
                    {personal.length
                      ? personal
                          .map(
                            (move) =>
                              `${move.move_uci} · ${move.games} games · ${move.score_percentage}%`,
                          )
                          .join(" · ")
                      : "unavailable"}{" "}
                    · Stockfish: unavailable · Maia: unavailable
                  </p>
                  <p>
                    Selected response:{" "}
                    <strong>{selected ?? "Choose a legal move"}</strong>
                  </p>
                  <Button
                    variant="primary"
                    className="primary-button"
                    disabled={!selected}
                    onClick={() => void resolve()}
                  >
                    Keep this response
                  </Button>
                </div>
              </div>
            ) : (
              <p role="alert">
                This source cannot be read. Edit or remove the invalid
                line/card, then resume repair.
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
