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
  integrityRepairSubmissionSchema,
  repertoireIntegritySchema,
} from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { asFenString, asUciMove } from "../types";
import { MoveComparisonTable } from "./move-comparison-table";
import { reportDebugError } from "../lib/debug-reporting";

export function RepertoireIntegrityDialog({
  repertoireId,
  theme,
  pieceSet,
  onClose,
  onClean,
}: {
  repertoireId: string;
  theme: BoardTheme;
  pieceSet: PieceSet;
  onClose: () => void;
  onClean: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [payload, setPayload] = useState<ReturnType<typeof repertoireIntegritySchema.parse>>();
  const [selected, setSelected] = useState<string>();
  const [personal, setPersonal] = useState<{ move_uci: string; games: number; score_percentage: number }[]>([]);
  const [lichess, setLichess] = useState<CandidateMove[]>([]);
  const [masters, setMasters] = useState<CandidateMove[]>([]);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [taskState, setTaskState] = useState<"queued" | "running" | "retrying" | "complete" | "failed">();

  const issue = payload?.issues[0];
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
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/repertoires/${repertoireId}/integrity`);
      const next = await readJsonResponse(response, repertoireIntegritySchema, "repertoire integrity");
      setPayload(next);
      setSelected(undefined);
      const nextIssue = next.issues[0];
      if (nextIssue?.fen) {
        const [gamesResponse, explorer] = await Promise.all([
          fetch(`${API_URL}/api/games/position-summary?fen=${encodeURIComponent(nextIssue.fen)}`),
          loadExplorer(nextIssue.fen, "blitz,rapid,classical", "1600,1800,2000,2200,2500", readLichessSessionToken()),
        ]);
        if (gamesResponse.ok) {
          const games = (await gamesResponse.json()) as { moves?: { move_uci: string; games: number; score_percentage: number }[] };
          setPersonal(games.moves ?? []);
        } else setPersonal([]);
        if (explorer) {
          setLichess(adaptExplorerMoves(nextIssue.fen, explorer.lichess.moves));
          setMasters(adaptExplorerMoves(nextIssue.fen, explorer.masters.moves));
        } else {
          setLichess([]);
          setMasters([]);
        }
      }
    } catch (failure) {
      reportDebugError(failure, {
        kind: "api",
        source: "repertoire-integrity",
        operation: "load integrity data",
        endpoint: `${API_URL}/api/repertoires/${repertoireId}/integrity`,
      });
      setError(failure instanceof Error ? failure.message : "Integrity data unavailable.");
    }
  }, [repertoireId]);

  useEffect(() => {
    if (!payload || !["queued", "running", "retrying"].includes(payload.scan_status)) return;
    const timer = window.setTimeout(() => void load(), 500);
    return () => window.clearTimeout(timer);
  }, [payload, load]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
    };
  }, [load]);

  async function resolve() {
    if (!issue || !selected) return;
    setWorking(true);
    setTaskState(undefined);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/repertoires/${repertoireId}/integrity/issues/${issue.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ signature: issue.signature, selected_move_uci: selected }),
      });
      const submission = await readJsonResponse(response, integrityRepairSubmissionSchema, "integrity repair");
      setTaskState(submission.state === "leased" ? "running" : submission.state);
      for (let poll = 0; poll < 300; poll += 1) {
        await new Promise((resolveDelay) => window.setTimeout(resolveDelay, 500));
        const [taskResponse, integrityResponse] = await Promise.all([
          fetch(`${API_URL}/api/system/tasks`),
          fetch(`${API_URL}/api/repertoires/${repertoireId}/integrity`),
        ]);
        if (!taskResponse.ok || !integrityResponse.ok) continue;
        const tasks = await taskResponse.json() as { tasks?: { id: string; state: string; last_error?: string | null }[] };
        const task = tasks.tasks?.find((candidate) => candidate.id === submission.task_id);
        if (task)
          setTaskState(task.state === "leased" ? "running" : task.state as "queued" | "retrying" | "complete" | "failed");
        if (task?.state === "failed")
          throw new Error(task.last_error || "The guided repair failed.");
        const next = repertoireIntegritySchema.parse(await integrityResponse.json());
        if (!next.issues.some((candidate) => candidate.id === submission.issue_id) && next.scan_status === "idle") {
          if (next.status === "clean") onClean();
          else {
            setPayload(next);
            setSelected(undefined);
          }
          setTaskState(undefined);
          return;
        }
      }
      throw new Error("The repair is still processing. It is safe to close this dialog and return later.");
    } catch (failure) {
      reportDebugError(failure, {
        kind: "api",
        source: "repertoire-integrity",
        operation: "resolve integrity issue",
        endpoint: `${API_URL}/api/repertoires/${repertoireId}/integrity/issues/${issue.id}/resolve`,
        method: "POST",
      });
      setError(failure instanceof Error ? failure.message : "The repair could not be saved.");
      setTaskState("failed");
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="integrity-dialog" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="integrity-title" onMouseDown={(event) => event.stopPropagation()}>
        <CloseButton onClose={onClose} ariaLabel="Defer repertoire repair" />
        <p className="eyebrow">Repertoire repair</p>
        <h2 id="integrity-title">Choose one response per position</h2>
        {!payload && !error && <p>Checking saved lines…</p>}
        {error && <p className="editor-error" role="alert">{error}</p>}
        {payload && !issue && <p role="status">This repertoire is clean.</p>}
        {issue && (
          <>
            <p className="dialog-copy">{payload.issue_count} issue{payload.issue_count === 1 ? "" : "s"} remaining · {issue.kind === "missing_response" ? "a response is missing" : issue.kind === "multiple_responses" ? "multiple responses are saved" : "a source is invalid"}</p>
            {issue.fen && board ? (
              <div className="integrity-layout">
                <div className="integrity-board">
                  <Chessboard fen={asFenString(issue.fen)} locked={false} showHint={false} theme={theme} pieceSet={pieceSet} orientation={issue.trained_color === "black" ? "black" : "white"} onMove={(from, to) => {
                    try {
                      const chess = new Chess(issue.fen!);
                      const move = chess.move({ from, to, promotion: "q" });
                      setSelected(asUciMove(`${move.from}${move.to}${move.promotion ?? ""}`));
                    } catch { /* Chessground only emits legal moves. */ }
                  }} />
                </div>
                <div className="integrity-evidence">
                  <p><strong>Saved responses</strong>: {issue.moves.length ? issue.moves.map((move) => `${move.uci} (${move.line_count} lines, ${move.card_count} cards, ${move.review_count} reviews)`).join(" · ") : "none"}</p>
                  <p className="source-status">Affected sources: {issue.sources.length ? issue.sources.map((source) => `${source.type} ${source.id}`).join(" · ") : "none"}</p>
                  <MoveComparisonTable repertoire={repertoireMoves} engine={[]} maia={[]} lichess={lichess} masters={masters} turn={board.turn() === "w" ? "white" : "black"} onPlay={(move) => setSelected(move)} onHover={() => undefined} />
                  <p className="source-status">Personal games: {personal.length ? personal.map((move) => `${move.move_uci} · ${move.games} games · ${move.score_percentage}%`).join(" · ") : "unavailable"} · Stockfish: unavailable · Maia: unavailable</p>
                  <p>Selected response: <strong>{selected ?? "Choose a legal move"}</strong></p>
                  {taskState && <p className="source-status" role="status">Repair task: {taskState}.</p>}
                  <button className="primary-button" disabled={!selected || working} onClick={() => void resolve()}>{working ? "Saving…" : "Keep this response"}</button>
                </div>
              </div>
            ) : (
              <p role="alert">This source cannot be read. Edit or remove the invalid line/card, then resume repair.</p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
